"""Busca avançada por texto no Diário Oficial hospedado pela plataforma
SIGPub (diariomunicipal.com.br) — mesmo mecanismo usado tanto por
`/amm-mg/` (Diário Oficial dos Municípios Mineiros) quanto `/apm/`
(Associação Paulista de Municípios, SP). Investigação completa do
mecanismo em `docs/fixtures/dom_amm_mg/busca_resultado_*.html` no repo
principal (achado 2026-09-01): GET simples com `requests.Session`, sem
JS, sem navegador headless — só precisa reusar o token CSRF da MESMA
sessão que buscou a página de pesquisa.

Fluxo: `obter_token` (GET na página de pesquisa) → `buscar` (GET com os
parâmetros `busca_avancada[...]`, mesma sessão) → `parsear_resultados`
(extrai as linhas de `table#datatable`) → `resolver_url_materia` (segue
o redirect de `/<base>/load/<codigo>` até a URL canônica da matéria).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.diariomunicipal.com.br"
USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"


@dataclass
class ResultadoBusca:
    entidade: str
    titulo: str
    orgao: str
    data_circulacao: date | None
    codigo: str
    url_load: str


def obter_token(session: requests.Session, caminho_pesquisar: str) -> str | None:
    """GET na página de busca (ex: `/amm-mg/pesquisar`) pra pegar o token
    CSRF da sessão — precisa ser reusado na MESMA sessão no GET de busca.
    Achado real: token/sessão errados fazem a busca devolver a página
    normal SEM `table#datatable`, sem nenhum erro visível — por isso
    `parsear_resultados` trata "tabela ausente" como "sem resultado
    aproveitável", nunca levanta erro."""
    resposta = session.get(
        f"{BASE_URL}{caminho_pesquisar}", headers={"User-Agent": USER_AGENT}, timeout=30
    )
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.text, "html.parser")
    campo = soup.find("input", {"name": "busca_avancada[_token]"})
    return campo.get("value") if campo else None


def _parsear_data_circulacao(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d-%m-%Y").date()
    except ValueError:
        return None


def parsear_resultados(html: str) -> list[ResultadoBusca]:
    """Extrai as linhas de `table#datatable` (biblioteca jQuery
    DataTables — todas as linhas já vêm renderizadas no HTML da
    resposta, sem paginação server-side pra seguir). Devolve lista vazia
    tanto quando a tabela tem só a linha "Nenhum registro encontrado"
    quanto quando a tabela não existe (requisição rejeitada por token
    inválido/expirado) — mesmo efeito prático pro chamador: nada
    aproveitável desta busca."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table", {"id": "datatable"})
    if tabela is None:
        return []
    corpo = tabela.find("tbody")
    if corpo is None:
        return []

    resultados = []
    for linha in corpo.find_all("tr"):
        celulas = linha.find_all("td")
        if len(celulas) < 4:
            continue
        if "dataTables_empty" in (celulas[0].get("class") or []):
            continue
        link_entidade = celulas[0].find("a")
        link_titulo = celulas[1].find("a")
        link_orgao = celulas[2].find("a")
        link_data = celulas[3].find("a")
        if not (link_entidade and link_titulo and link_orgao and link_data):
            continue
        href = link_titulo.get("href", "")
        codigo = href.rsplit("/", 1)[-1]
        resultados.append(
            ResultadoBusca(
                entidade=link_entidade.get_text(strip=True),
                titulo=link_titulo.get_text(strip=True),
                orgao=link_orgao.get_text(strip=True),
                data_circulacao=_parsear_data_circulacao(link_data.get_text(strip=True)),
                codigo=codigo,
                url_load=f"{BASE_URL}{href}",
            )
        )
    return resultados


def buscar(
    session: requests.Session,
    *,
    caminho_pesquisar: str,
    token: str,
    entidade_id: str,
    termo: str,
    data_inicio: date,
    data_fim: date,
) -> str:
    """GET de busca avançada na MESMA sessão que gerou `token`. Devolve o
    HTML bruto da resposta — usar `parsear_resultados` pra extrair.
    `data_inicio`/`data_fim` são obrigatórios pro formulário (achado
    real: sem eles a busca é rejeitada)."""
    parametros = {
        "busca_avancada[page]": "",
        "busca_avancada[entidadeUsuaria]": entidade_id,
        "busca_avancada[nome_orgao]": "",
        "busca_avancada[titulo]": "",
        "busca_avancada[texto]": termo,
        "busca_avancada[dataInicio]": data_inicio.strftime("%d/%m/%Y"),
        "busca_avancada[dataFim]": data_fim.strftime("%d/%m/%Y"),
        "busca_avancada[Enviar]": "",
        "busca_avancada[_token]": token,
    }
    resposta = session.get(
        f"{BASE_URL}{caminho_pesquisar}",
        params=parametros,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resposta.raise_for_status()
    return resposta.text


def resolver_url_materia(session: requests.Session, url_load: str) -> str:
    """`/<base>/load/<codigo>` redireciona pra URL canônica da matéria
    (`/<base>/materia/<codigo>/<hash>`) — `requests` já segue o redirect
    por padrão, só devolve a URL final."""
    resposta = session.get(url_load, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()
    return resposta.url
