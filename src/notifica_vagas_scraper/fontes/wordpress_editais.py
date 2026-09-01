"""Parser de posts de edital/processo seletivo em sites de prefeitura que
rodam WordPress de verdade (achado 2026-09-01, investigação via
`pesquisador-fonte`, ver TAREFAS.md) — **não** confundir com a flag
`wordpress_editais` da triagem anterior (`docs/dados/
triagem_instar_wordpress_2026-09-01.csv` no repo principal), que teve
alta taxa de falso positivo: de 20 candidatos, só 8 rodam WordPress de
verdade (confirmado via `GET /wp-json/`, ver
`docs/dados/categorizacao_wordpress_2026-09-01.csv`).

Sem categoria/slug único confiável entre instalações (achado real:
mesmo ID de categoria significa coisa diferente em sites diferentes) —
a API REST do WordPress (`wp-json/wp/v2/posts?search=<termo>`) é o
mecanismo de descoberta genérico que funciona em qualquer instalação,
sem precisar descobrir taxonomia por site.

Cargo/salário quase sempre só existem no PDF anexado ao post (não no
HTML do post em si) — extração via Gemini (`gemini_pdf.py`), mesmo
padrão da FGV/Actcon, não determinística feito Instar/AMM-MG.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources

import requests

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
TERMOS_BUSCA = ("processo seletivo", "concurso público")


@dataclass
class MunicipioWordpress:
    codigo_ibge: int
    nome: str
    uf: str
    url_prefeitura: str


@dataclass
class PostEdital:
    id: int
    titulo: str
    data: date | None
    link: str
    conteudo_html: str


def listar_municipios_wordpress() -> list[MunicipioWordpress]:
    caminho = resources.files("notifica_vagas_scraper.dados").joinpath("municipios_wordpress.csv")
    with caminho.open("r", encoding="utf-8", newline="") as f:
        return [
            MunicipioWordpress(
                codigo_ibge=int(linha["codigo_ibge"]),
                nome=linha["nome"],
                uf=linha["uf"],
                url_prefeitura=linha["url_prefeitura"],
            )
            for linha in csv.DictReader(f)
        ]


def _parsear_data(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto).date()
    except ValueError:
        return None


def buscar_posts(url_prefeitura: str, termo: str, *, por_pagina: int = 20) -> list[PostEdital]:
    """GET na API REST pública do WordPress — sem autenticação, sem
    precisar saber categoria/taxonomia do site. Devolve lista vazia (sem
    levantar) se o endpoint não existir/responder erro — chamador decide
    o que fazer (município pode ter mudado de CMS, por exemplo)."""
    try:
        resposta = requests.get(
            url_prefeitura.rstrip("/") + "/wp-json/wp/v2/posts",
            params={"search": termo, "per_page": por_pagina, "orderby": "date", "order": "desc"},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resposta.raise_for_status()
        dados = resposta.json()
    except (requests.exceptions.RequestException, ValueError):
        return []

    if not isinstance(dados, list):
        return []

    return [
        PostEdital(
            id=item["id"],
            titulo=item.get("title", {}).get("rendered", ""),
            data=_parsear_data(item.get("date")),
            link=item.get("link", ""),
            conteudo_html=item.get("content", {}).get("rendered", ""),
        )
        for item in dados
        if isinstance(item, dict) and "id" in item
    ]


def extrair_pdfs(conteudo_html: str) -> list[str]:
    """PDFs anexados ao post (bloco nativo de arquivo do WordPress ou
    link direto) — dedup preservando ordem de aparição."""
    encontrados = re.findall(r'href="([^"]+\.pdf)"', conteudo_html)
    vistos: list[str] = []
    for url in encontrados:
        if url not in vistos:
            vistos.append(url)
    return vistos


def escolher_pdf_edital(urls_pdf: list[str]) -> str | None:
    """Entre os PDFs anexados (edital, portaria, resultado, homologação),
    o nome do arquivo costuma indicar qual é o edital de abertura — só
    ele tem cargo/salário/vagas completos, resultado/homologação não.
    Sem sinal no nome, usa o primeiro (geralmente o mais referenciado no
    início do post)."""
    if not urls_pdf:
        return None
    for url in urls_pdf:
        if "edital" in url.lower():
            return url
    return urls_pdf[0]
