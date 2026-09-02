"""Parser da plataforma Kingpage ("Fábrica de Software", kingpage.com.br),
vendor de portal municipal identificado pelo texto "Kingpage" no rodapé
(achado 2026-09-01/02, investigação via `curl`/Python puro, sem
`pesquisador-fonte`). 3 tenants confirmados de ponta a ponta, todos em
SP, sem bloqueio nenhum: Itapetininga, Tupã, Cajati (ver
`municipios_kingpage.csv`).

Mecanismo (mesmo nos 3 tenants), HTML server-rendered, sem JS necessário:

1. Listagem por categoria: GET
   `{url_prefeitura}/concurso/categoria/{id}/{slug}/page/{N}`. Categorias
   confirmadas: 24="Processo Seletivo" e 25="Concurso" nos 3 tenants;
   Cajati tem categorias extras por ano (28 a 38 citadas na investigação,
   só a 38/"2026-processo-seletivo" confirmada por fixture — ver
   `CATEGORIAS_EXTRAS_POR_MUNICIPIO`). A listagem "oficial" sem categoria
   (`/concurso`) é protegida por CSRF preso à sessão e sempre dá 302 — não
   usar. Cada linha da tabela linka pra
   `/concurso/detalhe/{id}/{slug-decorativo}/` — só o `{id}` importa.
2. Detalhe: GET `{url_prefeitura}/concurso/detalhe/{id}/{slug}/`. Não tem
   cargo/salário/valores em R$ no HTML (confirmado por grep negativo nas
   fixtures) — só um resumo do objeto e a lista de "Arquivos" (data +
   nome do documento + link de download). Cargo/salário só existem no PDF
   do edital de abertura — mesmo padrão de Actcon/FGV/JCM, extração via
   Gemini (`gemini_pdf.py`).
3. Download do PDF: GET `{url_prefeitura}/concurso/download/{doc_id}/`
   redireciona (302) pro PDF real — `requests` já segue redirect por
   padrão, não precisa resolver a URL final manualmente.

**Escolha do edital certo entre os documentos listados** (achado real,
confirmado nos 3 tenants): cada processo tem vários documentos com
"Edital" no título — o de abertura (que tem cargo/salário/requisitos) é
sempre o mais antigo entre os que sobram depois de excluir homologação/
convocação/gabarito/resultado/classificação (esses reaproveitam a palavra
"Edital" no título mas não têm o conteúdo original) — ver
`escolher_edital`, confirmado batendo com o PDF real dos 3 exemplos de
fixture (Cajati/Médico, Itapetininga/PSS 07-2025, Tupã/PSS 002-2024).

**Descoberta de "o que é novo" (decisão registrada aqui, ver
TAREFAS.md)**: a paginação por categoria NÃO tem ordenação confiável
entre páginas (a última página pode ter IDs mais baixos que a primeira) —
não dá pra usar sozinha pra achar só o que mudou desde a última execução.
A alternativa mais cara (varredura sequencial de ID via
`/concurso/detalhe/{id}/`, usando 302 como sinal de "não existe") reprocessa
a faixa inteira de IDs a cada execução. Decisão: escanear as primeiras
`PAGINAS_POR_CATEGORIA` páginas de cada categoria relevante por município a
cada execução (mesmo custo, mais simples) e deixar o dedup normal do banco
(`identificador_externo` único por fonte+processo+cargo, ver
`db.inserir_vaga_com_evidencia`) evitar duplicar o que já foi processado —
mesmo padrão de custo já aceito por Actcon/JCM (que também rechamam Gemini
em cima de processos já vistos em execuções anteriores, dedup é sempre
pós-Gemini, não pré-fetch).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources

from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"

#: categorias confirmadas nos 3 tenants (id, slug) — ver docstring do módulo.
CATEGORIAS_PADRAO: list[tuple[int, str]] = [
    (24, "processo-seletivo"),
    (25, "concurso"),
]

#: categorias extras por município (chave = `MunicipioKingpage.nome`), além
#: das `CATEGORIAS_PADRAO`. Cajati tem 1 categoria por ano (28 a 38 citadas
#: na investigação, "2020 Processo Seletivo" até "2026 Processo Seletivo")
#: — só a 38 tem slug confirmado por fixture; as demais (28-37) não foram
#: adicionadas aqui até confirmação individual do slug (pendência registrada
#: em TAREFAS.md), pra não arriscar bater numa URL errada silenciosamente.
CATEGORIAS_EXTRAS_POR_MUNICIPIO: dict[str, list[tuple[int, str]]] = {
    "Cajati": [(38, "2026-processo-seletivo")],
}

#: quantas páginas por categoria escanear a cada execução — ver docstring.
PAGINAS_POR_CATEGORIA = 3

#: palavras que, aparecendo no título de um documento com "edital", indicam
#: que ele NÃO é o edital de abertura original (resultado/convocação/etc,
#: sem cargo-salário-requisitos completos) — ver `escolher_edital`.
_PALAVRAS_QUE_DESQUALIFICAM_EDITAL = (
    "homologa",
    "convoca",
    "gabarito",
    "resultado",
    "classifica",
)


@dataclass
class MunicipioKingpage:
    codigo_ibge: int
    nome: str
    uf: str
    url_prefeitura: str


@dataclass
class ItemListagem:
    processo_id: int
    url: str
    numero_ano: str
    modalidade: str
    objeto: str


@dataclass
class Documento:
    titulo: str
    data: date | None
    url_pdf: str


def listar_municipios_kingpage() -> list[MunicipioKingpage]:
    caminho = resources.files("notifica_vagas_scraper.dados").joinpath("municipios_kingpage.csv")
    with caminho.open("r", encoding="utf-8", newline="") as f:
        return [
            MunicipioKingpage(
                codigo_ibge=int(linha["codigo_ibge"]),
                nome=linha["nome"],
                uf=linha["uf"],
                url_prefeitura=linha["url_prefeitura"],
            )
            for linha in csv.DictReader(f)
        ]


def categorias_do_municipio(nome_municipio: str) -> list[tuple[int, str]]:
    """Categorias a escanear pra um município: as padrão (24/25) mais
    qualquer extra confirmada especificamente pra ele (ver
    `CATEGORIAS_EXTRAS_POR_MUNICIPIO`)."""
    return [*CATEGORIAS_PADRAO, *CATEGORIAS_EXTRAS_POR_MUNICIPIO.get(nome_municipio, [])]


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _achar_tabela_por_cabecalho(html: str, texto_cabecalho: str):
    soup = BeautifulSoup(html, "html.parser")
    for tabela in soup.find_all("table"):
        cabecalho = tabela.find("thead")
        if cabecalho and texto_cabecalho.upper() in cabecalho.get_text().upper():
            return tabela
    return None


def listar_processos_categoria(html: str, url_prefeitura: str) -> list[ItemListagem]:
    """Parseia 1 página de
    `/concurso/categoria/<id>/<slug>/page/<N>` — tabela com colunas
    Nº/ANO, MODALIDADE, OBJETO, DATA DA DISPUTA, DETALHES (link)."""
    tabela = _achar_tabela_por_cabecalho(html, "OBJETO")
    if tabela is None:
        return []

    corpo = tabela.find("tbody")
    if corpo is None:
        return []

    base = url_prefeitura.rstrip("/")
    itens: list[ItemListagem] = []
    for linha in corpo.find_all("tr"):
        celulas = linha.find_all("td")
        if len(celulas) < 5:
            continue
        link = celulas[4].find("a", href=True)
        if link is None:
            continue
        match_id = re.match(r"^/concurso/detalhe/(\d+)/", link["href"])
        if not match_id:
            continue
        processo_id = int(match_id.group(1))
        itens.append(
            ItemListagem(
                processo_id=processo_id,
                # slug é decorativo (ver docstring do módulo) — reconstrói
                # a URL só com o id, evita arrastar HTML quebrado que às
                # vezes vaza pro slug (ex: tag <span style=...> sem escape).
                url=f"{base}/concurso/detalhe/{processo_id}/edital/",
                numero_ano=celulas[0].get_text(strip=True),
                modalidade=celulas[1].get_text(strip=True),
                objeto=celulas[2].get_text(strip=True),
            )
        )
    return itens


def listar_documentos(html: str, url_prefeitura: str) -> list[Documento]:
    """Parseia a tabela "Arquivos" da página de detalhe: colunas Data,
    Nome do documento, Download."""
    tabela = _achar_tabela_por_cabecalho(html, "Nome do documento")
    if tabela is None:
        return []

    base = url_prefeitura.rstrip("/")
    documentos: list[Documento] = []
    for linha in tabela.find_all("tr"):
        celulas = linha.find_all("td")
        if len(celulas) != 3:
            continue
        link = celulas[2].find("a", href=True)
        if link is None:
            continue
        documentos.append(
            Documento(
                titulo=celulas[1].get_text(strip=True),
                data=_parsear_data(celulas[0].get_text()),
                url_pdf=base + link["href"],
            )
        )
    return documentos


def escolher_edital(documentos: list[Documento]) -> Documento | None:
    """Entre os documentos de um processo, acha o edital de abertura (o
    único com cargo/salário/requisitos completos) — ver docstring do
    módulo pra achado real confirmado nos 3 exemplos de fixture.

    1. Só considera documentos com "edital" no título.
    2. Descarta os que também têm palavra de homologação/convocação/
       gabarito/resultado/classificação — reaproveitam "Edital" no título
       mas não são o edital de abertura original.
    3. Entre o que sobrar, pega o mais antigo (o de abertura é sempre
       publicado antes de qualquer retificação/homologação/convocação).
    4. Se nenhum documento tiver "edital" no título, cai pro mais antigo
       de todos (mesmo critério de fallback do Actcon)."""
    if not documentos:
        return None

    candidatos = [d for d in documentos if "edital" in d.titulo.lower()]
    qualificados = [
        d
        for d in candidatos
        if not any(palavra in d.titulo.lower() for palavra in _PALAVRAS_QUE_DESQUALIFICAM_EDITAL)
    ]
    if qualificados:
        candidatos = qualificados
    if not candidatos:
        candidatos = documentos

    return min(candidatos, key=lambda d: d.data or date.max)
