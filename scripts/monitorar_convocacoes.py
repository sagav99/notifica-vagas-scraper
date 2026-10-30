#!/usr/bin/env python3
"""Monitor de convocações (plano Max): lê as publicações novas de convocação/nomeação/chamada dos
diários oficiais de MG/SP/PR/SC/RS e cruza com o NOME dos assinantes (em qualquer concurso) e, se
cadastrado, com o município/concurso que acompanham. Grava avisos pendentes (`convocacao_avisos`);
o cron do site (`/api/cron/avisos-convocacao`) envia o e-mail e a cópia para o WhatsApp manual.

Custo: 0 Serper, 0 Gemini. ~1 requisição por publicação nova (cache em `convocacao_vistos`), independente
do número de assinantes. Detalhe: docs/convocacoes_monitoramento.md (repo principal).

Uso: python scripts/monitorar_convocacoes.py [--dias 3] [--uf MG ...] [--dry-run] [--exemplo-json arquivo]
Requer DATABASE_URL (exceto com --exemplo-json, que usa acompanhamentos de arquivo e não toca o banco).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db
from notifica_vagas_scraper.fontes import convocacoes as conv
from notifica_vagas_scraper.fontes import diarios_sul

PAUSA_S = 0.5
#: Teto de consultas Serper por execução (só município cadastrado sem diário agregador, em dia par).
LIMITE_SERPER_POR_EXECUCAO = 6


def pausa() -> None:
    time.sleep(PAUSA_S)


def carregar_acompanhamentos(conn) -> list[conv.Acompanhamento]:
    with conn.cursor() as cur:
        cur.execute(
            "select id::text, usuario_id::text, nome_edital, uf, municipio, concurso, cargo, classificacao, url_convocacoes "
            "from public.convocacao_acompanhamentos where ativo order by criado_em"
        )
        return [conv.Acompanhamento(*linha) for linha in cur.fetchall()]


def carregar_paginas(conn, urls: list[str]) -> dict[str, set[str]]:
    if not urls:
        return {}
    with conn.cursor() as cur:
        cur.execute("select url, sentencas from public.convocacao_paginas where url = any(%s)", (urls,))
        return {url: set(sent) for url, sent in cur.fetchall()}


def gravar_paginas(conn, memoria: dict[str, set[str]]) -> None:
    with conn.cursor() as cur:
        for url, sentencas in memoria.items():
            cur.execute(
                "insert into public.convocacao_paginas (url, sentencas) values (%s, %s::jsonb) "
                "on conflict (url) do update set sentencas = excluded.sentencas, visto_em = now()",
                (url, json.dumps(sorted(sentencas))),
            )
    conn.commit()


def gravar_cobertura(conn, cobertura: dict[str, dict[str, str]]) -> None:
    """`cobertura[uf][municipio_norm] = nome do município` (fonte = agregador do estado)."""
    fontes = {"MG": "amm-mg", "SP": "apm", "PR": "amp", "RS": "famurs", "SC": "ciga-sc"}
    with conn.cursor() as cur:
        for uf, municipios in cobertura.items():
            for norm, nome in municipios.items():
                cur.execute(
                    "insert into public.convocacao_cobertura (uf, municipio_norm, municipio, fonte) values (%s, %s, %s, %s) "
                    "on conflict (uf, municipio_norm) do update set municipio = excluded.municipio, atualizado_em = now()",
                    (uf, norm, nome, fontes[uf]),
                )
    conn.commit()


def carregar_vistos(conn, dias: int = 30) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("select chave from public.convocacao_vistos where visto_em > now() - make_interval(days => %s)", (dias,))
        return {linha[0] for linha in cur.fetchall()}


def gravar_vistos(conn, chaves: list[str]) -> None:
    with conn.cursor() as cur:
        for chave in chaves:
            cur.execute("insert into public.convocacao_vistos (chave) values (%s) on conflict do nothing", (chave,))
    conn.commit()


def gravar(conn, achados: list[conv.Achado]) -> int:
    novos = 0
    with conn.cursor() as cur:
        for a in sorted(achados, key=lambda x: 0 if x.tipo == "nome" else 1):
            cur.execute(
                "insert into public.convocacao_publicacoes (chave, fonte, uf, municipio, titulo, data_publicacao, url) "
                "values (%s, %s, %s, %s, %s, %s, %s) on conflict (chave) do nothing",
                (a.chave, a.fonte, a.uf, a.municipio, a.titulo[:300], a.data_publicacao, a.url),
            )
            cur.execute(
                "insert into public.convocacao_avisos (acompanhamento_id, publicacao_chave, tipo, trecho, concurso_detectado, cargo_detectado, prazo_detectado, confianca) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (acompanhamento_id, publicacao_chave) do nothing",
                (a.acompanhamento_id, a.chave, a.tipo, a.trecho[:900], a.concurso, a.cargo, a.prazo, a.confianca),
            )
            novos += cur.rowcount
    conn.commit()
    return novos


def coletar(acompanhamentos, dias: int, ufs: set[str], vistos: set[str], ao_ler=lambda chave: None, conn=None, estado: dict | None = None) -> list[conv.Achado]:
    estado = estado if estado is not None else {}
    estado.setdefault("cobertura", {})
    hoje = diarios_sul.datetime_hoje_brasilia().date()
    lista_dias = [hoje - timedelta(days=n) for n in range(dias, -1, -1)]
    sessao = requests.Session()
    achados: list[conv.Achado] = []

    for uf in ("MG", "SP", "PR", "RS"):
        if uf not in ufs:
            continue
        try:
            estado["cobertura"][uf] = {diarios_sul.normalizar(n): n for n in conv.municipios_cobertos_sigpub(sessao, uf)}
        except requests.RequestException as erro:
            print(f"  cobertura {uf}: {erro}")
        novos, lidas, cartoes = conv.processar_sigpub(sessao, uf, lista_dias, acompanhamentos, vistos, pausa=pausa, ao_ler=ao_ler)
        print(f"{uf}: {cartoes} publicação(ões) na varredura, {lidas} nova(s) lida(s), {len(novos)} achado(s).")
        # Dia útil sem NENHUMA publicação de convocação num diário desse porte = diário quebrou/mudou: falhar alto.
        if cartoes == 0 and any(d.weekday() < 5 for d in lista_dias[-2:]) and uf in ("MG", "PR", "RS"):
            raise RuntimeError(f"{uf}: varredura do diário devolveu 0 publicações em dia útil (HTML do SIGPub mudou ou diário fora do ar?)")
        achados += novos

    if "SC" in ufs:  # sempre: a cobertura de SC vem dos ZIPs do CIGA
        zips = diarios_sul.listar_zips_sc(dias, hoje=hoje, sessao=sessao)
        achados_sc = 0
        for z in zips:
            resposta = sessao.get(z["url"], headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=180)
            resposta.raise_for_status()
            pubs_sc = diarios_sul.ler_zip_sc(resposta.content)
            for pub in pubs_sc:
                if pub.municipio:
                    estado["cobertura"].setdefault("SC", {})[diarios_sul.normalizar(pub.municipio)] = pub.municipio
            novos = conv.achados_sc(pubs_sc, acompanhamentos)
            achados_sc += len(novos)
            achados += novos
        print(f"SC: {len(zips)} ZIP(s) do CIGA, {achados_sc} achado(s).")
        if not zips and any(d.weekday() < 5 for d in lista_dias[-2:]):
            raise RuntimeError("SC: nenhum ZIP do CIGA na janela em dia útil (CKAN mudou ou fora do ar?)")

    # Município cadastrado que o agregador do estado NÃO cobre: notícias (grátis) todo dia; Google (Serper) só em dia par, com teto.
    descobertos = [
        a for a in acompanhamentos
        if a.uf and a.municipio and a.uf in estado["cobertura"] and diarios_sul.normalizar(a.municipio) not in estado["cobertura"][a.uf]
    ]
    estado["descobertos"] = len(descobertos)
    chave_serper = os.environ.get("SERPER_API_KEY")
    gastos = 0
    for ac in descobertos:
        try:
            achados += conv.noticias_convocacao(sessao, ac, hoje - timedelta(days=dias))
        except requests.RequestException as erro:
            print(f"  notícias {ac.municipio}: {erro}")
        pausa()
        if chave_serper and conn is not None and hoje.toordinal() % 2 == 0 and gastos < LIMITE_SERPER_POR_EXECUCAO:
            consulta = conv.consulta_google(ac)
            resposta = sessao.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": chave_serper, "Content-Type": "application/json"},
                json={"q": consulta, "num": 10, "hl": "pt", "gl": "br", "tbs": "qdr:d3"},
                timeout=30,
            )
            gastos += 1
            if resposta.ok:
                novos = conv.achados_google(ac, resposta.json())
                achados += novos
                db.registrar_consulta_serper(conn, origem="convocacao", consulta=consulta, resultados=len(novos))
    estado["serper"] = gastos

    # Página de convocações informada pelo assinante (qualquer município, inclusive os não cobertos).
    com_pagina = [a for a in acompanhamentos if a.url_convocacoes and conv.url_de_pagina_valida(a.url_convocacoes)]
    memoria = carregar_paginas(conn, sorted({a.url_convocacoes for a in com_pagina})) if conn is not None else {}
    novas_memorias: dict[str, set[str]] = {}
    for url in sorted({a.url_convocacoes for a in com_pagina}):
        try:
            resposta = sessao.get(url, headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=30)
            resposta.raise_for_status()
            texto = diarios_sul.texto_limpo(resposta.text[:2_000_000])
        except requests.RequestException as erro:
            print(f"  página {url[:60]}: {erro}")
            continue
        for ac in [a for a in com_pagina if a.url_convocacoes == url]:
            achados += conv.achados_da_pagina(ac, url, texto, memoria.get(url))
        novas_memorias[url] = memoria.get(url, set()) | conv.hashes_das_sentencas(texto)
        pausa()
    estado["paginas"] = novas_memorias
    return achados


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=2)
    ap.add_argument("--uf", nargs="*", default=["MG", "SP", "PR", "SC", "RS"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--exemplo-json", help="acompanhamentos de teste (lista de objetos) em vez do banco")
    args = ap.parse_args()
    ufs = set(args.uf)

    if args.exemplo_json:
        acompanhamentos = [conv.Acompanhamento(**item) for item in json.loads(Path(args.exemplo_json).read_text())]
        achados = coletar(acompanhamentos, args.dias, ufs, set())
        for a in achados:
            print(f"[{a.tipo}] {a.uf} {a.municipio} | {a.titulo[:80]} | {a.concurso} | {a.cargo} | {a.data_publicacao} | {a.url}")
        print(f"{len(achados)} achado(s).")
        return

    with db.rastrear_execucao("monitorar_convocacoes.py") as execucao:
        conn = db.conectar()
        try:
            acompanhamentos = carregar_acompanhamentos(conn)
            print(f"{len(acompanhamentos)} acompanhamento(s) ativo(s).")
            vistos = carregar_vistos(conn)
            lidos: list[str] = []
            estado: dict = {}
            achados = coletar(acompanhamentos, args.dias, ufs, vistos, ao_ler=lidos.append, conn=conn, estado=estado)
            novos = 0
            if not args.dry_run:
                novos = gravar(conn, achados)
                gravar_vistos(conn, lidos)  # só depois de gravar os avisos: falha no meio = relê no dia seguinte
                gravar_paginas(conn, estado.get("paginas", {}))
                gravar_cobertura(conn, estado.get("cobertura", {}))
            execucao.detalhe = (
                f"acompanhamentos={len(acompanhamentos)} lidas={len(lidos)} achados={len(achados)} avisos_novos={novos} "
                f"descobertos={estado.get('descobertos', 0)} serper={estado.get('serper', 0)}"
            )
            print(execucao.detalhe)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
