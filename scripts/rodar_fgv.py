#!/usr/bin/env python3
"""Entrypoint do cron pra fonte FGV Conhecimento: percorre a listagem de
concursos "em andamento" (paginada), casa o título contra os municípios de
MG/SP já cadastrados, e só pros que baterem baixa o PDF do edital principal
e usa Gemini pra extrair cargo/salário/vagas (dado que não existe em HTML,
diferente da IMESO — ver `notifica_vagas_scraper.gemini_pdf`).

Uso: python scripts/rodar_fgv.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import fgv

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "FGV Conhecimento"
MAX_PAGINAS = 20


def listar_todos_concursos() -> list[fgv.ItemConcurso]:
    todos: list[fgv.ItemConcurso] = []
    for pagina in range(MAX_PAGINAS):
        resposta = requests.get(
            f"{fgv.BASE_URL}/concursos",
            params={"page": pagina},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resposta.raise_for_status()
        itens = fgv.listar_concursos(resposta.text)
        if not itens:
            break
        todos.extend(itens)
    return todos


def _parsear_data_iso(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def processar_concurso(conn, item: fgv.ItemConcurso, municipio: str, uf: str) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    pdf_url = fgv.encontrar_pdf_edital_principal(resposta.text)
    if pdf_url is None:
        print(f"  aviso: sem PDF de edital principal identificado em {item.url}")
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
    fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=fgv.BASE_URL, tipo="oficial", uf=uf)

    slug_concurso = item.url.rstrip("/").rsplit("/", 1)[-1]
    data_publicacao = _parsear_data_iso(extraido.get("data_publicacao"))
    inscricoes_inicio = _parsear_data_iso(extraido.get("inscricoes_inicio"))
    inscricoes_fim = _parsear_data_iso(extraido.get("inscricoes_fim"))
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
            identificador_externo=f"fgv-{slug_concurso}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=f"Edital nº {numero_edital} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=pdf_url,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"  {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    conn = db.conectar()
    try:
        municipios = db.listar_nomes_municipios(conn, ufs=["MG", "SP"])
        print(f"{len(municipios)} município(s) de MG/SP carregados pra match.")

        concursos = listar_todos_concursos()
        print(f"{len(concursos)} concurso(s) em andamento na FGV.")

        total_geral = 0
        for item in concursos:
            match = fgv.encontrar_municipio(item.titulo, municipios)
            if match is None:
                continue
            municipio, uf = match
            print(f"Achado: '{item.titulo}' -> {municipio}/{uf} ({item.url})")
            try:
                # savepoint por concurso: erro de banco num item não deixa a
                # transação inteira do lote em estado abortado pros próximos.
                with conn.transaction():
                    total_geral += processar_concurso(conn, item, municipio, uf)
            except Exception as exc:  # nunca deixar 1 concurso derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo}': {exc}")

        conn.commit()
        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_fgv.py"):
        main()
