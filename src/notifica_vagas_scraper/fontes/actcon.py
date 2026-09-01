"""Parser da plataforma Actcon.net — CMS de portal municipal usado por
vários municípios de MG (achado 2026-09-01, investigação via
`pesquisador-fonte`, ver TAREFAS.md), módulo "Processos Seletivos"
(`/processos-seletivos`).

A listagem e a página de detalhe carregam os dados via AjaxPro.NET (não
é HTML estático renderizado no servidor, ao contrário do que a
investigação inicial sugeriu) — mas o protocolo é simples o bastante
pra replicar com `requests` puro, sem navegador headless:

1. GET `/processos-seletivos` — HTML da página, contém a URL exata do
   proxy AjaxPro (`ajaxpro/proc_sel_lis,<hash>.ashx`, hash muda por
   deployment/município).
2. POST nesse `.ashx` com header `X-AjaxPro-Method: GetListaDados` e um
   corpo JSON com os parâmetros de filtro (todos `None` = sem filtro) —
   devolve `new Ajax.Web.DataTable([colunas], [linhas]);/*...*/`, que
   não é JSON puro (é uma chamada de construtor JS) mas vira JSON válido
   envolvendo os dois argumentos em `[...]` antes do parse.
3. Pra cada processo com `NMSITUACAO` em `SITUACOES_ABERTAS`: GET
   `proc_sel_vis.aspx?cd=<CDPROCSELETIVO>` (redireciona pra URL amigável,
   não precisa saber o slug de antemão) pra achar o proxy AjaxPro
   `proc_sel_vis,<hash>.ashx` dessa página.
4. POST nesse `.ashx` com `X-AjaxPro-Method: GetPublicacoes`,
   `{"cdProcSeletivo": "<id>", "dmOpcaoExibicao": "1"}` — devolve uma
   string JSON contendo HTML (`<ul><li><a href="abrir_arquivo.aspx?...">
   dd/mm/aaaa - Título</a></li>...</ul>`) com a lista de publicações
   (editais, resultados, convocações) e o link real de cada PDF.

Cargo/salário ficam só dentro do PDF (mesmo padrão da FGV) — extração
via Gemini (`gemini_pdf.py`), não determinística feito Instar/AMM-MG.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources

import requests
from bs4 import BeautifulSoup

SITUACOES_ABERTAS = {"Em andamento"}
USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"


@dataclass
class MunicipioActcon:
    codigo_ibge: int
    nome: str
    uf: str
    url_prefeitura: str


@dataclass
class ProcessoSeletivo:
    cd: int
    titulo: str
    situacao: str
    unidade: str


@dataclass
class Publicacao:
    data: date | None
    titulo: str
    url_pdf: str


def listar_municipios_actcon() -> list[MunicipioActcon]:
    caminho = resources.files("notifica_vagas_scraper.dados").joinpath("municipios_actcon.csv")
    with caminho.open("r", encoding="utf-8", newline="") as f:
        return [
            MunicipioActcon(
                codigo_ibge=int(linha["codigo_ibge"]),
                nome=linha["nome"],
                uf=linha["uf"],
                url_prefeitura=linha["url_prefeitura"],
            )
            for linha in csv.DictReader(f)
        ]


def _extrair_ashx(html: str, nome_proxy: str) -> str | None:
    m = re.search(rf"ajaxpro/{nome_proxy},[A-Za-z0-9_]+\.ashx", html)
    return m.group(0) if m else None


def _chamar_ajaxpro(url_prefeitura: str, ashx_path: str, metodo: str, corpo: dict) -> str:
    resposta = requests.post(
        url_prefeitura.rstrip("/") + "/" + ashx_path,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
            "X-AjaxPro-Method": metodo,
        },
        data=json.dumps(corpo),
        timeout=20,
    )
    resposta.raise_for_status()
    return resposta.text


def _remover_comentario_final(texto: str) -> str:
    """Toda resposta do AjaxPro.NET termina em `;/*` (comentário JS
    literalmente NUNCA fechado com `*/` — é só um marcador fixo de fim
    de payload, não um comentário de verdade) depois do valor real —
    remove isso antes de tentar parsear o valor como JSON. Achado real:
    `json.loads` direto falha com "Extra data" nas respostas de
    `GetPublicacoes` por causa desse sufixo (não apareceu no teste manual
    inicial porque a resposta impressa ali foi truncada antes de chegar
    nele — e uma 1ª tentativa de corrigir exigindo `*/` de fechamento
    também falhou, porque esse fechamento nunca existe de verdade)."""
    texto = texto.strip()
    idx = texto.rfind(";/*")
    return texto[:idx] if idx != -1 else texto


def _parsear_datatable(texto: str) -> list[dict]:
    """`new Ajax.Web.DataTable([colunas], [linhas]);/*...*/` não é JSON
    puro (é literalmente uma chamada de construtor JS) — os dois
    argumentos, unidos numa lista `[...]`, viram um array JSON válido."""
    m = re.match(r"new Ajax\.Web\.DataTable\((.*)\)", _remover_comentario_final(texto), re.DOTALL)
    if not m:
        return []
    colunas, linhas = json.loads("[" + m.group(1) + "]")
    nomes = [c[0] for c in colunas]
    return [dict(zip(nomes, linha)) for linha in linhas]


def listar_processos_seletivos(url_prefeitura: str) -> list[ProcessoSeletivo]:
    """Só devolve processos com situação em SITUACOES_ABERTAS (ver
    módulo) — "Homologado"/"Encerrado" já foram decididos, "Em
    convocação" é sobre chamar quem já passou, não vaga nova."""
    resposta = requests.get(
        url_prefeitura.rstrip("/") + "/processos-seletivos",
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resposta.raise_for_status()
    ashx = _extrair_ashx(resposta.text, "proc_sel_lis")
    if not ashx:
        return []

    corpo = {
        "Page": 0,
        "Size": 200,
        "cdTipo": None,
        "nuProcSeletivo": None,
        "nuAno": None,
        "palavraChave": None,
        "cdUnidade": None,
        "cdSituacao": None,
        "dmOrdenacao": None,
    }
    texto = _chamar_ajaxpro(url_prefeitura, ashx, "GetListaDados", corpo)
    linhas = _parsear_datatable(texto)
    return [
        ProcessoSeletivo(
            cd=int(linha["CDPROCSELETIVO"]),
            titulo=f"{linha['NMTIPO']} {linha['NUPROCSELETIVO']}/{linha['NUANOPROCSELETIVO']}",
            situacao=linha["NMSITUACAO"],
            unidade=linha["NMUNIDADE"] or "",
        )
        for linha in linhas
        if linha.get("NMSITUACAO") in SITUACOES_ABERTAS
    ]


def _parsear_data_publicacao(texto_item: str) -> tuple[date | None, str]:
    """Item vem como "dd/mm/aaaa - Título" — separa data e título."""
    m = re.match(r"(\d{2}/\d{2}/\d{4})\s*-\s*(.+)", texto_item.strip())
    if not m:
        return None, texto_item.strip()
    try:
        data = datetime.strptime(m.group(1), "%d/%m/%Y").date()
    except ValueError:
        data = None
    return data, m.group(2).strip()


def listar_publicacoes(url_prefeitura: str, cd_proc_seletivo: int) -> list[Publicacao]:
    resposta = requests.get(
        url_prefeitura.rstrip("/") + f"/proc_sel_vis.aspx?cd={cd_proc_seletivo}",
        headers={"User-Agent": USER_AGENT},
        timeout=20,
        allow_redirects=True,
    )
    resposta.raise_for_status()
    ashx = _extrair_ashx(resposta.text, "proc_sel_vis")
    if not ashx:
        return []

    corpo = {"cdProcSeletivo": str(cd_proc_seletivo), "dmOpcaoExibicao": "1"}
    texto = _chamar_ajaxpro(url_prefeitura, ashx, "GetPublicacoes", corpo)
    try:
        html_lista = json.loads(_remover_comentario_final(texto))
    except json.JSONDecodeError:
        return []
    if not html_lista:
        return []

    soup = BeautifulSoup(html_lista, "html.parser")
    publicacoes = []
    base = url_prefeitura.rstrip("/") + "/"
    for a in soup.select("a.item-editais"):
        href = a.get("href")
        if not href:
            continue
        data, titulo = _parsear_data_publicacao(a.get_text())
        publicacoes.append(Publicacao(data=data, titulo=titulo, url_pdf=base + href))
    return publicacoes
