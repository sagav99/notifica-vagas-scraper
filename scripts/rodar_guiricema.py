#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Prefeitura de Guiricema/MG
(`guiricema.mg.gov.br/legislacao-tipo/processos-seletivos/` — WordPress +
Elementor/JetEngine, sem banca organizadora terceirizada, PSS direto).

Diferente de `rodar_pmjm_mg.py`/`rodar_bocaiuva_mg.py` (CMS "XFind.inc",
que exige 1 request de detalhe por processo pra achar o anexo certo),
aqui a listagem já entrega o link do PDF do edital direto — não existe
página de detalhe separada. Ver docstring de `fontes/guiricema_mg.py` pro
detalhe completo do achado (campo `cargo` da listagem é só triagem, a
lista definitiva de cargos sempre vem do Gemini lendo o PDF inteiro).

Uso: python scripts/rodar_guiricema.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import guiricema_mg
from notifica_vagas_scraper.processamento_pdf_gemini import processar_pdf_e_gravar_vagas

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Prefeitura de Guiricema/MG"
ORGAO_PADRAO = "Prefeitura Municipal de Guiricema"


def processar_processo(conn, fonte_id: str, codigo_ibge: int, item: guiricema_mg.ItemListagem) -> int:
    return processar_pdf_e_gravar_vagas(
        conn,
        fonte_id=fonte_id,
        codigo_ibge=codigo_ibge,
        municipio_nome=guiricema_mg.MUNICIPIO,
        uf=guiricema_mg.UF,
        url_pdf=item.url_pdf,
        data_publicacao=item.data_publicacao,
        orgao_fallback=ORGAO_PADRAO,
        numero_edital_fallback=None,
        id_prefix=guiricema_mg.ID_PREFIX,
        processo_id=item.processo_id,
        resumo_prefixo=item.titulo,
        user_agent=USER_AGENT,
    )


def main() -> None:
    resposta = requests.get(guiricema_mg.URL_LISTAGEM, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()

    itens = guiricema_mg.listar_processos(resposta.text)
    processos_saude = guiricema_mg.filtrar_processos_saude_abertura(itens)
    print(f"{len(processos_saude)} edital(is) de abertura com indício de cargo de saúde de {len(itens)} listado(s) no total.")

    conn = db.conectar()
    try:
        codigo_ibge = db.buscar_codigo_ibge_local(conn, guiricema_mg.MUNICIPIO, guiricema_mg.UF) or ibge.buscar_codigo_ibge(
            guiricema_mg.MUNICIPIO, guiricema_mg.UF
        )
        if codigo_ibge is None:
            print(f"aviso: município '{guiricema_mg.MUNICIPIO}/{guiricema_mg.UF}' não encontrado no IBGE, abortando")
            return

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=guiricema_mg.URL_LISTAGEM, tipo="oficial", uf=guiricema_mg.UF)
        conn.commit()

        ids_processados = guiricema_mg.extrair_ids_processados(db.listar_identificadores_processados(conn, fonte_id))
        novos = [item for item in processos_saude if item.processo_id not in ids_processados]
        print(f"{len(novos)} edital(is) novo(s) (não visto(s) em execução anterior).")

        total_geral = 0
        for item in novos:
            print(f"Processando {item.titulo} ({item.url_pdf})...")
            try:
                # savepoint por item: erro num processo não deixa a
                # transação inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, codigo_ibge, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_guiricema.py"):
        main()
