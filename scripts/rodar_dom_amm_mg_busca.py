#!/usr/bin/env python3
"""Entrypoint do cron: busca automatizada de matérias de concurso público/
processo seletivo no DOM/AMM-MG, por entidade (161 prefeituras de MG, ver
`dom_amm_mg.listar_entidades_amm_mg`), substituindo a antiga lista curada
manual (`fontes_conhecidas.MATERIAS_DOM_AMM_MG`, 1 município só).

Mecanismo (ver `fontes/sigpub_busca.py` e
docs/fixtures/dom_amm_mg/busca_resultado_*.html no repo principal):
1 GET pra pegar o token CSRF de sessão nova, depois 1 GET de busca
avançada por termo — **token/sessão novos a cada entidade** (achado
real: reaproveitar 1 sessão pra todas as 161 entidades passa a devolver
0 resultado silenciosamente depois de alguns minutos de execução real).
Sem Gemini nesta etapa — dom_amm_mg.parsear_materia já é extração
determinística (regex/HTML), o Gemini só entra na revisão automática
(revisar_vagas.py), igual toda outra fonte.

**Processa só um LOTE das 161 entidades por execução** (ver BATCH_SIZE),
não todas de uma vez — achado real rodando o lote inteiro contra
produção (2026-09-01, confirmado tanto de máquina local quanto do
runner do GitHub Actions): o servidor começa a throttlar silenciosamente
depois de ~45 entidades numa mesma execução, independente de IP/sessão.
O lote do dia é escolhido deterministicamente por `date.today()` (dia do
ano módulo número de lotes) — sem precisar de cursor persistente em
banco — então rodando o cron a cada 3 dias, a cobertura completa das 161
entidades se repete a cada ~5 execuções (~15 dias).

Uso: python scripts/rodar_dom_amm_mg_busca.py
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
from notifica_vagas_scraper.fontes import dom_amm_mg, sigpub_busca

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Diário Oficial dos Municípios Mineiros (AMM-MG)"
FONTE_URL = "https://www.diariomunicipal.com.br/amm-mg/"
TERMOS_BUSCA = ("concurso público", "processo seletivo")
JANELA_DIAS = 90
INTERVALO_ENTRE_BUSCAS_S = 3.0  # 1.5s disparou throttling silencioso do servidor sob carga sustentada (ver verificar_canario)
BATCH_SIZE = 35  # margem abaixo do limiar observado (~45 entidades) antes do throttling

# Canário: entidade com matéria real confirmada manualmente (Pedra Dourada,
# ver docs/fixtures/dom_amm_mg/ no repo principal), usado pra detectar
# throttling silencioso do servidor (ver verificar_canario). CAVEAT: só
# vale enquanto essa matéria (2026-07-08) estiver dentro de JANELA_DIAS —
# válido até ~2026-10-08; depois disso, trocar por outro caso confirmado
# se a checagem começar a falhar por motivo legítimo, não throttling.
CANARIO_ENTIDADE_ID = "273955"
CANARIO_TERMO = "processo seletivo"
CANARIO_A_CADA_N_ENTIDADES = 15


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
    """Sessão e token novos por entidade — achado real rodando o backfill
    completo em produção (2026-09-01): um token/sessão único reaproveitado
    pra todas as 161 entidades funcionou nas primeiras (smoke test com 3)
    mas, no lote inteiro (~15-20min de execução real), passou a devolver
    0 resultado silenciosamente a partir de um certo ponto — mesmo pra
    Pedra Dourada, que tinha resultado real confirmado minutos antes.
    Sessão/token têm validade por tempo (não só por sessão em si, ver
    docstring de sigpub_busca.obter_token) — pedir de novo por entidade é
    mais requisições, mas elimina essa classe de falha silenciosa."""
    session = requests.Session()
    token = sigpub_busca.obter_token(session, dom_amm_mg.CAMINHO_PESQUISAR)
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
            caminho_pesquisar=dom_amm_mg.CAMINHO_PESQUISAR,
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
    """Confirma que o pipeline INTEIRO ainda funciona de verdade —
    busca -> resolve /load/<codigo> -> parseia a matéria e acha vaga
    real — checando `CANARIO_ENTIDADE_ID` (resultado real conhecido) com
    sessão/token totalmente novos.

    Dois achados reais rodando o backfill completo em produção
    (2026-09-01), cada um exigiu ampliar esta checagem:
    (1) sob carga sustentada (~100 entidades numa única execução), a
    BUSCA passa a devolver 0 resultado silenciosamente — sem erro HTTP,
    sem exceção — e volta a funcionar normalmente assim que a carga do
    processo para;
    (2) checar só a busca não bastou: numa 2ª rodada, a busca continuou
    achando resultado normalmente, mas o passo seguinte
    (`/amm-mg/load/<codigo>`, que deveria REDIRECIONAR pra
    `/amm-mg/materia/<codigo>/<hash>`) parou de redirecionar — passou a
    devolver um HTML genérico de "diário do dia" ("NENHUMA MATÉRIA
    ENCONTRADA PARA ESTA DATA") em vez do conteúdo real, fazendo
    `parsear_materia` achar 0 vaga em TODA entidade processada depois
    disso, com o lote inteiro "terminando com sucesso" sem inserir nada.
    Uma checagem que só confere a contagem da busca não pega esse caso —
    por isso agora resolve e parseia de verdade, igual o pipeline real.

    Ambos os sintomas romperam sem erro HTTP visível e se recuperaram
    sozinhos depois de um tempo sem carga — sinal de throttling/anti-bot
    do servidor por IP/sessão, não token expirado (testado: mesmo
    token/sessão aguentou 16min de uso espaçado sem falhar) nem bloqueio
    permanente (recuperou sozinho depois de pausar)."""
    hoje = date.today()
    sessao = requests.Session()
    token = sigpub_busca.obter_token(sessao, dom_amm_mg.CAMINHO_PESQUISAR)
    if not token:
        return False
    html = sigpub_busca.buscar(
        sessao,
        caminho_pesquisar=dom_amm_mg.CAMINHO_PESQUISAR,
        token=token,
        entidade_id=CANARIO_ENTIDADE_ID,
        termo=CANARIO_TERMO,
        data_inicio=hoje - timedelta(days=JANELA_DIAS),
        data_fim=hoje,
    )
    resultados = sigpub_busca.parsear_resultados(html)
    if not resultados:
        return False

    try:
        url_materia = sigpub_busca.resolver_url_materia(sessao, resultados[0].url_load)
        resposta = requests.get(url_materia, headers={"User-Agent": USER_AGENT}, timeout=20)
        resposta.raise_for_status()
    except requests.exceptions.RequestException:
        return False
    return len(dom_amm_mg.parsear_materia(resposta.text, url=url_materia)) > 0


def selecionar_lote_do_dia(
    entidades: list[dom_amm_mg.EntidadeAmmMg], *, hoje: date | None = None
) -> list[dom_amm_mg.EntidadeAmmMg]:
    """Escolhe deterministicamente qual fatia das entidades processar
    hoje (dia do ano módulo número de lotes) — sem precisar de cursor
    persistente em banco. Mesma entrada + mesma data sempre devolve o
    mesmo lote, então rodar de novo no mesmo dia (ex: workflow_dispatch
    manual depois de uma falha) repete o lote, não pula pro próximo."""
    hoje = hoje or date.today()
    total_lotes = -(-len(entidades) // BATCH_SIZE)  # ceil division
    indice_lote = hoje.timetuple().tm_yday % total_lotes
    inicio = indice_lote * BATCH_SIZE
    return entidades[inicio : inicio + BATCH_SIZE]


def main() -> None:
    conn = db.conectar()
    try:
        todas_entidades = dom_amm_mg.listar_entidades_amm_mg()
        entidades = selecionar_lote_do_dia(todas_entidades)
        print(
            f"{len(todas_entidades)} entidade(s) AMM-MG confirmada(s) — "
            f"processando lote de {len(entidades)} hoje."
        )

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=FONTE_URL, tipo="oficial", uf="MG")
        conn.commit()

        if not verificar_canario():
            raise RuntimeError(
                "Canário inicial falhou — mecanismo de busca do AMM-MG não "
                "está respondendo com resultado real conhecido. Abortando "
                "antes de processar qualquer entidade."
            )

        total_geral = 0
        for indice, entidade in enumerate(entidades, start=1):
            print(f"Processando {entidade.nome}/{entidade.uf} (entidade {entidade.entidade_id})...")
            try:
                # savepoint por entidade: erro numa não derruba o lote inteiro.
                with conn.transaction():
                    total_geral += processar_entidade(conn, fonte_id, entidade)
                conn.commit()  # por entidade — preserva o que já foi achado se abortar no meio
            except Exception as exc:  # nunca deixar 1 entidade derrubar o lote inteiro
                print(f"  ERRO processando {entidade.nome}/{entidade.uf}: {exc}")

            if indice % CANARIO_A_CADA_N_ENTIDADES == 0 and indice < len(entidades):
                if not verificar_canario():
                    raise RuntimeError(
                        f"Canário falhou depois de {indice}/{len(entidades)} entidades "
                        "— mecanismo de busca parou de responder com resultado real "
                        "(provável throttling do servidor). Abortando o resto do lote; "
                        f"{total_geral} vaga(s) já processada(s) até aqui ficam preservadas."
                    )

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_dom_amm_mg_busca.py"):
        main()
