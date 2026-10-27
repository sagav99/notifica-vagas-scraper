#!/usr/bin/env python3
"""Entrypoint do cron para o feed Estratégia MED (concursos médicos), segunda fonte
curada para o Sul (PR/SC/RS) ao lado de O Concurso Médico: percorre as primeiras
páginas do feed, fica com os itens do Sul ainda abertos/futuros, descarta o que o
banco já tem (mesmo município + mesmo fim de inscrição) e, para o que falta, baixa
o PDF do edital (CDN deles) e deixa o Gemini extrair cargo/salário. Depois é o
pipeline normal (revisão + completude).

Trava de cota: no máximo `MAX_NOVOS_POR_EXECUCAO` editais por rodada (o resto fica
para a próxima). `--dry-run` só lista, sem banco/Gemini.

Uso: python scripts/rodar_estrategia_med.py [--dry-run] [--max N] [--paginas N]
Requer DATABASE_URL (e GEMINI_API_KEY fora do dry-run).
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import requests

from notifica_vagas_scraper import db, gemini_pdf, gemini_util
from notifica_vagas_scraper.fontes import estrategia_med as em
from notifica_vagas_scraper.fontes import fgv
from notifica_vagas_scraper.ufs import UFS_SUL

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Estratégia MED (índice)"
MAX_NOVOS_POR_EXECUCAO = 5
PAGINAS_PADRAO = 3
PAUSA_S = 2.0


def _slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _get(url: str, **kw) -> requests.Response:
    time.sleep(PAUSA_S)  # o site limita rajada
    resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=40, **kw)
    resposta.raise_for_status()
    return resposta


def coletar_candidatos(paginas: int, municipios_por_uf: dict[str, list[tuple[str, str]]]):
    """(item, artigo, nome_municipio, uf) dos itens do Sul abertos/futuros com edital em PDF."""
    hoje = date.today()
    vistos: set[str] = set()
    saida = []
    for pagina in range(1, paginas + 1):
        url = em.FEED_URL if pagina == 1 else f"{em.FEED_URL}?paged={pagina}"
        try:
            itens = em.listar_feed(_get(url).text)
        except requests.RequestException as exc:
            print(f"  ERRO lendo feed página {pagina}: {exc}")
            break
        for item in itens:
            if item.url in vistos:
                continue
            vistos.add(item.url)
            uf_titulo = em.uf_do_titulo(item.titulo)
            if uf_titulo is not None and uf_titulo not in UFS_SUL:
                continue
            if "resid" in item.titulo.lower():  # residência é programa de formação (fora do escopo)
                continue
            try:
                artigo = em.parsear_artigo(_get(item.url).text)
            except requests.RequestException as exc:
                print(f"  ERRO lendo artigo '{item.titulo}': {exc}")
                continue
            uf = uf_titulo or artigo.uf
            if uf not in UFS_SUL:
                continue
            if artigo.inscricoes_fim is None or artigo.inscricoes_fim < hoje or not artigo.edital_url:
                continue
            match = fgv.encontrar_municipio(item.titulo, municipios_por_uf[uf])
            if match is None:
                print(f"  sem município no cadastro: {item.titulo} ({uf})")
                continue
            saida.append((item, artigo, match[0], uf))
    return saida


def processar(conn, item, artigo, municipio: str, uf: str, codigo_ibge: int, ocm_mod) -> int:
    try:
        resposta = requests.get(artigo.edital_url, headers={"User-Agent": USER_AGENT}, timeout=60)
        resposta.raise_for_status()
    except requests.RequestException as exc:
        raise ocm_mod.DownloadFalhou(str(exc)) from exc
    extraido = gemini_pdf.extrair_vagas_de_pdf(resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra {artigo.edital_url}")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio, uf=uf)
    fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=em.SITE_URL, tipo="oficial", uf=uf)
    orgao = extraido.get("orgao") or item.titulo
    numero_edital = extraido.get("numero_edital") or item.titulo
    hoje = date.today()
    status = "futura" if artigo.inscricoes_inicio and artigo.inscricoes_inicio > hoje else "aberta"

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=f"em-{_slug(item.url)}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=extraido.get("tipo_oportunidade"),
            numero_edital=numero_edital,
            data_publicacao=None,
            inscricoes_inicio=artigo.inscricoes_inicio,
            inscricoes_fim=artigo.inscricoes_fim,
            status=status,
            resumo=f"{item.titulo} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=artigo.edital_url,
            tipo_documento="pdf",
            texto_extraido=None,
            **gemini_util.campos_estruturados_extras(extraido, vaga),
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def main(dry_run: bool, maximo: int, paginas: int) -> None:
    import rodar_oconcursomedico as ocm_mod  # reaproveita ja_temos/_chave/DownloadFalhou

    if dry_run:
        # sem banco: casa o município só pelo nome (lista IBGE) para conferência
        from notifica_vagas_scraper import ibge

        municipios_por_uf = {uf: [(m["nome"], uf) for m in ibge.listar_municipios(uf)] for uf in UFS_SUL}
        for item, artigo, mun, uf in coletar_candidatos(paginas, municipios_por_uf):
            print(f"  ABERTO: {item.titulo} | {mun}/{uf} | {artigo.inscricoes_inicio}..{artigo.inscricoes_fim} | {artigo.edital_url}")
        return

    conn = db.conectar()
    try:
        completos = db.listar_municipios_com_codigo(conn, ufs=list(UFS_SUL))
        municipios_por_uf = {uf: [(nome, u) for _c, nome, u in completos if u == uf] for uf in UFS_SUL}
        codigo_por_nome_uf = {(nome, uf): codigo for codigo, nome, uf in completos}
        ja_processados = db.listar_identificadores_por_fonte_nome(conn, FONTE_NOME)

        candidatos = coletar_candidatos(paginas, municipios_por_uf)
        print(f"{len(candidatos)} item(ns) do Sul abertos/futuros no feed.")
        faltando = []
        for item, artigo, mun, uf in candidatos:
            codigo = codigo_por_nome_uf[(mun, uf)]
            if ocm_mod.ja_temos(conn, codigo, artigo.inscricoes_fim) or db.item_ja_processado(f"em-{_slug(item.url)}-", ja_processados):
                continue
            faltando.append((item, artigo, mun, uf, codigo))
        print(f"{len(faltando)} edital(is) que o banco não tem.")

        total = gastos = 0
        for item, artigo, mun, uf, codigo in faltando:
            if gastos >= maximo:
                break
            print(f"Processando: {item.titulo} ({artigo.edital_url})")
            try:
                with conn.transaction():
                    total += processar(conn, item, artigo, mun, uf, codigo, ocm_mod)
                conn.commit()
                gastos += 1
            except ocm_mod.DownloadFalhou as exc:
                print(f"  ERRO (download, sem Gemini): {exc}")
            except Exception as exc:  # item ruim não derruba o lote
                gastos += 1
                print(f"  ERRO processando '{item.titulo}': {exc}")
        print(f"\nOk. {total} vaga(s) gravada(s); {max(0, len(faltando) - gastos)} edital(is) ficam pra próxima.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    maximo = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else MAX_NOVOS_POR_EXECUCAO
    paginas = int(sys.argv[sys.argv.index("--paginas") + 1]) if "--paginas" in sys.argv else PAGINAS_PADRAO
    if dry:
        main(dry, maximo, paginas)
    else:
        with db.rastrear_execucao("rodar_estrategia_med.py"):
            main(dry, maximo, paginas)
