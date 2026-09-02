#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Ache Concursos: percorre as listagens de
MG e SP, filtra pra quem ainda está com inscrição aberta, casa o título
contra os municípios de MG/SP já cadastrados (mesmo mecanismo da FGV —
não tem UF/município estruturado em HTML) e só pros que baterem segue a
cadeia artigo -> sub-página "Edital do Concurso" -> PDF (self-hosted no
próprio domínio do Ache Concursos, mesmo pra edital municipal — ver
`fontes/ache_concursos.py`) pra extrair cargo/salário via Gemini.

Uso: python scripts/rodar_ache_concursos.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import ache_concursos as ache
from notifica_vagas_scraper.fontes import fgv

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Ache Concursos"
LISTAGENS = {"MG": "/concursos-minas-gerais", "SP": "/concursos-sao-paulo"}


def processar_item(conn, item: ache.ItemListagem, municipio: str, uf: str) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    url_pagina_edital = ache.extrair_url_pagina_edital(resposta.text)
    if url_pagina_edital is None:
        print(f"  aviso: sem seção 'Anexos' identificada em {item.url}")
        return 0

    pagina_edital = requests.get(url_pagina_edital, headers={"User-Agent": USER_AGENT}, timeout=20)
    pagina_edital.raise_for_status()

    pdf_url = ache.extrair_url_pdf(pagina_edital.text)
    if pdf_url is None:
        print(f"  aviso: sem PDF identificado em {url_pagina_edital}")
        return 0

    pdf_resposta = requests.get(pdf_url, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra {pdf_url}")
        return 0

    codigo_ibge = ibge.buscar_codigo_ibge(municipio, uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{municipio}/{uf}' não encontrado no IBGE, pulando")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio, uf=uf)
    # tipo="oficial", não "indice": diferente da PCI (bloqueada, só gera
    # CSV pra revisão manual), aqui o pipeline completo funciona —
    # cargo/salário reais extraídos via Gemini do PDF espelhado, mesmo
    # padrão de FGV/Actcon/WordPress/IMAM/JCM/ACCESS.
    fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=ache.BASE_URL, tipo="oficial", uf=uf)

    slug_item = item.url.rstrip("/").rsplit("/", 1)[-1]
    orgao = extraido.get("orgao") or item.titulo
    numero_edital = extraido.get("numero_edital")

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=f"ache-{slug_item}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            numero_edital=numero_edital,
            data_publicacao=None,
            inscricoes_inicio=None,
            inscricoes_fim=item.inscricoes_fim,
            status="aberta",
            resumo=f"{item.titulo} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=pdf_url,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    conn = db.conectar()
    try:
        municipios = db.listar_nomes_municipios(conn, ufs=["MG", "SP"])
        print(f"{len(municipios)} município(s) de MG/SP carregados pra match.")

        hoje = date.today()
        total_geral = 0
        for uf, caminho in LISTAGENS.items():
            resposta = requests.get(f"{ache.BASE_URL}{caminho}", headers={"User-Agent": USER_AGENT}, timeout=30)
            resposta.raise_for_status()
            itens = ache.listar_concursos(resposta.text)
            abertos = [i for i in itens if i.inscricoes_fim is None or i.inscricoes_fim >= hoje]
            print(f"{uf}: {len(abertos)} item(ns) com inscrição aberta de {len(itens)} listados.")

            for item in abertos:
                # título costuma ter "Município-UF" com hífen (achado real:
                # "São João Del-Rei-MG" não bate contra "São João del Rei"
                # cadastrado com espaço) — troca hífen por espaço antes de
                # comparar, sem alterar fgv.encontrar_municipio (compartilhado
                # com a FGV, que não tem esse problema nos próprios títulos).
                match = fgv.encontrar_municipio(item.titulo.replace("-", " "), municipios)
                if match is None:
                    continue
                municipio, municipio_uf = match
                print(f"Achado: '{item.titulo}' -> {municipio}/{municipio_uf} ({item.url})")
                try:
                    # savepoint por item: erro num item não deixa a transação
                    # inteira do lote em estado abortado pros próximos.
                    with conn.transaction():
                        total_geral += processar_item(conn, item, municipio, municipio_uf)
                except Exception as exc:  # nunca deixar 1 item derrubar o lote inteiro
                    print(f"  ERRO processando '{item.titulo}': {exc}")

        conn.commit()
        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_ache_concursos.py"):
        main()
