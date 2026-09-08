#!/usr/bin/env python3
"""Descoberta ampla via Google Custom Search API.

Uso normal: ``python scripts/rodar_descoberta_google_search.py``.
Use ``--backfill`` na carga retroativa manual: ele afrouxa (ou remove) a
restrição de recência da busca, pra achar edital com inscrição ainda aberta
mas publicado há mais tempo, e grava um relatório em ``relatorios/`` ao
terminar. Combine com ``--janela {semana,mes,trimestre,tudo}`` (padrão
"tudo") pra fazer o backfill em estágios de recência — decisão do usuário,
2026-09-08: começar pelo que tem mais chance de ainda estar aberto (semana,
depois mês) antes de ir pra janelas maiores, em vez de um "tudo de uma vez"
só dominado por resultado antigo/já encerrado. Cada estágio cabe várias
vezes dentro da cota diária gratuita — dá pra rodar todos no mesmo dia.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests
from bs4 import BeautifulSoup

from notifica_vagas_scraper import db, gemini_pdf, gemini_texto
from notifica_vagas_scraper.fontes import fgv, google_search

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
UFS_DO_PROJETO = ["MG", "SP"]
TEXTO_MAX_CHARS = 8000
RELATORIOS_DIR = Path(__file__).parent.parent / "relatorios"
MAX_FALHAS_CONSECUTIVAS_API = 3


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")[:120]


def _parsear_data_iso(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def buscar_itens(*, api_key: str, engine_id: str, backfill: bool = False, janela: str | None = None) -> list[google_search.ItemBusca]:
    """Faz no máximo uma consulta por query, nunca excedendo a cota diária."""
    queries = google_search.QUERIES[: google_search.COTA_DIARIA_GRATUITA]
    if len(google_search.QUERIES) > len(queries):
        print(
            f"aviso: {len(google_search.QUERIES)} queries configuradas; usando só {len(queries)} "
            f"para respeitar a cota diária de {google_search.COTA_DIARIA_GRATUITA}.",
            file=sys.stderr,
        )

    todos: list[google_search.ItemBusca] = []
    falhas_consecutivas = 0
    for query in queries:
        try:
            resposta = requests.get(
                google_search.BASE_URL,
                params=google_search.montar_parametros(
                    query, api_key=api_key, engine_id=engine_id, backfill=backfill, janela=janela
                ),
                headers={"User-Agent": USER_AGENT},
                timeout=20,
            )
            resposta.raise_for_status()
            dados = resposta.json()
        except (requests.RequestException, ValueError) as exc:
            # ``str(HTTPError)`` inclui a URL inteira, inclusive o parâmetro
            # ``key``. Nunca a escrevemos em log.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detalhe = f"HTTP {status}" if status else type(exc).__name__
            print(f"  aviso: falha buscando Google Custom Search para '{query}': {detalhe}", file=sys.stderr)
            falhas_consecutivas += 1
            if falhas_consecutivas >= MAX_FALHAS_CONSECUTIVAS_API:
                print(
                    f"  aviso: {falhas_consecutivas} falhas consecutivas na API; interrompendo "
                    "as consultas restantes para não insistir contra uma configuração inválida.",
                    file=sys.stderr,
                )
                break
            continue
        falhas_consecutivas = 0
        todos.extend(google_search.listar_itens(dados))

    vistos: set[str] = set()
    return [item for item in todos if not (item.link in vistos or vistos.add(item.link))]


def buscar_pagina_html(url: str) -> str | None:
    try:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resposta.raise_for_status()
    except requests.RequestException:
        return None
    return resposta.text


def extrair_texto_de_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    texto = soup.get_text(separator="\n", strip=True)
    return texto[:TEXTO_MAX_CHARS] if texto else None


def buscar_texto_pagina(url: str) -> str | None:
    html = buscar_pagina_html(url)
    return extrair_texto_de_html(html) if html else None


def encontrar_pdf_edital(html: str, url_base: str) -> str | None:
    """Heurística genérica pra achar o PDF do edital numa página de
    origem desconhecida a priori (diferente dos parsers dedicados por
    banca, que já sabem a estrutura exata do site). Prioriza link cujo
    texto ou URL contenha "edital"; sem isso, cai pro primeiro PDF
    linkado na página."""
    soup = BeautifulSoup(html, "html.parser")
    candidatos = [
        (link.get_text(strip=True), urljoin(url_base, link["href"]))
        for link in soup.find_all("a", href=True)
        if ".pdf" in link["href"].lower()
    ]
    for texto, url_pdf in candidatos:
        if "edital" in texto.lower() or "edital" in url_pdf.lower():
            return url_pdf
    return candidatos[0][1] if candidatos else None


def baixar_pdf(url: str) -> bytes | None:
    try:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        resposta.raise_for_status()
    except requests.RequestException:
        return None
    return resposta.content


def normalizar_dominio(url: str) -> str:
    """Retorna o host comparável a ``public.fontes.url``.

    Uma fonte pode estar cadastrada sem ``www`` enquanto o resultado do
    Google usa a variação com ``www`` (ou vice-versa). Para fins de decidir
    se já existe um parser dedicado, ambas representam a mesma origem.
    """
    dominio = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return dominio.removeprefix("www.")


def processar_item(conn, item: google_search.ItemBusca, municipio: str, uf: str, codigo_ibge: int,
                   dominios_conhecidos: set[str], gemini_api_key: str) -> tuple[int, int]:
    """Registra sempre o sinal e devolve ``(sinais_novos, vagas_extraidas)``."""
    dominio = normalizar_dominio(item.link)
    dominios_normalizados = {normalizar_dominio(f"//{host}") for host in dominios_conhecidos}
    coberto = dominio in dominios_normalizados
    sinal_novo = db.registrar_sinal_descoberta(
        conn,
        fonte_descoberta="google_custom_search",
        municipio_id=codigo_ibge,
        titulo=item.titulo,
        url=item.link,
        dominios_externos=[dominio] if dominio else [],
        coberto_por_fonte_oficial=coberto,
    )
    if not sinal_novo:
        # O sinal é idempotente por URL. Reprocessá-lo consumiria Gemini e
        # poderia criar uma evidência duplicada, sem acrescentar informação.
        return 0, 0

    # `coberto=True` NÃO pula mais a extração (decisão do usuário,
    # 2026-09-08): pular era o comportamento antigo, mas isso fazia essa
    # função de auditoria se anular — se a fonte oficial daquele domínio
    # estiver quebrada (achado real em produção nesta data: JCM, ACCESS,
    # Avança SP e FUNDEP falhando ao mesmo tempo), o Vigia simplesmente
    # descartava o sinal em vez de servir de rede de segurança. Agora
    # sempre extrai; `inserir_vaga_com_evidencia` já dedup a nível de
    # `vagas` (município+órgão+cargo+edital), então se a fonte oficial já
    # capturou a mesma vaga, isso só anexa mais uma evidência à vaga
    # existente — sem duplicar. Se criar uma vaga NOVA mesmo com
    # `coberto=True`, é o sinal de alerta: a fonte oficial deveria ter
    # achado isso e não achou.

    # Prioriza ler o PDF do edital quando dá pra achar um (informação de
    # cargo/salário costuma estar completa só lá, igual às fontes oficiais
    # ACCESS/IMAM/JCM) — cai pro texto da página (HTML) só quando não tem
    # PDF ou a extração dele falha.
    url_pdf = item.link if urlparse(item.link).path.lower().endswith(".pdf") else None
    html = None
    if url_pdf is None:
        html = buscar_pagina_html(item.link)
        if html:
            url_pdf = encontrar_pdf_edital(html, item.link)

    extraido = None
    tipo_documento = "pagina_html"
    url_evidencia = item.link
    if url_pdf:
        pdf_bytes = baixar_pdf(url_pdf)
        if pdf_bytes:
            try:
                extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_bytes, api_key=gemini_api_key)
                tipo_documento = "pdf"
                url_evidencia = url_pdf
            except gemini_pdf.ErroExtracaoGemini as exc:
                print(f"  aviso: falha na extração Gemini (PDF) de '{item.titulo[:60]}': {exc}", file=sys.stderr)

    if extraido is None:
        texto = (extrair_texto_de_html(html) if html else None) or item.resumo or item.titulo
        try:
            extraido = gemini_texto.extrair_vagas_de_texto(item.titulo, texto, api_key=gemini_api_key)
        except gemini_texto.ErroExtracaoGemini as exc:
            print(f"  aviso: falha na extração Gemini de '{item.titulo[:60]}': {exc}", file=sys.stderr)
            return int(sinal_novo), 0

    if not extraido.get("vagas"):
        return int(sinal_novo), 0

    fonte_id = db.upsert_fonte(conn, nome=f"Google Custom Search ({uf})", url=google_search.BASE_URL,
                               tipo="indice", uf=uf)
    orgao = extraido.get("orgao") or f"Prefeitura Municipal de {municipio}/{uf}"
    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        resumo = f"{item.titulo} (via Google Custom Search)"
        if coberto:
            # Achado (c): domínio já tinha parser oficial, mas essa vaga
            # não veio dele — marca pra chamar atenção na revisão/painel
            # admin, em vez de só logar num job que ninguém acompanha.
            resumo = f"[ALERTA cobertura: {dominio} tem fonte oficial, mas não achou isto] {resumo}"
        resultado = db.inserir_vaga_com_evidencia(
            conn, fonte_id=fonte_id, municipio_id=codigo_ibge,
            identificador_externo=f"{_slug(item.link)}-{_slug(cargo)}", orgao=orgao, cargo=cargo,
            salario=Decimal(str(vaga["salario"])) if vaga.get("salario") is not None else None,
            salario_tipo=vaga.get("salario_tipo"), tipo_oportunidade=extraido.get("tipo_oportunidade"),
            numero_edital=extraido.get("numero_edital"), data_publicacao=_parsear_data_iso(extraido.get("data_publicacao")),
            inscricoes_inicio=_parsear_data_iso(extraido.get("inscricoes_inicio")),
            inscricoes_fim=_parsear_data_iso(extraido.get("inscricoes_fim")), status="aberta",
            resumo=resumo, url_evidencia=url_evidencia,
            tipo_documento=tipo_documento, texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        if coberto and resultado["vaga_criada"]:
            print(f"    ALERTA cobertura: {cargo}: vaga_id={resultado['vaga_id']} é NOVA mesmo com domínio '{dominio}' coberto por fonte oficial ({novo})")
        else:
            print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return int(sinal_novo), total


def escrever_relatorio_backfill(*, janela: str, itens: int, casados: int, sinais_novos: int, vagas: int) -> Path:
    RELATORIOS_DIR.mkdir(exist_ok=True)
    caminho = RELATORIOS_DIR / f"backfill_google_custom_search_{janela}_{datetime.now(timezone.utc):%Y-%m-%d}.md"
    caminho.write_text(
        f"# Backfill Google Custom Search API — janela: {janela}\n\n"
        f"Executado em {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}.\n\n"
        f"- Resultados únicos: {itens}\n- Casados com municípios MG/SP: {casados}\n"
        f"- Sinais novos: {sinais_novos}\n- Vagas extraídas: {vagas}\n",
        encoding="utf-8",
    )
    return caminho


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true", help="busca com recência afrouxada/removida; usar na carga retroativa manual")
    parser.add_argument(
        "--janela", choices=sorted(google_search.JANELAS_BACKFILL), default="tudo",
        help='só com --backfill: estágio de recência ("semana", "mes", "trimestre" ou "tudo", padrão "tudo")',
    )
    args = parser.parse_args(argv)
    api_key = os.environ.get("GOOGLE_CUSTOM_SEARCH_API_KEY")
    engine_id = os.environ.get("GOOGLE_CUSTOM_SEARCH_ENGINE_ID")
    gemini_api_key = os.environ.get("GEMINI_API_KEY_DESCOBERTA_GOOGLE")
    faltando = [nome for nome, valor in (("GOOGLE_CUSTOM_SEARCH_API_KEY", api_key),
                                         ("GOOGLE_CUSTOM_SEARCH_ENGINE_ID", engine_id),
                                         ("GEMINI_API_KEY_DESCOBERTA_GOOGLE", gemini_api_key)) if not valor]
    if faltando:
        raise RuntimeError(f"Variáveis de ambiente obrigatórias não definidas: {', '.join(faltando)}")

    itens = buscar_itens(api_key=api_key, engine_id=engine_id, backfill=args.backfill, janela=args.janela if args.backfill else None)
    print(f"{len(itens)} resultado(s) único(s) da Google Custom Search API.")
    conn = db.conectar()
    try:
        municipios_completos = db.listar_municipios_com_codigo(conn, ufs=UFS_DO_PROJETO)
        municipios = [(nome, uf) for _, nome, uf in municipios_completos]
        codigos = {(nome, uf): codigo for codigo, nome, uf in municipios_completos}
        dominios_conhecidos = db.listar_dominios_fontes_conhecidas(conn)
        casados = sinais_novos = vagas = 0
        for item in itens:
            match = fgv.casar_municipio_com_guarda_de_uf(item.titulo, "", municipios)
            if match is None or (codigo_ibge := codigos.get(match)) is None:
                continue
            casados += 1
            print(f"Processando '{item.titulo[:80]}' -> {match[0]}/{match[1]}...")
            try:
                with conn.transaction():
                    novos, extraidas = processar_item(conn, item, match[0], match[1], codigo_ibge,
                                                       dominios_conhecidos, gemini_api_key)
                conn.commit()
                sinais_novos += novos
                vagas += extraidas
            except Exception as exc:
                print(f"  ERRO processando '{item.titulo[:80]}': {exc}", file=sys.stderr)
        print(f"\nOk. {casados} casado(s), {sinais_novos} sinal(is) novo(s), {vagas} vaga(s) processada(s).")
        if args.backfill:
            print(f"Relatório do backfill: {escrever_relatorio_backfill(janela=args.janela, itens=len(itens), casados=casados, sinais_novos=sinais_novos, vagas=vagas)}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_descoberta_google_search.py"):
        main()
