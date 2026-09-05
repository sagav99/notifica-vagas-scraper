#!/usr/bin/env python3
"""Entrypoint do cron pra descoberta ampla via Google News RSS — pedido do
usuário (2026-09-05): complementar os scrapers site-a-site com uma
varredura barata que pega vaga já indexada/divulgada por veículo de
notícia (ex: quando aparece num agregador tipo PCI Concursos, ou direto
na imprensa local), sem depender de mapear cada banca/prefeitura
manualmente. Foco 100% em concurso médico (prioridade #1 do produto,
CLAUDE.md) via as ~20 queries de `fontes/google_news.QUERIES`.

Fluxo por item de RSS casado com município de MG/SP:
1. Resolve o link de redirect do Google News pra URL final do artigo
   (best-effort — se falhar, usa o próprio link do RSS).
2. Registra o sinal em `sinais_descoberta_externa` (mesma tabela da PCI,
   migration 015) — sempre, mesmo quando o domínio já é uma fonte oficial
   conhecida (nesse caso é só confirmação).
3. Só quando o domínio NÃO é uma fonte oficial já coberta, tenta extrair
   vaga de verdade: busca o texto da página final e manda pro Gemini
   (`gemini_texto.extrair_vagas_de_texto`, mesmo contrato usado pela
   Instar) — cargo/salário/edital. Sem cargo extraível, `vagas: []` (o
   próprio prompt já instrui isso) e nada é gravado em `vagas` — nunca
   aprova no escuro. Quando extrai algo, entra em `vagas`/`vaga_evidencias`
   com `revisao_status='pendente'`, mesma fila de revisão automática das
   outras fontes (`scripts/revisar_vagas.py`).

Quando o domínio JÁ é coberto por fonte oficial, pular a extração via
Gemini é deliberado: o parser dedicado daquela fonte já é mais confiável
que extrair de texto de notícia solta, e evitar 2 caminhos de inserção
pra mesma vaga real evita duplicata/conflito de dado.

Uso: python scripts/rodar_descoberta_google_news.py
Requer DATABASE_URL no ambiente; GEMINI_API_KEY é usado só quando o
domínio não é conhecido (sua ausência não impede o registro do sinal).
"""

from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests
from bs4 import BeautifulSoup

from notifica_vagas_scraper import db, gemini_texto
from notifica_vagas_scraper.fontes import fgv, google_news

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
UFS_DO_PROJETO = ["MG", "SP"]
JANELA_DIAS = 2  # cadência é 1x/dia; folga pequena contra ciclo atrasado/pulado
TEXTO_MAX_CHARS = 8000


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


def buscar_itens_recentes() -> list[google_news.ItemNoticia]:
    """1 GET por query em `google_news.QUERIES` — falha isolada numa
    query (rede, XML malformado) não derruba as demais."""
    todos: list[google_news.ItemNoticia] = []
    for query in google_news.QUERIES:
        try:
            resposta = requests.get(
                google_news.montar_url_busca(query), headers={"User-Agent": USER_AGENT}, timeout=20
            )
            resposta.raise_for_status()
        except requests.RequestException as exc:
            print(f"  aviso: falha buscando RSS pra '{query}': {exc}", file=sys.stderr)
            continue
        todos.extend(google_news.listar_itens(resposta.text))

    recentes = google_news.filtrar_recentes(todos, dentro_de_dias=JANELA_DIAS)

    vistos: set[str] = set()
    unicos = []
    for item in recentes:
        if item.link in vistos:
            continue
        vistos.add(item.link)
        unicos.append(item)
    return unicos


def resolver_url_final(link_google_news: str) -> str:
    """Segue o redirect do Google News pra URL real do artigo. Best-effort:
    falha de rede ou destino ainda em `news.google.com` (interstitial que
    depende de JS pra resolver de verdade) devolve o link original — ainda
    dá pra registrar o sinal e tentar extrair texto dele mesmo assim."""
    try:
        resposta = requests.get(
            link_google_news, headers={"User-Agent": USER_AGENT}, timeout=20, allow_redirects=True
        )
        return resposta.url
    except requests.RequestException:
        return link_google_news


