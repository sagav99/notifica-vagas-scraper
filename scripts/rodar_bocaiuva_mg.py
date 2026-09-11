#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Prefeitura de Bocaiúva/MG
(`bocaiuva.mg.gov.br/concursos-publicos` — site próprio, mesmo CMS
"XFind.inc" de João Monlevade/MG, sem banca organizadora terceirizada).

Estrutura idêntica a `scripts/rodar_pmjm_mg.py` (mesmo miolo de
"listar → filtrar por saúde → pular processo já visto → baixar detalhe/
PDF → Gemini → gravar", ver docstring de lá pro detalhe de cada etapa) —
só troca o módulo de fonte (`fontes.bocaiuva_mg` em vez de
`fontes.pmjm_mg`). Particularidade confirmada na investigação
(`fontes/bocaiuva_mg.py`): os 2 processos de saúde/médico das fixtures
só tinham anexo de convocação recente, sem evidência de edital de
abertura com inscrição ainda aberta — este script continua descobrindo
qualquer edital de abertura NOVO que apareça (monitoramento contínuo
normal), sem gerar vaga a partir de convocação de quem já passou
(`escolher_pdf_edital` nunca escolhe anexo de convocação/resultado/
homologação como fonte de cargo).

Uso: python scripts/rodar_bocaiuva_mg.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import bocaiuva_mg
from notifica_vagas_scraper.processamento_pdf_gemini import processar_pdf_e_gravar_vagas

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Prefeitura de Bocaiúva/MG"
ORGAO_PADRAO = "Secretaria Municipal de Saúde de Bocaiúva"


def processar_processo(conn, fonte_id: str, codigo_ibge: int, item: bocaiuva_mg.ItemListagem) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    detalhe = bocaiuva_mg.extrair_detalhe(resposta.text)
    anexo = bocaiuva_mg.escolher_pdf_edital(detalhe.anexos)
    if anexo is None:
        print(f"  aviso: '{item.titulo}' sem PDF de edital de abertura reconhecido (só convocação/resultado/homologação?), pulando")
        return 0

    numero_edital = detalhe.numero_edital or item.numero_edital
    data_publicacao = detalhe.data_publicacao or item.data_publicacao
    titulo = detalhe.titulo or item.titulo

    return processar_pdf_e_gravar_vagas(
        conn,
        fonte_id=fonte_id,
        codigo_ibge=codigo_ibge,
        municipio_nome=bocaiuva_mg.MUNICIPIO,
        uf=bocaiuva_mg.UF,
        url_pdf=anexo.url,
        data_publicacao=data_publicacao,
        orgao_fallback=ORGAO_PADRAO,
        numero_edital_fallback=numero_edital,
        id_prefix=bocaiuva_mg.ID_PREFIX,
        processo_id=item.processo_id,
        resumo_prefixo=titulo,
        user_agent=USER_AGENT,
    )


def main() -> None:
    resposta = requests.get(bocaiuva_mg.URL_LISTAGEM, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()

    itens = bocaiuva_mg.listar_processos(resposta.text)
    processos_saude = bocaiuva_mg.filtrar_processos_saude(itens)
    print(f"{len(processos_saude)} processo(s) com indício de cargo de saúde de {len(itens)} listado(s) no total.")

    conn = db.conectar()
    try:
        codigo_ibge = db.buscar_codigo_ibge_local(conn, bocaiuva_mg.MUNICIPIO, bocaiuva_mg.UF) or ibge.buscar_codigo_ibge(
            bocaiuva_mg.MUNICIPIO, bocaiuva_mg.UF
        )
        if codigo_ibge is None:
            print(f"aviso: município '{bocaiuva_mg.MUNICIPIO}/{bocaiuva_mg.UF}' não encontrado no IBGE, abortando")
            return

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=bocaiuva_mg.URL_LISTAGEM, tipo="oficial", uf=bocaiuva_mg.UF)
        conn.commit()

        ids_processados = bocaiuva_mg.extrair_ids_processados(db.listar_identificadores_processados(conn, fonte_id))
        novos = [item for item in processos_saude if item.processo_id not in ids_processados]
        print(f"{len(novos)} processo(s) novo(s) (não visto(s) em execução anterior).")

        total_geral = 0
        for item in novos:
            print(f"Processando {item.titulo} ({item.url})...")
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
    with db.rastrear_execucao("rodar_bocaiuva_mg.py"):
        main()
