"""Parser do Instituto Mais (`institutomais.org.br`), banca organizadora com
portfólio real de ~20 municípios/entidades de SP (Itapetininga, Jarinu,
Itapeva, Tietê, Diadema, Barueri, Santo André, Santos, entre outras) —
investigado pelo `pesquisador-fonte` em 2026-09-05, achado original: Itapeva/
SP, Médico Psiquiatra (ver `docs/investigacao_fonte_instituto_mais_2026-09-05.md`
no repo principal, fixtures reais em `docs/fixtures/instituto_mais/`). Sem
bloqueio anti-bot em nenhuma etapa testada.

Estrutura investigada — **duas plataformas coexistem**:

1. Plataforma antiga (ASP.NET, `institutomais.org.br`), ainda o único índice
   central de descoberta:
   - `GET /Concursos/ConcursosAbertos` lista TODOS os concursos numa página
     só, sem paginação, em 3 seções por `<div class="SubTituloPagina">`:
     "Próximos Concursos" (`proximo`, inscrição ainda não abriu),
     "Inscrições Abertas" (`aberta`) e "Concursos em andamento" (`andamento`,
     inscrição já fechada mas processo ainda rodando — mesmo sentido de
     nomenclatura do IMEPAM/IMAM). Só `aberta` interessa pro cron
     (`scripts/rodar_instituto_mais.py`), mesma decisão de escopo do INEPAM
     (`fontes/inepam.py`) — mas as 3 seções são expostas aqui pra não perder
     rastreabilidade, e os concursos reais com cargo médico confirmados na
     investigação (Itapeva CP 01/2026, Santa Casa SJRP PS 02/2026, Jarinu CP
     02/2025) estavam todos em "andamento"/encerrados no momento da
     investigação — a decisão de só processar "aberta" é sobre status de
     inscrição, não sobre presença de cargo médico.
   - `GET /Concursos/Detalhe/{id}` traz o nome do concurso, texto
     institucional, uma seção "EDITAIS E COMUNICADOS" (lista cronológica
     reversa — mais recente primeiro, edital original geralmente por
     último) com um link de PDF por parágrafo, e uma seção **"Quadro de
     Vagas"** com uma `<table class="Tabela2">` estruturada em HTML:
     código+nome do cargo, pré-requisito (num modal) e quantidade de vagas.

     **Achado que contradiz a investigação inicial** (que dizia "cargo só
     existe no PDF"): a tabela "Quadro de Vagas" confirmada em HTML puro
     tanto no concurso de Itapeva (Médico Psiquiatra, código 302, 2 vagas)
     quanto no de Residência Médica da Santa Casa de São José do Rio Preto
     (Cirurgia Geral/Oncológica) — **só o salário/vencimento continua
     exclusivo do PDF**, não o nome do cargo nem a quantidade de vagas. Por
     isso este módulo trata a tabela "Quadro de Vagas" como a fonte de
     verdade de QUAIS cargos existem (`listar_quadro_vagas`, garante que
     nenhuma especialidade médica é perdida mesmo se o Gemini falhar ou
     ficar sem cota — ver `scripts/rodar_instituto_mais.py`) e reserva o
     PDF/Gemini (`..gemini_pdf`) só pra completar salário/tipo de vínculo/
     datas, mesmo padrão de `inepam.listar_funcoes` (cargo em HTML, salário
     só no PDF) mas ainda mais robusto aqui por já ter a quantidade de vagas
     estruturada também.

2. Plataforma nova (Blazor, `imais.org.br/concursos/detalhesconcurso/{id}`),
   em uso pra concursos recém-abertos (ex: Itapira Edital 02/2026). A
   página é um app Blazor que não renderiza sem JS completo, mas o link do
   PDF do edital (Azure Blob Storage com SAS token) já vem embutido no HTML
   estático, então `listar_documentos_novo` funciona sem executar JS.
   **Pendência conhecida**: não há fixture real de uma página `/Concursos/
   Detalhe/{id}` (plataforma antiga) que already contenha o link "Clique
   aqui para acessar a página do Concurso Público" apontando pra essa URL
   nova — sem esse elo capturado, `scripts/rodar_instituto_mais.py` não
   segue esse link (evita inventar seletor não testado); as funções deste
   módulo pra plataforma nova ficam prontas e testadas contra a fixture
   real (`institutomais_plataforma_nova_itapira_detalhe62_com_injecao_spam.
   html`) pra quando esse elo for investigado (`pesquisador-fonte`) e
   registrado em TAREFAS.md.

   Achado à parte, não bloqueador (ver docstring do repo principal): a
   página nova tem um bloco de script injetado com 3 links invisíveis de
   SEO spam de terceiro — não interagimos com isso, só lemos HTML/PDF
   estático, nunca executamos JS da página.

Escolha do PDF do edital certo (`escolher_edital`, compartilhada pelas duas
plataformas): candidatos são documentos cujo título **começa** com "edital"
(case-insensitive, `^edital\\b` — mesmo padrão de
`fgv.encontrar_pdf_edital_principal`), excluindo os que citam "convocação"
(ex: "EDITAL DE CONVOCAÇÃO PARA A PROVA OBJETIVA" também começa com
"Edital", mas é aviso de prova, nunca tem Tabela I de cargo/salário). Título
**checado por prefixo, não substring** — achado real na fixture de Itapeva:
o comunicado "RESULTADO PROVISÓRIO DA PROVA PRÁTICA... conforme
estabelecido no Capítulo XII – Dos Recursos, do Edital no 01/2026..." cita
"Edital" no meio do texto (não no início) sem ser o documento certo; se a
checagem fosse por substring (como `inepam.escolher_edital`, que nunca bateu
nesse caso na fixture dele), teria uma chance real de escolher o documento
errado aqui. Quando há mais de 1 candidato, pega o primeiro na ordem da
página — a listagem é cronológica reversa (mais recente primeiro), então
isso favorece a versão retificada/mais completa sobre a original quando as
duas existem (achado real: Santa Casa SJRP só publicou a versão já
"Retificado" do edital, sem o original avulso).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

__all__ = [
    "BASE_URL_ANTIGA",
    "ItemListagem",
    "VagaQuadro",
    "Documento",
    "listar_concursos",
    "listar_quadro_vagas",
    "listar_documentos",
    "listar_documentos_novo",
    "escolher_edital",
    "extrair_numero_edital",
    "identificador_externo",
    "normalizar_cargo",
]

BASE_URL_ANTIGA = "https://institutomais.org.br"

#: texto exato de `<div class="SubTituloPagina">` -> status normalizado.
_SECAO_PARA_STATUS = {
    "Próximos Concursos": "proximo",
    "Inscrições Abertas": "aberta",
    "Concursos em andamento": "andamento",
}

_RE_DETALHE_ID = re.compile(r"/Concursos/Detalhe/(\d+)")
_RE_CODIGO_CARGO = re.compile(r"^(\d+)\s*-\s*(.+)$")
_RE_PDF = re.compile(r"\.pdf($|\?)", re.IGNORECASE)

#: "edital nº 04/2026", "Edital 01/2025" (sem "nº"), "EDITAL N.º 04/2025" —
#: até 20 caracteres não-dígitos entre a palavra e o número, pra cobrir
#: variações de pontuação sem ficar tão frouxo a ponto de pular pra outro
#: número qualquer no meio do título.
_RE_NUMERO_EDITAL_COM_PALAVRA = re.compile(r"edital\D{0,20}?(\d{1,4}/\d{2,4})", re.IGNORECASE)
#: fallback pra título sem a palavra "edital" (achado real: "Prefeitura de
#: Francisco Morato - Concurso Público 04/2025 - Procurador Jurídico" e
#: "Programa de Residência Médica - P. S. nº 02/2026" não usam essa
#: palavra) — primeiro padrão N/AAAA solto no título.
_RE_NUMERO_GENERICO = re.compile(r"(\d{1,4}/\d{2,4})")


@dataclass
class ItemListagem:
    concurso_id: int
    url: str
    titulo: str
    #: "proximo" | "aberta" | "andamento" — ver docstring do módulo.
    status: str


@dataclass
class VagaQuadro:
    """Uma linha da tabela HTML "Quadro de Vagas" da página de detalhe —
    fonte de verdade de quais cargos existem (ver docstring do módulo)."""

    codigo: str | None
    cargo: str
    vagas: int | None
    requisitos: str | None


@dataclass
class Documento:
    titulo: str
    url_pdf: str


def listar_concursos(html: str) -> list[ItemListagem]:
    """Lê as 3 seções de `/Concursos/ConcursosAbertos` — cada uma é uma
    `<table class="TbListConc">` logo após o `<div class="SubTituloPagina">`
    correspondente. Cada linha tem 2 `<a>` pro mesmo `/Concursos/Detalhe/
    {id}` (um só com `<img>`, sem texto; outro com o título) — pega o que
    tem texto não vazio."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for sub in soup.find_all("div", class_="SubTituloPagina"):
        status = _SECAO_PARA_STATUS.get(sub.get_text(" ", strip=True))
        if status is None:
            continue
        tabela = sub.find_next_sibling("table", class_="TbListConc")
        if tabela is None:
            continue

        for linha in tabela.find_all("tr"):
            candidatos = linha.find_all("a", href=_RE_DETALHE_ID)
            link = next((a for a in candidatos if a.get_text(strip=True)), None)
            if link is None:
                continue
            match_id = _RE_DETALHE_ID.search(link["href"])
            if match_id is None:
                continue
            titulo = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
            if not titulo:
                continue
            itens.append(
                ItemListagem(
                    concurso_id=int(match_id.group(1)),
                    url=urljoin(BASE_URL_ANTIGA + "/", link["href"]),
                    titulo=titulo,
                    status=status,
                )
            )

    return itens


