#!/usr/bin/env python3
"""Entrypoint do cron: busca automatizada de matérias de concurso público/
processo seletivo no Diário Oficial dos Municípios do Estado de São Paulo
(SIGPub, `/apm/`), pelos 7 municípios reais confirmados (ver
`fontes/apm_sp.py`) — mesmo mecanismo já validado no DOM/AMM-MG de MG
(token CSRF de sessão nova por entidade, busca avançada, resolve
`/load/<codigo>` -> matéria real).

Sem processamento em lote/canário periódico feito o AMM-MG: só 7
entidades no total, volume baixo demais pra bater no limiar de
throttling observado lá (~45 entidades numa mesma execução). Canário
ainda roda 1x no início, pra abortar cedo se o mecanismo genuinamente
não estiver respondendo.

Uso: python scripts/rodar_apm_sp_busca.py
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db
from notifica_vagas_scraper.fontes import apm_sp, dom_amm_mg, sigpub_busca

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Diário Oficial dos Municípios do Estado de São Paulo (APM)"
FONTE_URL = "https://www.diariomunicipal.com.br/apm/"
TERMOS_BUSCA = ("concurso público", "processo seletivo")
JANELA_DIAS = 90
INTERVALO_ENTRE_BUSCAS_S = 3.0

# Canário: Barra do Turvo tinha convocação de concurso real confirmada
# em 2026-09-01 (Portaria 215/2026, dentro do edital 01/2024) — mesmo
# papel do canário do AMM-MG, ver docstring de
# rodar_dom_amm_mg_busca.verificar_canario. CAVEAT: só vale enquanto
# essa portaria estiver dentro de JANELA_DIAS a partir de hoje.
CANARIO_ENTIDADE_ID = "17868"
CANARIO_TERMO = "concurso público"


def processar_materia(conn, fonte_id: int, codigo_ibge: int, url_materia: str) -> int:
    resposta = requests.get(url_materia, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    vagas_extraidas = dom_amm_mg.parsear_materia(resposta.text, url=url_materia)
    if not vagas_extraidas:
        return 0

    total = 0
    for vaga in vagas_extraidas:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=dom_amm_mg.identificador_externo(vaga),
            orgao=vaga.orgao,
            cargo=vaga.cargo,
            salario=vaga.salario,
            salario_tipo=None,
            tipo_oportunidade=None,
            numero_edital=vaga.numero_edital,
            data_publicacao=vaga.data_publicacao,
            inscricoes_inicio=vaga.inscricoes_inicio,
            inscricoes_fim=vaga.inscricoes_fim,
            status="aberta",
            resumo=f"{vaga.tipo_processo} nº {vaga.numero_edital} — {vaga.cargo} ({vaga.vagas_qtd or '?'} vaga(s), {vaga.carga_horaria or 'carga horária não informada'}).",
            url_evidencia=vaga.url,
            tipo_documento="pagina_html",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {vaga.cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def processar_entidade(conn, fonte_id: int, entidade: dom_amm_mg.EntidadeAmmMg) -> int:
    session = requests.Session()
    token = sigpub_busca.obter_token(session, apm_sp.CAMINHO_PESQUISAR)
    if not token:
        print("    aviso: não obteve token pra esta entidade, pulando.")
        return 0

    hoje = date.today()
    total = 0
    codigos_ja_processados: set[str] = set()

    for termo in TERMOS_BUSCA:
        time.sleep(INTERVALO_ENTRE_BUSCAS_S)
        html = sigpub_busca.buscar(
            session,
            caminho_pesquisar=apm_sp.CAMINHO_PESQUISAR,
            token=token,
            entidade_id=entidade.entidade_id,
            termo=termo,
            data_inicio=hoje - timedelta(days=JANELA_DIAS),
            data_fim=hoje,
        )
        resultados = sigpub_busca.parsear_resultados(html)
        for resultado in resultados:
            if resultado.codigo in codigos_ja_processados:
                continue
            codigos_ja_processados.add(resultado.codigo)
            try:
                url_materia = sigpub_busca.resolver_url_materia(session, resultado.url_load)
                total += processar_materia(conn, fonte_id, entidade.codigo_ibge, url_materia)
            except requests.exceptions.RequestException as exc:
                print(f"    aviso: falha ao processar '{resultado.titulo[:60]}': {exc}")

    return total


def verificar_canario() -> bool:
    """Ver docstring de rodar_dom_amm_mg_busca.verificar_canario — mesmo
    papel, aqui só rodado 1x no início (volume baixo demais pra precisar
    de checagem periódica no meio do lote)."""
    hoje = date.today()
    sessao = requests.Session()
    token = sigpub_busca.obter_token(sessao, apm_sp.CAMINHO_PESQUISAR)
    if not token:
        return False
    html = sigpub_busca.buscar(
        sessao,
        caminho_pesquisar=apm_sp.CAMINHO_PESQUISAR,
        token=token,
        entidade_id=CANARIO_ENTIDADE_ID,
        termo=CANARIO_TERMO,
        data_inicio=hoje - timedelta(days=JANELA_DIAS),
        data_fim=hoje,
    )
    return len(sigpub_busca.parsear_resultados(html)) > 0


def main() -> None:
    conn = db.conectar()
    try:
        entidades = apm_sp.listar_entidades_apm_sp()
        print(f"{len(entidades)} entidade(s) APM/SP confirmada(s).")

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=FONTE_URL, tipo="oficial", uf="SP")
        conn.commit()

        if not verificar_canario():
            raise RuntimeError(
                "Canário inicial falhou — mecanismo de busca do APM/SP não "
                "está respondendo com resultado real conhecido. Abortando "
                "antes de processar qualquer entidade."
            )

        total_geral = 0
        for entidade in entidades:
            print(f"Processando {entidade.nome}/{entidade.uf} (entidade {entidade.entidade_id})...")
            try:
                with conn.transaction():
                    total_geral += processar_entidade(conn, fonte_id, entidade)
                conn.commit()
            except Exception as exc:
                print(f"  ERRO processando {entidade.nome}/{entidade.uf}: {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_apm_sp_busca.py"):
        main()
