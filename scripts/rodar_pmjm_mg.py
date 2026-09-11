#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Prefeitura de João Monlevade/MG
(`pmjm.mg.gov.br/concursos-publicos` — site próprio, sem banca
organizadora terceirizada).

A listagem cobre o histórico INTEIRO de processos desde 2011 (sem filtro
nativo de "só o vigente", diferente de IMAM/JCM que já expõem status
"Novo"/"Inscrições Abertas") — por isso este script filtra por 2 critérios
antes de gastar rede/Gemini com um item: (1) título com indício de cargo
de saúde (`pmjm_mg.eh_cargo_saude`, deliberadamente permissivo — ver
docstring de `fontes/pmjm_mg.py`) e (2) `<id>` do processo ainda não visto
em nenhuma evidência já gravada pra esta fonte
(`db.listar_identificadores_processados` + `pmjm_mg.extrair_ids_processados`).

Cargo/vagas/salário/requisitos vêm 100% de `gemini_pdf.extrair_vagas_de_pdf`
via `processamento_pdf_gemini.processar_pdf_e_gravar_vagas` (mesmo miolo
compartilhado de ACCESS/IMAM/JCM) — o PDF do Edital 12/2026 real (fixture)
é escaneado/sem camada de texto, mas isso não exige nenhum código
diferente: a API do Gemini pra documento já lê PDF escaneado visualmente
por padrão (ver docstring de `fontes/pmjm_mg.py`).

Uso: python scripts/rodar_pmjm_mg.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import pmjm_mg
from notifica_vagas_scraper.processamento_pdf_gemini import processar_pdf_e_gravar_vagas

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Prefeitura de João Monlevade/MG"
ORGAO_PADRAO = "Secretaria Municipal de Saúde de João Monlevade"


def processar_processo(conn, fonte_id: str, codigo_ibge: int, item: pmjm_mg.ItemListagem) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    detalhe = pmjm_mg.extrair_detalhe(resposta.text)
    anexo = pmjm_mg.escolher_pdf_edital(detalhe.anexos)
    if anexo is None:
        print(f"  aviso: '{item.titulo}' sem PDF de edital de abertura reconhecido (só resultado/homologação?), pulando")
        return 0

    numero_edital = detalhe.numero_edital or item.numero_edital
    data_publicacao = detalhe.data_publicacao or item.data_publicacao
    titulo = detalhe.titulo or item.titulo

    return processar_pdf_e_gravar_vagas(
        conn,
        fonte_id=fonte_id,
        codigo_ibge=codigo_ibge,
        municipio_nome=pmjm_mg.MUNICIPIO,
        uf=pmjm_mg.UF,
        url_pdf=anexo.url,
        data_publicacao=data_publicacao,
        orgao_fallback=ORGAO_PADRAO,
        numero_edital_fallback=numero_edital,
        id_prefix=pmjm_mg.ID_PREFIX,
        processo_id=item.processo_id,
        resumo_prefixo=titulo,
        user_agent=USER_AGENT,
    )


def main() -> None:
    resposta = requests.get(pmjm_mg.URL_LISTAGEM, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()

    itens = pmjm_mg.listar_processos(resposta.text)
    processos_saude = pmjm_mg.filtrar_processos_saude(itens)
    print(f"{len(processos_saude)} processo(s) com indício de cargo de saúde de {len(itens)} listado(s) no total.")

    conn = db.conectar()
    try:
        codigo_ibge = db.buscar_codigo_ibge_local(conn, pmjm_mg.MUNICIPIO, pmjm_mg.UF) or ibge.buscar_codigo_ibge(
            pmjm_mg.MUNICIPIO, pmjm_mg.UF
        )
        if codigo_ibge is None:
            print(f"aviso: município '{pmjm_mg.MUNICIPIO}/{pmjm_mg.UF}' não encontrado no IBGE, abortando")
            return

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=pmjm_mg.URL_LISTAGEM, tipo="oficial", uf=pmjm_mg.UF)
        conn.commit()

        ids_processados = pmjm_mg.extrair_ids_processados(db.listar_identificadores_processados(conn, fonte_id))
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
    with db.rastrear_execucao("rodar_pmjm_mg.py"):
        main()