def buscar_texto_pagina(url: str) -> str | None:
    try:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resposta.raise_for_status()
    except requests.RequestException:
        return None
    soup = BeautifulSoup(resposta.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    texto = soup.get_text(separator="\n", strip=True)
    return texto[:TEXTO_MAX_CHARS] if texto else None


def processar_item(
    conn,
    item: google_news.ItemNoticia,
    municipio: str,
    uf: str,
    codigo_ibge: int,
    dominios_conhecidos: set[str],
) -> int:
    url_final = resolver_url_final(item.link)
    dominio = urlparse(url_final).netloc.lower()
    coberto = dominio in dominios_conhecidos

    db.registrar_sinal_descoberta(
        conn,
        fonte_descoberta="google_news_rss",
        municipio_id=codigo_ibge,
        titulo=item.titulo,
        url=url_final,
        dominios_externos=[dominio] if dominio else [],
        coberto_por_fonte_oficial=coberto,
    )

    if coberto:
        # domínio já tem parser dedicado — a vaga real, se existir, já vem
        # por aquele caminho; extrair de texto de notícia aqui só criaria
        # um 2º caminho de inserção pra mesma vaga (risco de duplicata).
        return 0

    texto = buscar_texto_pagina(url_final) or item.titulo
    try:
        extraido = gemini_texto.extrair_vagas_de_texto(item.titulo, texto)
    except gemini_texto.ErroExtracaoGemini as exc:
        print(f"  aviso: falha na extração Gemini de '{item.titulo[:60]}': {exc}", file=sys.stderr)
        return 0

    if not extraido.get("vagas"):
        return 0

    fonte_id = db.upsert_fonte(
        conn, nome=f"Google News RSS ({uf})", url=google_news.BASE_URL, tipo="indice", uf=uf
    )
    orgao = extraido.get("orgao") or f"Prefeitura Municipal de {municipio}/{uf}"
    numero_edital = extraido.get("numero_edital")
    tipo_oportunidade = extraido.get("tipo_oportunidade")
    data_publicacao = _parsear_data_iso(extraido.get("data_publicacao"))
    inscricoes_inicio = _parsear_data_iso(extraido.get("inscricoes_inicio"))
    inscricoes_fim = _parsear_data_iso(extraido.get("inscricoes_fim"))

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=f"{_slug(url_final)}-{_slug(cargo)}",
            orgao=orgao,
            cargo=cargo,
            salario=Decimal(str(vaga["salario"])) if vaga.get("salario") is not None else None,
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=f"{item.titulo} (via Google News RSS, {item.fonte_nome or 'fonte não identificada'})",
            url_evidencia=url_final,
            tipo_documento="pagina_html",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    itens = buscar_itens_recentes()
    print(f"{len(itens)} item(ns) de RSS único(s) dentro da janela de {JANELA_DIAS} dia(s).")

    conn = db.conectar()
    try:
        municipios_completos = db.listar_municipios_com_codigo(conn, ufs=UFS_DO_PROJETO)
        municipios = [(nome, uf) for _, nome, uf in municipios_completos]
        codigo_por_nome_uf = {(nome, uf): codigo for codigo, nome, uf in municipios_completos}
        dominios_conhecidos = db.listar_dominios_fontes_conhecidas(conn)

        total_geral = 0
        casados = 0
        for item in itens:
            match = fgv.casar_municipio_com_guarda_de_uf(item.titulo, "", municipios)
            if match is None:
                continue
            municipio, uf = match
            codigo_ibge = codigo_por_nome_uf.get((municipio, uf))
            if codigo_ibge is None:
                continue
            casados += 1

            print(f"Processando '{item.titulo[:80]}' -> {municipio}/{uf}...")
            try:
                with conn.transaction():
                    total_geral += processar_item(conn, item, municipio, uf, codigo_ibge, dominios_conhecidos)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 item derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo[:80]}': {exc}", file=sys.stderr)

        print(f"\nOk. {casados} item(ns) casado(s) com município de MG/SP, {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_descoberta_google_news.py"):
        main()
