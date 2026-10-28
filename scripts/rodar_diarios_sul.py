#!/usr/bin/env python3
"""Monitor dos diários oficiais municipais do Sul (SC: CIGA/DOMSC; PR: AMP; RS: FAMURS).

Fluxo: publicações dos últimos `--dias` dias -> filtro determinístico de
abertura com cargo médico (`diarios_sul.eh_candidata`, sem IA) -> município pelo
cadastro local -> texto recortado -> Gemini (1 chamada por candidata, teto
`--max-gemini`) -> só cargo médico com inscrição não vencida vira vaga.
Custo: 0 Serper; Gemini só nos poucos candidatos (~3/dia em SC).

Uso: python scripts/rodar_diarios_sul.py --uf SC|PR|RS [--dias 3] [--max-gemini 12] [--dry-run]
Requer DATABASE_URL e GEMINI_API_KEY (exceto --dry-run, que só lista candidatas).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_texto, gemini_util
from notifica_vagas_scraper.classificar_medico import eh_cargo_medico
from notifica_vagas_scraper.fontes import diarios_sul

PAUSA_S = 1.5
LIMITE_TEXTO_GEMINI = 24000
PAGINAS_MAX_SIGPUB = 3

FONTES = {
    "SC": ("Diário Oficial dos Municípios de SC (CIGA/DOMSC)", "https://dados.ciga.sc.gov.br/", "sc"),
    "PR": ("Diário Oficial dos Municípios do Paraná (AMP)", "https://www.diariomunicipal.com.br/amp/", "pr"),
    "RS": ("Diário Oficial dos Municípios do RS (FAMURS)", "https://www.diariomunicipal.com.br/famurs/", "rs"),
}


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", diarios_sul.normalizar(texto)).strip("-")[:60]


def recortar_para_gemini(texto: str, limite: int = LIMITE_TEXTO_GEMINI) -> str:
    """Começo do edital (cronograma/cabeçalho) + janelas ao redor de 'médico' (quadro de cargos)."""
    if len(texto) <= limite:
        return texto
    partes = [texto[: limite // 3]]
    usado = len(partes[0])
    fim_ultima = limite // 3
    for achado in diarios_sul._CARGO_MEDICO.finditer(texto):
        if achado.start() < fim_ultima:
            continue
        ini = max(fim_ultima, achado.start() - 600)
        fim = min(len(texto), achado.end() + 1400)
        if usado + (fim - ini) > limite:
            break
        partes.append(texto[ini:fim])
        usado += fim - ini
        fim_ultima = fim
    return "\n[...]\n".join(partes)


def coletar_candidatas_sc(dias: int, hoje: date, sessao: requests.Session) -> list[dict]:
    zips = diarios_sul.listar_zips_sc(dias, hoje=hoje, sessao=sessao)
    print(f"SC: {len(zips)} ZIP(s) do CIGA na janela de {dias} dia(s).")
    candidatas: list[dict] = []
    for z in zips:
        resposta = sessao.get(z["url"], headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=180)
        resposta.raise_for_status()
        pubs = diarios_sul.ler_zip_sc(resposta.content)
        for pub in diarios_sul.candidatas_sc(pubs):
            url = pub.url if pub.url.lower().endswith(".pdf") else pub.link
            candidatas.append(
                {
                    "municipio": pub.municipio,
                    "titulo": pub.titulo,
                    "texto": diarios_sul.texto_limpo(pub.texto),
                    "url": url,
                    "tipo_documento": "pdf" if url.lower().endswith(".pdf") else "pagina_html",
                    "codigo": pub.codigo,
                }
            )
    return candidatas


def coletar_candidatas_sigpub(uf: str, dias: int, hoje: date, sessao: requests.Session, conn) -> list[dict]:
    inicio = hoje - timedelta(days=dias)
    cartoes: dict[str, object] = {}
    for termo in diarios_sul.TERMOS_SIGPUB:
        for pagina in range(1, PAGINAS_MAX_SIGPUB + 1):
            time.sleep(PAUSA_S)
            html = diarios_sul.buscar_sigpub(sessao, uf, termo, inicio, hoje, pagina)
            from notifica_vagas_scraper.fontes import sigpub_busca

            resultados = sigpub_busca.parsear_resultados(html)
            for r in resultados:
                cartoes.setdefault(r.codigo, r)
            if len(resultados) < 25:
                break
    print(f"{uf}: {len(cartoes)} matéria(s) distintas nas buscas.")

    candidatas: list[dict] = []
    for r in cartoes.values():
        if diarios_sul._EXCLUIR_TITULO.search(r.titulo):
            continue
        nome = diarios_sul.nome_municipio_da_entidade(r.entidade)
        if not nome:
            continue
        if conn is not None and db.buscar_codigo_ibge_local(conn, nome, uf) is None:
            continue
        time.sleep(PAUSA_S)
        resposta = sessao.get(r.url_load, headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=45)
        resposta.raise_for_status()
        texto = diarios_sul.texto_materia_sigpub(resposta.text)
        if not diarios_sul.eh_candidata(r.titulo, texto):
            continue
        candidatas.append(
            {
                "municipio": nome,
                "titulo": r.titulo,
                "texto": texto,
                "url": resposta.url,
                "tipo_documento": "pagina_html",
                "codigo": r.codigo,
            }
        )
    return candidatas


def processar(conn, fonte_id, uf: str, cand: dict, codigo_ibge: int, hoje: date) -> int:
    extraido = gemini_texto.extrair_vagas_de_texto(cand["titulo"], recortar_para_gemini(cand["texto"]))
    vagas = [v for v in (extraido.get("vagas") or []) if v.get("cargo") and eh_cargo_medico(v["cargo"])]
    if not vagas:
        print("    (Gemini: sem cargo médico de abertura)")
        return 0
    fim = gemini_util.parsear_data_iso(extraido.get("inscricoes_fim"))
    if fim and fim < hoje:
        print(f"    (inscrição já encerrada em {fim}, ignorado)")
        return 0
    numero = extraido.get("numero_edital") or cand["titulo"][:40]
    total = 0
    for vaga in vagas:
        ident = f"diariosul-{uf.lower()}-{codigo_ibge}-{slug(numero)}-{slug(vaga['cargo'])}"
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=ident,
            orgao=extraido.get("orgao") or f"Prefeitura Municipal de {cand['municipio']}",
            cargo=vaga["cargo"],
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=extraido.get("tipo_oportunidade"),
            numero_edital=numero,
            data_publicacao=gemini_util.parsear_data_iso(extraido.get("data_publicacao")),
            inscricoes_inicio=gemini_util.parsear_data_iso(extraido.get("inscricoes_inicio")),
            inscricoes_fim=fim,
            status="aberta",
            resumo=f"{cand['titulo']} — {vaga['cargo']} ({cand['municipio']}/{uf})",
            url_evidencia=cand["url"],
            tipo_documento=cand["tipo_documento"],
            texto_extraido=None,
            **gemini_util.campos_estruturados_extras(extraido, vaga),
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {vaga['cargo']}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uf", required=True, choices=sorted(FONTES))
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--max-gemini", type=int, default=12)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    uf = args.uf
    hoje = diarios_sul.datetime_hoje_brasilia().date()
    sessao = requests.Session()

    conn = None
    try:
        conn = db.conectar()
    except Exception as exc:
        if not args.dry_run:
            raise
        print(f"aviso: sem banco ({type(exc).__name__}); dry-run só lista candidatas, sem validar município.")
    try:
        if uf == "SC":
            candidatas = coletar_candidatas_sc(args.dias, hoje, sessao)
        else:
            candidatas = coletar_candidatas_sigpub(uf, args.dias, hoje, sessao, conn)
        print(f"{uf}: {len(candidatas)} candidata(s) a abertura com cargo médico (filtro sem IA).")

        nome_fonte, url_fonte, _ = FONTES[uf]
        fonte_id = None if (args.dry_run or conn is None) else db.upsert_fonte(conn, nome=nome_fonte, url=url_fonte, tipo="oficial", uf=uf)
        if fonte_id:
            conn.commit()

        gastos = total = 0
        vistos: set[str] = set()
        for cand in candidatas:
            codigo_ibge = db.buscar_codigo_ibge_local(conn, cand["municipio"], uf) if conn is not None else 0
            rotulo = f"{cand['municipio']}/{uf} | {cand['titulo'][:70]}"
            if codigo_ibge is None:
                print(f"  sem município no cadastro: {rotulo}")
                continue
            chave = f"{codigo_ibge}|{slug(cand['titulo'])}"
            chave_visto = f"{uf}:{cand['codigo']}"
            if chave in vistos or (
                conn is not None and (db.url_ja_e_evidencia(conn, [cand["url"]]) or db.diario_sul_ja_visto(conn, chave_visto))
            ):
                continue
            vistos.add(chave)
            if args.dry_run:
                print(f"  candidata: {rotulo}")
                continue
            if gastos >= args.max_gemini:
                print(f"  teto de {args.max_gemini} chamadas Gemini nesta execução; fica para a próxima: {rotulo}")
                continue
            print(f"  processando: {rotulo}")
            gastos += 1
            try:
                with conn.transaction():
                    gravadas = processar(conn, fonte_id, uf, cand, codigo_ibge, hoje)
                    db.registrar_diario_sul_visto(conn, chave=chave_visto, uf=uf, titulo=cand["titulo"], gerou_vaga=gravadas > 0)
                total += gravadas
                conn.commit()
            except Exception as exc:  # 1 publicação ruim não derruba o lote
                print(f"    ERRO: {exc}")
        print(f"\nOk. {total} vaga(s) médica(s) gravada(s); {gastos} chamada(s) Gemini.")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        main()
    else:
        with db.rastrear_execucao("rodar_diarios_sul.py"):
            main()
