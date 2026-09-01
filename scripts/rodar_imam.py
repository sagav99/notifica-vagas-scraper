#!/usr/bin/env python3
"""Entrypoint do cron pra fonte IMAM (Instituto Mineiro de Assessoria
Municipal): descobre processos "Novo"/"Inscrições Abertas" em
`/sitenoticia/processo_seletivo.aspx` (sem lista curada de município,
igual IMESO — a busca é automática desde o início), acha o edital de
abertura na grade de documentos de cada processo e usa Gemini pra ler o
PDF (cargo/salário/vagas não têm campo estruturado em HTML, igual
Actcon/FGV/WordPress).

Entidade que não é prefeitura/câmara (ex: consórcio intermunicipal) é
pulada — não mapeia 1:1 pra um município.

Uso: python scripts/rodar_imam.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import imam

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "IMAM (Instituto Mineiro de Assessoria Municipal)"
STATUS_DE_INTERESSE = {"Novo", "Inscrições Abertas"}


def processar_processo(conn, fonte_id: str, item: imam.ItemListagem) -> int:
    municipio_nome = imam.extrair_municipio(item.entidade)
    if municipio_nome is None:
        print(f"  aviso: entidade '{item.entidade}' não é prefeitura/câmara, pulando")
        return 0

    codigo_ibge = ibge.buscar_codigo_ibge(municipio_nome, "MG")
    if codigo_ibge is None:
        print(f"  aviso: município '{municipio_nome}/MG' não encontrado no IBGE, pulando")
        return 0

    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    documentos = imam.listar_documentos(resposta.text, item.url)
    edital = imam.escolher_edital(documentos)
    if edital is None:
        print(f"  aviso: '{item.titulo_processo}' sem documentos listados")
        return 0

    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{item.titulo_processo}' ({edital.url_pdf})")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio_nome, uf="MG")
    orgao = extraido.get("orgao") or item.entidade
    numero_edital = extraido.get("numero_edital") or item.titulo_processo

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
            identificador_externo=f"imam-{item.processo_id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            numero_edital=numero_edital,
            data_publicacao=edital.data.date() if edital.data else None,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=f"{item.titulo_processo} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=edital.url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(
        f"{imam.BASE_URL}/processo_seletivo.aspx", headers={"User-Agent": USER_AGENT}, timeout=20
    )
    resposta.raise_for_status()
    itens = imam.listar_processos(resposta.text)
    interessantes = [i for i in itens if i.status in STATUS_DE_INTERESSE]

    print(f"{len(interessantes)} processo(s) novo(s)/com inscrição aberta de {len(itens)} total.")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=imam.BASE_URL, tipo="oficial", uf="MG")
        conn.commit()

        total_geral = 0
        for item in interessantes:
            print(f"Processando {item.entidade} — {item.titulo_processo} ({item.status})...")
            try:
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo_processo}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_imam.py"):
        main()
