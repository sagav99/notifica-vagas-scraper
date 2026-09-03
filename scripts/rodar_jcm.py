#!/usr/bin/env python3
"""Entrypoint do cron pra fonte JCM Concursos: descobre processos com
inscrição aberta em `/index/abertos/` (sem lista curada de município,
igual IMESO/IMAM), acha a versão mais recente do edital entre as
publicações e usa Gemini pra ler o PDF (salário não tem campo estruturado
em HTML — só cargo/quantidade têm, ver `fontes/jcm.py`, mas
`vagas.quantidade` está fora de escopo por decisão do usuário).

**Usar sempre `concursosjcm.com.br`** — `jcmconcursos.com.br` (domínio
citado originalmente) dá erro de TLS consistente, ver `fontes/jcm.py`.

Uso: python scripts/rodar_jcm.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import jcm
from notifica_vagas_scraper.processamento_pdf_gemini import processar_pdf_e_gravar_vagas

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "JCM Concursos"

#: só MG foi visto na amostra até agora, mas a JCM roda na mesma
#: plataforma ProSeleta da ACCESS, que atende várias UFs (RJ/SP/MG/GO/SC)
#: — filtra por segurança, mesmo padrão do rodar_access.py (achado de
#: code review, 2026-09-02: risco não coberto aqui antes).
UFS_DO_PROJETO = {"MG", "SP"}


def processar_processo(conn, fonte_id: str, item: jcm.ItemListagem) -> int:
    codigo_ibge = ibge.buscar_codigo_ibge(item.municipio, item.uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{item.municipio}/{item.uf}' não encontrado no IBGE, pulando")
        return 0

    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    documentos = jcm.listar_documentos(resposta.text)
    edital = jcm.escolher_edital(documentos)
    if edital is None:
        print(f"  aviso: '{item.tipo_processo} {item.numero_edital}' sem documentos listados")
        return 0

    return processar_pdf_e_gravar_vagas(
        conn,
        fonte_id=fonte_id,
        codigo_ibge=codigo_ibge,
        municipio_nome=item.municipio,
        uf=item.uf,
        url_pdf=edital.url_pdf,
        data_publicacao=edital.data,
        orgao_fallback=f"{item.orgao} de {item.municipio}/{item.uf}",
        numero_edital_fallback=item.numero_edital,
        id_prefix="jcm",
        processo_id=item.processo_id,
        resumo_prefixo=f"{item.tipo_processo} nº {item.numero_edital}",
        user_agent=USER_AGENT,
    )


def main() -> None:
    resposta = requests.get(f"{jcm.BASE_URL}/index/abertos/", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    todos = jcm.listar_processos_abertos(resposta.text)
    itens = [i for i in todos if i.uf in UFS_DO_PROJETO]

    print(f"{len(itens)} processo(s) com inscrição aberta em MG/SP (de {len(todos)} encontrados).")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=jcm.BASE_URL, tipo="oficial", uf="MG")
        conn.commit()

        total_geral = 0
        for item in itens:
            print(f"Processando {item.municipio}/{item.uf} — {item.tipo_processo} {item.numero_edital}...")
            try:
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.tipo_processo} {item.numero_edital}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_jcm.py"):
        main()
