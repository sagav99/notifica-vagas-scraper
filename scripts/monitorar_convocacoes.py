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
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db
from notifica_vagas_scraper.fontes import convocacoes as conv
from notifica_vagas_scraper.fontes import diarios_sul

PAUSA_S = 0.8


def pausa() -> None:
    time.sleep(PAUSA_S)


def carregar_acompanhamentos(conn) -> list[conv.Acompanhamento]:
    with conn.cursor() as cur:
        cur.execute(
            "select id::text, usuario_id::text, nome_edital, uf, municipio, concurso, cargo, classificacao "
            "from public.convocacao_acompanhamentos where ativo order by criado_em"
        )
        return [conv.Acompanhamento(*linha) for linha in cur.fetchall()]


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
                "insert into public.convocacao_avisos (acompanhamento_id, publicacao_chave, tipo, trecho, concurso_detectado, cargo_detectado) "
                "values (%s, %s, %s, %s, %s, %s) on conflict (acompanhamento_id, publicacao_chave) do nothing",
                (a.acompanhamento_id, a.chave, a.tipo, a.trecho[:900], a.concurso, a.cargo),
            )
            novos += cur.rowcount
    conn.commit()
    return novos


def coletar(acompanhamentos, dias: int, ufs: set[str], vistos: set[str], ao_ler=lambda chave: None) -> list[conv.Achado]:
    hoje = diarios_sul.datetime_hoje_brasilia().date()
    lista_dias = [hoje - timedelta(days=n) for n in range(dias, -1, -1)]
    sessao = requests.Session()
    achados: list[conv.Achado] = []

    for uf in ("MG", "SP", "PR", "RS"):
        if uf not in ufs:
            continue
        novos, lidas = conv.processar_sigpub(sessao, uf, lista_dias, acompanhamentos, vistos, pausa=pausa, ao_ler=ao_ler)
        print(f"{uf}: {lidas} publicação(ões) nova(s) lida(s), {len(novos)} achado(s).")
        achados += novos

    if "SC" in ufs and any(a.uf in (None, "SC") for a in acompanhamentos):
        zips = diarios_sul.listar_zips_sc(dias, hoje=hoje, sessao=sessao)
        achados_sc = 0
        for z in zips:
            resposta = sessao.get(z["url"], headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=180)
            resposta.raise_for_status()
            novos = conv.achados_sc(diarios_sul.ler_zip_sc(resposta.content), acompanhamentos)
            achados_sc += len(novos)
            achados += novos
        print(f"SC: {len(zips)} ZIP(s) do CIGA, {achados_sc} achado(s).")

    if "SP" in ufs:  # complemento: SP é o estado menos coberto pelo agregador
        for ac in acompanhamentos:
            if ac.uf == "SP" and ac.municipio:
                try:
                    achados += conv.noticias_convocacao(sessao, ac, hoje - timedelta(days=dias))
                except requests.RequestException as erro:
                    print(f"  notícias {ac.municipio}: {erro}")
                pausa()
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
            if not acompanhamentos:
                execucao.detalhe = "sem acompanhamentos"
                return
            vistos = carregar_vistos(conn)
            lidos: list[str] = []
            achados = coletar(acompanhamentos, args.dias, ufs, vistos, ao_ler=lidos.append)
            novos = 0
            if not args.dry_run:
                novos = gravar(conn, achados)
                gravar_vistos(conn, lidos)  # só depois de gravar os avisos: falha no meio = relê no dia seguinte
            execucao.detalhe = f"acompanhamentos={len(acompanhamentos)} lidas={len(lidos)} achados={len(achados)} avisos_novos={novos}"
            print(execucao.detalhe)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
