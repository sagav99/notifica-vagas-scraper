#!/usr/bin/env python3
"""Descoberta leve de vagas de enfermagem e engenharia para posts de Instagram.

1 consulta Serper por área por execução; cada link novo vira no máx. 1
chamada Gemini (teto 80/dia por área). Grava em `vagas_light`
(migration 112). Ver `notifica_vagas_scraper.light`.

Uso: ``python scripts/rodar_descoberta_light.py [--area enfermagem|engenheiro] [--dry-run]``
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import requests

from notifica_vagas_scraper import db, deteccao_bloqueio, gemini_pdf, light
from notifica_vagas_scraper.fontes import google_search
from notifica_vagas_scraper.quota_gemini import CotaGeminiEsgotadaError
from rodar_descoberta_google_search import (
    USER_AGENT, baixar_pdf, buscar_texto_pagina, normalizar_dominio,
)

JANELA_BUSCA = "qdr:d7"


def buscar_links(query: str, api_key: str) -> list[google_search.ItemBusca]:
    resposta = requests.post(
        google_search.BASE_URL,
        json={"q": query, "num": 10, "hl": "pt", "gl": "br", "tbs": JANELA_BUSCA},
        headers={"User-Agent": USER_AGENT, "X-API-KEY": api_key, "Content-Type": "application/json"},
        timeout=20,
    )
    resposta.raise_for_status()
    return google_search.listar_itens(resposta.json())


def url_ja_vista(conn, url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("select 1 from public.vagas_light_urls where url = %s", (url,))
        return cur.fetchone() is not None


def marcar_url(conn, url: str, area: str, resultado: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "insert into public.vagas_light_urls (url, area, resultado) values (%s, %s, %s) on conflict do nothing",
            (url, area, resultado),
        )


def gravar(conn, linhas: list[dict]) -> int:
    novas = 0
    with conn.cursor() as cur:
        for l in linhas:
            cur.execute(
                """
                insert into public.vagas_light (area, cargo, orgao, municipio, uf, numero_vagas, salario,
                    inscricoes_inicio, inscricoes_fim, numero_edital, banca, url, titulo)
                values (%(area)s, %(cargo)s, %(orgao)s, %(municipio)s, %(uf)s, %(numero_vagas)s, %(salario)s,
                    %(inscricoes_inicio)s, %(inscricoes_fim)s, %(numero_edital)s, %(banca)s, %(url)s, %(titulo)s)
                on conflict (url, cargo) do nothing
                """,
                l,
            )
            novas += cur.rowcount
    return novas


def processar_area(conn, area: str, serper_key: str, gemini_key: str, hoje, dry_run: bool) -> None:
    # origem + teto próprios: ver quota_gemini.origem_atual/_teto_origem
    os.environ["GEMINI_ORIGEM"] = f"light-{area}"
    os.environ["GEMINI_TETO_ORIGEM_DIA"] = str(light.TETO_GEMINI_POR_AREA_DIA)
    itens = buscar_links(light.AREAS[area]["query"], serper_key)
    print(f"[{area}] {len(itens)} resultado(s) da Serper (1 crédito).")
    gravadas = gemini = 0
    for item in itens:
        if url_ja_vista(conn, item.link):
            continue
        if dry_run:
            print(f"  (dry-run) leria: {item.titulo[:80]} | {item.link}")
            continue
        try:
            if item.link.lower().split("?")[0].endswith(".pdf"):
                pdf = baixar_pdf(item.link)
                if not pdf:
                    continue  # falha de rede: tenta de novo outro dia
                gemini += 1
                extraido = gemini_pdf.extrair_vagas_de_pdf(pdf, api_key=gemini_key)
            else:
                texto = buscar_texto_pagina(item.link)
                if not texto:
                    continue
                gemini += 1
                extraido = light.extrair_de_texto(item.titulo, texto, area, api_key=gemini_key)
        except CotaGeminiEsgotadaError as exc:
            print(f"  [{area}] parando: {exc}")
            break
        except deteccao_bloqueio.FetchSuspeitoError as exc:
            marcar_url(conn, item.link, area, "bloqueado")
            print(f"  bloqueio em {normalizar_dominio(item.link)}: {exc}")
            conn.commit()
            continue
        except Exception as exc:  # um item ruim não derruba a área; sem marcar → tenta outro dia
            print(f"  erro em {item.link[:80]}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        linhas = light.montar_linhas(extraido, area, hoje=hoje, url=item.link, titulo=item.titulo)
        gravadas += gravar(conn, linhas)
        marcar_url(conn, item.link, area, "com_vaga" if linhas else "sem_vaga")
        conn.commit()
    print(f"[{area}] {gravadas} vaga(s) nova(s); {gemini} chamada(s) Gemini nesta execução.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", choices=sorted(light.AREAS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    serper_key, gemini_key = os.environ.get("SERPER_API_KEY"), os.environ.get("GEMINI_API_KEY")
    if not serper_key or (not gemini_key and not args.dry_run):
        sys.exit("faltam SERPER_API_KEY / GEMINI_API_KEY")
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    conn = db.conectar()
    try:
        for area in [args.area] if args.area else sorted(light.AREAS):
            processar_area(conn, area, serper_key, gemini_key or "", hoje, args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