def _extrair_requisitos(celula) -> str | None:
    """A célula "Pré Requisito" tem um `<div id="divModal_...">` escondido
    com 2 `<div>` filhos diretos: rótulo ("Pré Requisitos") e o texto de
    verdade — pega só o 2º."""
    modal = celula.find("div", id=re.compile(r"^divModal_"))
    if modal is None:
        return None
    filhos = modal.find_all("div", recursive=False)
    if len(filhos) < 2:
        return None
    texto = re.sub(r"\s+", " ", filhos[1].get_text(" ", strip=True)).strip()
    return texto or None


def listar_quadro_vagas(html: str) -> list[VagaQuadro]:
    """Lê a tabela "Quadro de Vagas" da página de detalhe (plataforma
    antiga) — sem lista de cargos conhecida, sem filtro por nome nem por
    quantidade de vagas (0/None ainda vira linha, mesmo precedente de
    `msconcursos.listar_vagas_html`/`ibgp.py`): PRIORIDADE #1 do produto,
    nenhuma especialidade é descartada silenciosamente. Devolve `[]` se a
    seção não existir na página (ex: layout diferente, ou concurso já
    migrado pra plataforma nova sem essa tabela — quem chama decide o que
    fazer, este módulo não adivinha)."""
    soup = BeautifulSoup(html, "html.parser")

    titulo_div = next(
        (
            div
            for div in soup.find_all("div", class_="TituloPagina")
            if "Quadro de Vagas" in div.get_text(" ", strip=True)
        ),
        None,
    )
    if titulo_div is None:
        return []
    tabela = titulo_div.find_next_sibling("table")
    if tabela is None:
        return []

    thead = tabela.find("thead")
    linhas = thead.find_next_siblings("tr") if thead else tabela.find_all("tr")

    vagas: list[VagaQuadro] = []
    for linha in linhas:
        tds = linha.find_all("td", recursive=False)
        if len(tds) < 4:
            continue

        nome_bruto = re.sub(r"\s+", " ", tds[1].get_text(" ", strip=True)).strip()
        if not nome_bruto:
            continue
        match_codigo = _RE_CODIGO_CARGO.match(nome_bruto)
        codigo, cargo = (match_codigo.group(1), match_codigo.group(2).strip()) if match_codigo else (None, nome_bruto)

        requisitos = _extrair_requisitos(tds[2])

        vagas_texto = tds[3].get_text(" ", strip=True)
        match_vagas = re.search(r"\d+", vagas_texto)
        quantidade = int(match_vagas.group(0)) if match_vagas else None

        vagas.append(VagaQuadro(codigo=codigo, cargo=cargo, vagas=quantidade, requisitos=requisitos))

    return vagas


