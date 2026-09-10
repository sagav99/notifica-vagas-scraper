"""Parser da plataforma Instar Tecnologia — endpoint de dados abertos de
concursos usado por várias prefeituras de MG (achado em 2026-09-01,
discussão do usuário fora desta sessão, ver TAREFAS.md do repo principal).

Endpoint: `GET {url_prefeitura}/portal/dados-abertos/concursos/{ano}` —
JSON público, sem autenticação, sem CSRF, sem paginação real. Formato:
`{"dados": [...]}`, onde cada item tem `titulo`, `situacao` ("Aberto" ou
"Concluído", entre outros ainda não catalogados), `modalidade` (texto
livre, ex: "Processo Seletivo", "Editais Temporários"), e `descricao`
(HTML rico, às vezes com uma `<table>` de cargo/formação/período, às
vezes só texto corrido). Quando não há nenhum registro, `dados` vem como
`[["Nenhum registro encontrado."]]` (lista de string, não de objeto) —
tratar como fonte sem resultado, não como erro.

Municípios confirmados: `dados/municipios_instar.csv` (231 municípios,
76 de MG + 155 de SP — corrigido 2026-09-02, o texto antigo dizia "230
de MG" mas a própria lista sempre teve as duas UFs; triagem em
2026-09-01 contra as ~1240 URLs de `public.municipios` já confirmadas —
ver docs/dados/triagem_instar_wordpress_2026-09-01.csv no repo
principal).

**2ª camada de varredura** (achado real 2026-09-05/09, plano aceito
2026-09-09 — ver TAREFAS.md): o endpoint de dados abertos acima nem
sempre enxerga o concurso — Confins nunca preencheu "dados abertos" (só
saiu como notícia comum), Sapucaí-Mirim preencheu mas numa categoria
("Chamamento Público") que o endpoint não devolve. `listar_itens_portal`
varre `/portal/editais` e `/portal/noticias` (mesmo template Instar,
HTML em vez de JSON) por palavra-chave — sem Gemini nesta varredura, só
aciona extração quando o endpoint de dados abertos não pegou o item (ver
`scripts/rodar_instar.py`).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from importlib import resources

from bs4 import BeautifulSoup

SITUACAO_ABERTA = "aberto"
_PADRAO_HREF_ITEM_PORTAL = re.compile(r"^/portal/(?:editais|noticias)/\d+/\d+/\d+/")


@dataclass
class MunicipioInstar:
    codigo_ibge: int
    nome: str
    uf: str
    url_prefeitura: str


def listar_municipios_instar() -> list[MunicipioInstar]:
    """Municípios com plataforma Instar confirmada (ver módulo docstring)."""
    caminho = resources.files("notifica_vagas_scraper.dados").joinpath("municipios_instar.csv")
    with caminho.open("r", encoding="utf-8", newline="") as f:
        return [
            MunicipioInstar(
                codigo_ibge=int(linha["codigo_ibge"]),
                nome=linha["nome"],
                uf=linha["uf"],
                url_prefeitura=linha["url_prefeitura"],
            )
            for linha in csv.DictReader(f)
        ]


def url_dados_abertos(url_prefeitura: str, ano: int) -> str:
    return url_prefeitura.rstrip("/") + f"/portal/dados-abertos/concursos/{ano}"


def url_editais(url_prefeitura: str) -> str:
    return url_prefeitura.rstrip("/") + "/portal/editais"


def url_noticias(url_prefeitura: str) -> str:
    return url_prefeitura.rstrip("/") + "/portal/noticias"


@dataclass
class ItemPortal:
    """Item de `/portal/editais` ou `/portal/noticias` (2ª camada de
    varredura, achado 2026-09-09 — ver TAREFAS.md). `situacao` só existe
    de verdade na listagem de editais (ex: "Aberto"); notícia não tem
    esse campo e vem sempre `None`."""

    titulo: str
    descricao: str
    url: str
    situacao: str | None


@dataclass
class CategoriaEditais:
    id: str
    nome: str


def listar_categorias_editais(html: str) -> list[CategoriaEditais]:
    """`/portal/editais` sem categoria sempre devolve 0 itens (achado real
    2026-09-10, confirmado ao vivo em 3 municípios) — o CMS exige escolher
    uma categoria do menu (`/portal/editais/{id}`, ex: "Editais de
    Licitação", "Editais de Concursos", "Chamamento Público") pra listar
    qualquer conteúdo. Reforça o próprio achado que motivou a 2ª camada:
    em Confins, "Chamamento Público" é uma categoria separada de
    "Concursos" — o mesmo tipo de categorização que fez o endpoint de
    dados abertos perder a vaga de Sapucaí-Mirim. O nome vem do card
    (`ed_titulo_tipo_edital`) que a própria página exibe pra cada
    categoria — o texto do menu lateral pro mesmo link varia (ex:
    "Concursos" vs "Concursos e Processos Seletivos"), o card é a fonte
    estável."""
    soup = BeautifulSoup(html, "html.parser")
    padrao = re.compile(r"^/portal/editais/(\d+)$")
    vistos: set[str] = set()
    categorias: list[CategoriaEditais] = []
    for link in soup.find_all("a", href=True):
        match = padrao.match(link["href"])
        if not match or match.group(1) in vistos:
            continue
        titulo_tag = link.find(class_=lambda c: c and "titulo_tipo_edital" in c)
        if titulo_tag is None:
            continue
        vistos.add(match.group(1))
        categorias.append(CategoriaEditais(id=match.group(1), nome=titulo_tag.get_text(strip=True)))
    return categorias


def listar_itens_portal(html: str, url_prefeitura: str) -> list[ItemPortal]:
    """Extrai itens de `/portal/editais` ou `/portal/noticias` — mesmo
    template Instar pras duas seções, só troca o prefixo de classe
    ("ed_" vs "ntc_"), por isso casa por substring de classe em vez do
    nome exato."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemPortal] = []
    for link in soup.find_all("a", href=True):
        if not _PADRAO_HREF_ITEM_PORTAL.match(link["href"]):
            continue
        titulo_tag = link.find(class_=lambda c: c and "titulo" in c)
        if titulo_tag is None:
            continue
        descricao_tag = link.find(class_=lambda c: c and "descricao" in c)
        situacao_tag = link.find("span", class_=lambda c: c and "situacao" in c)
        itens.append(
            ItemPortal(
                titulo=titulo_tag.get_text(strip=True),
                descricao=descricao_tag.get_text(" ", strip=True) if descricao_tag else "",
                url=url_prefeitura.rstrip("/") + link["href"],
                situacao=situacao_tag.get_text(strip=True) if situacao_tag else None,
            )
        )
    return itens


def listar_itens_abertos(payload: dict) -> list[dict]:
    """Filtra `payload["dados"]` pelos itens com situacao "Aberto"
    (case-insensitive). Trata o sentinela de "sem registro"
    (`[["Nenhum registro encontrado."]]`, itens que são lista em vez de
    dict) devolvendo lista vazia em vez de levantar erro."""
    itens = payload.get("dados") or []
    return [
        item
        for item in itens
        if isinstance(item, dict) and (item.get("situacao") or "").strip().lower() == SITUACAO_ABERTA
    ]
