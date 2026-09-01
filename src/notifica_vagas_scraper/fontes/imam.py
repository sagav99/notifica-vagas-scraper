"""Parser do IMAM (Instituto Mineiro de Assessoria Municipal), banca
organizadora de concursos/processos seletivos que atende prefeituras e
câmaras municipais de MG (achado 2026-09-01, investigação via
`curl`/Python puro — sinal trazido pelo usuário, "Prioridade alta" em
TAREFAS.md, comparável em estrutura à IMESO).

Estrutura investigada (fixtures em `tests/fixtures/imam/`):
- GET `/sitenoticia/processo_seletivo.aspx` (a home redireciona pra lá)
  lista TODOS os processos numa única página, sem paginação nem AJAX —
  4 grades ASP.NET GridView já vêm prontas no HTML inicial:
  `gridNovos` (recém-publicado), `gridInscricao` (inscrições abertas),
  `gridAndamento` (inscrições encerradas, processo em curso) e
  `gridConcluidos` (fora de interesse). Cada linha tem entidade+tipo/
  número combinados numa string só ("PREFEITURA MUNICIPAL DE X -
  CONCURSO EDITAL 001/2026"), período de isenção/inscrição e o id do
  processo (via `onclick="location.href='processo_seletivo_detalhes.
  aspx?id=<hash>'"`).
- GET `/sitenoticia/processo_seletivo_detalhes.aspx?id=<hash>` tem status
  clone da listagem, período de provas/comprovante e uma grade de
  documentos (`gridDocumentosTodos`) com título + data de publicação +
  link direto de PDF (sem bloqueio, sem query string opaca) — mesmo
  padrão de "cargo/salário só dentro do PDF" da Actcon/FGV/WordPress,
  precisa Gemini (não tem campo estruturado tipo `<div id="vagas">` da
  IMESO). Documentos vêm ordenados por data decrescente (mais recente
  primeiro) — confirmado comparando as datas de publicação da fixture.
Sem bloqueio anti-bot em nenhuma etapa; sem CSRF/sessão pra essas duas
páginas (diferente da busca por formulário do Kingpage, que exige
token de sessão — não usada aqui).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.imam.org.br/sitenoticia"

#: rótulo de status por id de grid — só "Novo" e "Inscrições Abertas"
#: interessam pra detectar vaga nova; "Em andamento" já teve a inscrição
#: encerrada (processo seguindo pra prova/resultado) e "Concluídos" já
#: foi decidido.
_GRID_PARA_STATUS = {
    "gridNovos": "Novo",
    "gridInscricao": "Inscrições Abertas",
    "gridAndamento": "Em andamento (inscrições encerradas)",
    "gridConcluidos": "Concluído",
}

_PREFIXOS_ENTIDADE = (
    "PREFEITURA MUNICIPAL DE ",
    "PREFEITURA DE ",
    "CÂMARA MUNICIPAL DE ",
    "CÂMARA DE ",
)


@dataclass
class ItemListagem:
    processo_id: str
    url: str
    entidade: str
    titulo_processo: str
    status: str


@dataclass
class Documento:
    titulo: str
    data: datetime | None
    url_pdf: str


def _parsear_data_hora(texto: str) -> datetime | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None


def extrair_municipio(entidade: str) -> str | None:
    """`None` pra entidade que não é prefeitura/câmara (ex: consórcio
    intermunicipal, que não mapeia 1:1 pra um município) — chamador decide
    pular."""
    alvo = entidade.strip().upper()
    for prefixo in _PREFIXOS_ENTIDADE:
        if alvo.startswith(prefixo):
            return entidade.strip()[len(prefixo):].strip().title()
    return None


def listar_processos(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for grid_id, status in _GRID_PARA_STATUS.items():
        tabela = soup.find(id=f"ContentPlaceHolder1_{grid_id}")
        if tabela is None:
            continue

        for linha in tabela.find_all("tr", onclick=True):
            match_id = re.search(r"id=([A-Za-z0-9]+)", linha.get("onclick", ""))
            if not match_id:
                continue

            celula = linha.find("td")
            if celula is None:
                continue
            texto_titulo = celula.contents[0].strip() if celula.contents else ""
            entidade, _, titulo_processo = texto_titulo.partition(" - ")
            if not titulo_processo:
                entidade, titulo_processo = texto_titulo, ""

            itens.append(
                ItemListagem(
                    processo_id=match_id.group(1),
                    url=f"{BASE_URL}/processo_seletivo_detalhes.aspx?id={match_id.group(1)}",
                    entidade=entidade.strip(),
                    titulo_processo=titulo_processo.strip(),
                    status=status,
                )
            )

    return itens


def listar_documentos(html: str, url_pagina: str) -> list[Documento]:
    """`url_pagina` é a URL de `processo_seletivo_detalhes.aspx` que gerou
    o `html` — os links de PDF vêm relativos (`../documentos/x.pdf`),
    resolvidos a partir dela, não de `BASE_URL`."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find(id="ContentPlaceHolder1_gridDocumentosTodos")
    if tabela is None:
        return []

    documentos: list[Documento] = []
    vistos: set[str] = set()
    for linha in tabela.find_all("tr"):
        link = linha.find("a", class_="grid_link", href=True)
        if link is None or link["href"] in vistos:
            continue
        vistos.add(link["href"])

        celula_data = linha.find("td", class_="data_tables")
        documentos.append(
            Documento(
                titulo=link.get_text(strip=True),
                data=_parsear_data_hora(celula_data.get_text()) if celula_data else None,
                url_pdf=urljoin(url_pagina, link["href"]),
            )
        )

    return documentos


def escolher_edital(documentos: list[Documento]) -> Documento | None:
    """Documentos vêm ordenados por data decrescente — o 1º título que
    contém "edital" já é a versão mais recente (ex: "Edital com
    alterações da retificação nº 01" antes do "Edital 001/2026" original),
    sem precisar comparar datas manualmente."""
    for documento in documentos:
        if "edital" in documento.titulo.lower():
            return documento
    return documentos[0] if documentos else None
