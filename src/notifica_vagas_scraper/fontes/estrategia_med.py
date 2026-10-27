"""Estratégia MED — Concursos Médicos (med.estrategia.com/portal/concursos),
feed curado de concursos/seletivos para médico do país todo. Segunda fonte
curada (independente de O Concurso Médico) para o Sul — achado 2026-10-27: em
amostra de 2026, OCM não tinha Cianorte, Novo Hamburgo, Brusque, Chapecó,
Prudentópolis e outros que a Estratégia publicou.

Mesmo princípio de OCM/Ache: só usamos como ÍNDICE (cidade/UF, período de
inscrição e link do edital em PDF hospedado no CDN deles); cargo, salário e
vagas vêm do PDF via Gemini.

- Feed WordPress: `FEED_URL` (`?paged=N` para o backlog), 15 itens por página.
- Artigo: tabela "Período de Inscrição 27/07 a 25/08/2026" e link
  "ACESSE O EDITAL COMPLETO!" apontando para o PDF.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup

FEED_URL = "https://med.estrategia.com/portal/concursos/feed/"
SITE_URL = "https://med.estrategia.com/portal/concursos/"

_NOMES_UF = {
    "paraná": "PR",
    "parana": "PR",
    "santa catarina": "SC",
    "rio grande do sul": "RS",
}
_RE_UF_TITULO = re.compile(r"[\(/\-]\s*(PR|SC|RS|SP|MG|[A-Z]{2})\s*\)?", re.UNICODE)
_RE_PERIODO = re.compile(
    r"Per[ií]odo(?: de)? Inscri[çc](?:[ãa]o|[õo]es)\s*:?\s*(\d{2})/(\d{2})(?:/(\d{4}))?\s*(?:a|à|até)\s*(\d{2})/(\d{2})/(\d{4})",
    re.IGNORECASE,
)


_MESES = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"], 1)}
_RE_PROSA = re.compile(
    r"inscri[çc][õo]es[^.]{0,120}?entre\s+(?:os\s+dias\s+)?(\d{1,2})[ºo°]?\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?"
    r"\s+e\s+(?:o\s+dia\s+)?(\d{1,2})[ºo°]?\s+de\s+([a-zç]+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)


@dataclass
class ItemFeed:
    titulo: str
    url: str


@dataclass
class Artigo:
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    edital_url: str | None
    uf: str | None  # UF citada no 1º parágrafo ("no Paraná", "em Santa Catarina"...)


def listar_feed(xml_texto: str) -> list[ItemFeed]:
    raiz = ET.fromstring(xml_texto)
    itens = []
    for item in raiz.iter("item"):
        titulo = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if titulo and link:
            itens.append(ItemFeed(titulo=titulo, url=link))
    return itens


def uf_do_titulo(titulo: str) -> str | None:
    """UF entre parênteses/barra no título ("Ivoti (RS) abre...", "Cambé/PR lança...")."""
    m = re.search(r"[\(/]\s*([A-Z]{2})\s*\)?(?=\s|$|[,;:])", titulo)
    return m.group(1) if m else None


def parsear_artigo(html: str) -> Artigo:
    soup = BeautifulSoup(html, "html.parser")
    texto = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    inicio = fim = None
    m = _RE_PERIODO.search(texto)
    if m:
        d1, m1, a1, d2, m2, a2 = m.groups()
        try:
            fim = date(int(a2), int(m2), int(d2))
            ano_ini = int(a1) if a1 else int(a2)
            inicio = date(ano_ini, int(m1), int(d1))
            if inicio > fim:  # "20/12 a 15/01/2027"
                inicio = date(ano_ini - 1, int(m1), int(d1))
        except ValueError:
            inicio = fim = None

    if fim is None:
        p = _RE_PROSA.search(texto)
        if p and p.group(2).lower() in _MESES and p.group(5).lower() in _MESES:
            d1, m1, a1, d2, m2, a2 = p.groups()
            try:
                fim = date(int(a2), _MESES[m2.lower()], int(d2))
                inicio = date(int(a1) if a1 else int(a2), _MESES[m1.lower()], int(d1))
            except ValueError:
                inicio = fim = None

    edital = None
    for a in soup.find_all("a", href=True):
        if "edital" in a.get_text(" ", strip=True).lower() and a["href"].lower().split("?")[0].endswith(".pdf"):
            edital = a["href"]
            break

    uf = None
    primeiros = texto[:1500].lower()
    for nome, sigla in _NOMES_UF.items():
        if re.search(rf"\b(?:no|na|em|do|de)\s+{nome}\b", primeiros):
            uf = sigla
            break
    return Artigo(inscricoes_inicio=inicio, inscricoes_fim=fim, edital_url=edital, uf=uf)
