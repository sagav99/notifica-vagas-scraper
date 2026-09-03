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

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import imam
from notifica_vagas_scraper.processamento_pdf_gemini import processar_pdf_e_gravar_vagas

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

    return processar_pdf_e_gravar_vagas(
        conn,
        fonte_id=fonte_id,
        codigo_ibge=codigo_ibge,
        municipio_nome=municipio_nome,
        uf="MG",
        url_pdf=edital.url_pdf,
        data_publicacao=edital.data.date() if edital.data else None,
        orgao_fallback=item.entidade,
        numero_edital_fallback=item.titulo_processo,
        id_prefix="imam",
        processo_id=item.processo_id,
        resumo_prefixo=item.titulo_processo,
        user_agent=USER_AGENT,
    )


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