def _extrair_titulo_documento(paragrafo) -> str:
    """O texto de cada `<p>` da seção "EDITAIS E COMUNICADOS" é sempre
    "⇒ Clique aqui para visualizar o/a <TÍTULO DO DOCUMENTO>" (âncora
    "Clique aqui" embutida no meio) — remove o texto de navegação e sobra
    só o título real."""
    texto = paragrafo.get_text(" ", strip=True).replace("\xa0", " ")
    texto = re.sub(r"clique aqui", "", texto, flags=re.IGNORECASE)
    texto = texto.replace("⇒", "")
    texto = re.sub(r"para visualizar\s+(?:o|a|os|as)\s+", "", texto, count=1, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", texto).strip()


def listar_documentos(html: str) -> list[Documento]:
    """Lê a seção "EDITAIS E COMUNICADOS" da página de detalhe (plataforma
    antiga) — um `<p>` por documento, cada um com um `<a href=".../
    ckfinder/userfiles/files/....pdf">` embutido no meio do texto (ver
    `_extrair_titulo_documento`)."""
    soup = BeautifulSoup(html, "html.parser")

    cabecalho = next(
        (p for p in soup.find_all("p") if "EDITAIS E COMUNICADOS" in p.get_text(" ", strip=True).upper()),
        None,
    )
    if cabecalho is None:
        return []
    container = cabecalho.find_parent("div")
    if container is None:
        return []

    documentos: list[Documento] = []
    for paragrafo in container.find_all("p"):
        link = paragrafo.find("a", href=_RE_PDF)
        if link is None:
            continue
        titulo = _extrair_titulo_documento(paragrafo)
        if not titulo:
            continue
        documentos.append(Documento(titulo=titulo, url_pdf=urljoin(BASE_URL_ANTIGA, link["href"])))

    return documentos


def listar_documentos_novo(html: str) -> list[Documento]:
    """Lê a tabela "Arquivos" da página de detalhe da plataforma nova
    (Blazor) — `<div class="arquivos-concurso ...">` com uma tabela de
    `data | link`. O link já é a URL absoluta do Azure Blob Storage (com
    SAS token na query string, por isso o `.pdf` não é o final da URL — ver
    `_RE_PDF`), sem precisar juntar com base URL nenhuma. Ver docstring do
    módulo pra pendência de como descobrir essa URL a partir da plataforma
    antiga (ainda não usado por `scripts/rodar_instituto_mais.py`)."""
    soup = BeautifulSoup(html, "html.parser")

    documentos: list[Documento] = []
    for div in soup.find_all("div", class_=lambda c: c and "arquivos-concurso" in c.split()):
        for link in div.find_all("a", href=_RE_PDF):
            titulo = link.get_text(" ", strip=True).replace("\xa0", " ")
            titulo = re.sub(r"\s+", " ", titulo).strip()
            if not titulo:
                continue
            documentos.append(Documento(titulo=titulo, url_pdf=link["href"]))

    return documentos


def escolher_edital(documentos: list[Documento]) -> Documento | None:
    """Prioriza documento cujo título COMEÇA com "edital" (não substring —
    ver docstring do módulo pro achado real que justifica isso) e não cita
    "convocação" (aviso de prova, nunca tem Tabela I). Com mais de 1
    candidato, pega o primeiro na ordem da página (lista é cronológica
    reversa — favorece a versão mais recente/retificada)."""
    candidatos = [d for d in documentos if re.match(r"^edital\b", d.titulo, re.IGNORECASE)]
    sem_convocacao = [d for d in candidatos if "convocação" not in d.titulo.lower()]
    if sem_convocacao:
        candidatos = sem_convocacao
    if candidatos:
        return candidatos[0]
    return documentos[0] if documentos else None


def extrair_numero_edital(titulo: str) -> str | None:
    """Extrai "NN/AAAA" do título da listagem — usado como fallback quando
    o Gemini não devolve `numero_edital` (ex: PDF indisponível/sem cota,
    ver `scripts/rodar_instituto_mais.py`). Tenta primeiro perto da palavra
    "edital"; sem essa palavra (achado real: títulos de Francisco Morato e
    de Programa de Residência Médica não a usam), cai pro primeiro padrão
    N/AAAA solto no título."""
    match = _RE_NUMERO_EDITAL_COM_PALAVRA.search(titulo)
    if match:
        return match.group(1)
    match = _RE_NUMERO_GENERICO.search(titulo)
    return match.group(1) if match else None


def normalizar_cargo(cargo: str) -> str:
    """Sem acento/maiúscula — usado só pra casar o cargo do "Quadro de
    Vagas" (HTML) com o cargo devolvido pelo Gemini (PDF), que podem vir em
    capitalização diferente (ex: "MÉDICO PSIQUIATRA" no HTML de Itapeva vs.
    "Médico Psiquiatra" que o Gemini tende a devolver)."""
    sem_acento = unicodedata.normalize("NFKD", cargo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def identificador_externo(concurso_id: int, cargo: str) -> str:
    """Chave de dedup: id do concurso (único na plataforma antiga) + slug
    do cargo — mesmo padrão de `ache_concursos`/`msconcursos`."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    return f"instituto-mais-{concurso_id}-{slug_cargo}"
