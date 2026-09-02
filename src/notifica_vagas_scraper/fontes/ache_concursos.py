"""Parser do Ache Concursos (acheconcursos.com.br), agregador nacional de
notícias de concurso público — achado 2026-09-01/02, investigação via
`pesquisador-fonte` + validação ao vivo via `curl`/Python direto (ver
TAREFAS.md). Sem bloqueio anti-bot em nenhuma etapa testada; HTML
server-rendered, sem JS necessário.

Estrutura:
- GET `/concursos-minas-gerais` ou `/concursos-sao-paulo` lista os
  concursos em uma `<table class="tbl-conc">` só, sem paginação nem
  filtro de "aberto" (mistura tudo) — cada linha tem só título+link,
  data limite de inscrição, quantidade agregada de vagas e salário
  máximo. **Sem cargo nem município estruturado** — cargo só existe no
  PDF (Gemini lê, igual Actcon/FGV/WordPress/IMAM/JCM/ACCESS);
  município reaproveita `fgv.encontrar_municipio` contra o título (achado
  real: nem todo item nomeia o município no título — ex: "Prefeitura em
  Minas Gerais abre vagas..." — esses ficam sem match e são pulados, por
  segurança, igual a FGV já faz).
- Cada linha da listagem leva pra um artigo/notícia (`/concursos-<uf>/
  <slug>`) com bloco `.concurso-info` (label `.cartola` + `<span>` — dá
  pra ler abertura/encerramento de inscrições e vagas de novo, mas
  redundante com a listagem) e seção "Anexos" (`a.anexo`) apontando pra
  uma sub-página `/edital-concurso/<slug>`.
- A sub-página do edital embute o PDF real num `<iframe>`. **Achado
  técnico importante**: o PDF não é hospedado no site de origem — é uma
  cópia baixada e re-hospedada no próprio domínio do Ache Concursos, em
  URL previsível `/imagens/anexo/<id>/<slug>.pdf`. Confirmado tanto pra
  edital federal (IFSULDEMINAS, cópia fiel do DOU) quanto municipal
  (Prefeitura de Lagoa da Prata/MG) — funciona igual pros dois tipos de
  entidade, sem depender do site original ficar no ar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.acheconcursos.com.br"


@dataclass
class ItemListagem:
    titulo: str
    url: str
    inscricoes_fim: date | None
    #: capturado pra uso futuro (`vagas.quantidade`, ver TAREFAS.md) —
    #: `rodar_ache_concursos.py` ainda não consome isso, cargo/quantidade
    #: reais vêm do Gemini por vaga individual.
    quantidade_vagas: int | None


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _parsear_inteiro(texto: str) -> int | None:
    try:
        return int(re.sub(r"\D", "", texto))
    except ValueError:
        return None


def listar_concursos(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table", class_="tbl-conc")
    if tabela is None:
        return []

    itens: list[ItemListagem] = []
    for linha in tabela.find_all("tr"):
        link = linha.find("a", href=True)
        if link is None:
            continue
        titulo_tag = link.find("span", class_="titulo")
        titulo = (titulo_tag.get_text(strip=True) if titulo_tag else link.get_text(strip=True)).strip()
        if not titulo:
            continue

        inscricao_tag = linha.find("span", class_="inscricao_fim")
        vagas_tag = linha.find("span", class_="numero_vagas")

        itens.append(
            ItemListagem(
                titulo=titulo,
                url=urljoin(BASE_URL, link["href"]),
                inscricoes_fim=_parsear_data(inscricao_tag.get_text()) if inscricao_tag else None,
                quantidade_vagas=_parsear_inteiro(vagas_tag.get_text()) if vagas_tag else None,
            )
        )

    return itens


def extrair_url_pagina_edital(html_artigo: str) -> str | None:
    """Seção "Anexos" do artigo aponta pra sub-página `/edital-concurso/
    <slug>` que embute o PDF de verdade — o artigo em si não tem o PDF
    direto."""
    soup = BeautifulSoup(html_artigo, "html.parser")
    link = soup.find("a", class_="anexo", href=True)
    if link is not None:
        return link["href"]
    # fallback: qualquer link pra /edital-concurso/ dentro da página,
    # caso a classe CSS mude sem o padrão de URL mudar junto.
    link = soup.find("a", href=re.compile(r"/edital-concurso/"))
    return link["href"] if link else None


def extrair_url_pdf(html_pagina_edital: str) -> str | None:
    """PDF real fica num `<iframe src=...>` na sub-página do edital — não
    é um link clicável comum."""
    soup = BeautifulSoup(html_pagina_edital, "html.parser")
    iframe = soup.find("iframe", src=re.compile(r"\.pdf($|\?)"))
    return iframe["src"] if iframe else None
