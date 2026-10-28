"""Processos Seletivos Simplificados (PSS) da Secretaria de Estado da Saúde de
Santa Catarina (`saude.sc.gov.br`) — achado 2026-10-27: a SES-SC publica um PSS
com cargo de médico a cada ~2 semanas (hospitais, UDAPH, regulação) e nenhuma
agregadora/Serper trazia isso para o banco.

Estrutura (Joomla, HTML estático, sem bloqueio):
- Listagem `PSS_LISTAGEM_URL`: um card por PSS (`div.mfp_default_item`) com o
  link do artigo e um resumo no padrão "Informações Gerais Vagas: <cargos>
  Local: <cidades>. Inscrições: dd/mm/aaaa até dd/mm/aaaa. Processo ...".
- Artigo do PSS: link `.../edital-pss-NNN-AAAA-.../download` = PDF do edital.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.saude.sc.gov.br"
PSS_LISTAGEM_URL = f"{BASE_URL}/index.php/pt/servicos/concursos-e-selecoes/processos-seletivos-ses"
UF = "SC"
ID_PREFIX = "ses-sc"
_RE_MEDICO = re.compile(r"\bm[eé]dic[oa]s?\b", re.IGNORECASE)
_RE_INSCRICAO = re.compile(r"Inscri[çc][õo]es:\s*(\d{2}/\d{2}/\d{4})\s*at[ée]\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
_RE_VAGAS = re.compile(r"Vagas:\s*(.+?)\s*Local:", re.IGNORECASE)
_RE_LOCAL = re.compile(r"Local:\s*(.+?)\s*Inscri", re.IGNORECASE)


@dataclass
class ItemPss:
    titulo: str  # "PSS Nº 042/2026/SES"
    url: str
    numero: str  # "042/2026"
    cargos: str
    local: str
    inscricoes_inicio: date | None
    inscricoes_fim: date | None

    @property
    def tem_medico(self) -> bool:
        return _RE_MEDICO.search(self.cargos) is not None


def _data(texto: str) -> date:
    return datetime.strptime(texto, "%d/%m/%Y").date()


def listar_pss(html: str) -> list[ItemPss]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemPss] = []
    for card in soup.select("div.mfp_default_item"):
        link = card.find("a", href=True, class_=False) or card.find("h4").find("a", href=True)
        intro = card.find("p", class_="mfp_introtext")
        if link is None or intro is None:
            continue
        titulo = link.get_text(strip=True)
        texto = re.sub(r"\s+", " ", intro.get_text(" ", strip=True))
        numero = re.search(r"(\d{3}/\d{4})", titulo)
        vagas = _RE_VAGAS.search(texto)
        local = _RE_LOCAL.search(texto)
        insc = _RE_INSCRICAO.search(texto)
        itens.append(
            ItemPss(
                titulo=titulo,
                url=urljoin(BASE_URL, link["href"].replace("&amp;", "&")),
                numero=numero.group(1) if numero else titulo,
                cargos=vagas.group(1) if vagas else texto,
                local=local.group(1) if local else "",
                inscricoes_inicio=_data(insc.group(1)) if insc else None,
                inscricoes_fim=_data(insc.group(2)) if insc else None,
            )
        )
    return itens


def url_edital(html_artigo: str) -> str | None:
    """Link `/download` do edital de abertura no artigo (ignora retificação/anexos)."""
    soup = BeautifulSoup(html_artigo, "html.parser")
    candidatos = [
        a["href"]
        for a in soup.find_all("a", href=True)
        if a["href"].rstrip("/").endswith("/download") and "edital" in a["href"].lower()
    ]
    preferidos = [h for h in candidatos if "retifica" not in h.lower() and "errata" not in h.lower()]
    escolhido = (preferidos or candidatos or [None])[0]
    return urljoin(BASE_URL, escolhido) if escolhido else None


def url_pagina_processo(html_artigo: str, numero: str) -> str | None:
    """Página do processo (`processos-seletivos-AAAA/processo-seletivo-NNN-AAAA`).

    Desde 2026-10 o artigo da listagem é só um ponteiro; o link `/download` do
    edital fica nessa página (ex.: PSS 042/2026)."""
    numero_url = numero.replace("/", "-")
    soup = BeautifulSoup(html_artigo, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/processos-seletivos-" in href and href.rstrip("/").endswith(f"processo-seletivo-{numero_url}"):
            return urljoin(BASE_URL, href)
    return None


def escolher_municipio(local: str, municipios_sc: dict[str, int], padrao: str = "Florianópolis") -> str:
    """Um único município de SC citado em `Local` -> ele; senão a sede (Florianópolis)
    ("Grande Florianópolis", várias cidades, etc.)."""
    citados = {nome for nome in municipios_sc if re.search(rf"\b{re.escape(nome)}\b", local, re.IGNORECASE)}
    return next(iter(citados)) if len(citados) == 1 else padrao
