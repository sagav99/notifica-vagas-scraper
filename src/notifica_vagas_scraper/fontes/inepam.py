"""Parser do INEPAM (Instituto Nacional Especializado em Pesquisa e Apoio
aos Municípios, `app.inepam.org.br`), banca organizadora que atende
prefeituras/câmaras municipais de MG e SP (e alguns outros estados, ex: MT,
SC, vistos na amostra real) — achado 2026-09-02 via notícia do Ache
Concursos sobre Embu das Artes/SP: processo seletivo com **7 especialidades
médicas** (Fisiatra, Ginecologista-Obstetra, Neurologista Infantil,
Ortopedista, Pneumologista Infantil, Psiquiatra Infantil, Ultrassonografista
— 10 vagas no total) com inscrições até 08/09/2026 (prazo curto).
Investigado pelo `pesquisador-fonte`, fixtures reais em
`docs/fixtures/inepam/` (repo principal). Sem bloqueio anti-bot em nenhuma
URL, tudo HTTP 200 direto via `requests`.

Estrutura investigada:
- GET `/home.do`: 3 seções por status ("Inscrições Abertas",
  "Em Andamento", "Finalizados"), cada uma com uma `<table>` de `<tr
  data-href="/concurso/concursoPaginaInterna.do?idInstituicao=<N>
  &idConcurso=<M>">` — o texto do processo (município, UF, órgão, tipo,
  número do edital) vem só como texto solto dentro de um `<span>` na
  célula (`data-descricao` no atributo da linha só existe na seção
  "Inscrições Abertas" da home, NÃO na página "Ver mais" de "Em
  Andamento" nem seria seguro depender dele — por isso este parser lê
  sempre o `<span>` de texto da célula, nunca o atributo).
- GET `/concurso/concursosEmAndamento.do`: mesma estrutura de linha, só
  que sem seção/status — usado só se o chamador já souber que é a lista
  "Em Andamento" completa (ver `listar_processos_pagina`).
- GET `/concurso/concursoPaginaInterna.do?idInstituicao=<N>&idConcurso=<M>`:
  período de inscrições (`.fi-period`), tabela "Arquivos Disponíveis" (link
  de PDF do edital, sem bloqueio, path relativo tipo
  `/concurso/downloadAnexo.do?idAnexo=<N>`) e tabela "Funções Oferecidas"
  (nomes de cargo em HTML puro, SEM salário/vagas — isso só existe dentro
  do PDF do edital, precisa Gemini, mesmo padrão de FGV/Actcon/FUNDEP/IMAM).

**Formato do texto de cada linha da listagem é inconsistente** (achado
real, confirmado nas 26 linhas de "Em Andamento" + 11 de "Inscrições
Abertas" das fixtures): geralmente
"Município (sep) UF (sep) Órgão - Tipo Nº Número/Ano [- complemento]", mas
o separador entre Município e UF varia: " - " (espaço-hífen-espaço, mais
comum), "-" colado sem espaço nenhum ("Lambari-MG"), "- " só com espaço
depois ("Angatuba- SP") ou "/" em vez de hífen ("São Sebastião do Rio
Verde/MG"). `extrair_municipio_uf` tolera todas essas variações via regex
com separador `[-/]` e `\\s*` nas duas pontas.

**Nem toda linha representa um único município** (achado real): há
consórcios intermunicipais ("Água Boa - MT - Consórcio Intermunicipal de
Saúde do Médio Araguaia/MT - CISMA - ..."), conselhos regionais
("Conselho Regional de Engenharia e Agronomia do Estado de Mato Grosso -
CREA/MT - ...") e conselhos municipais que não são prefeitura/câmara
("Adamantina-SP - Conselho Municipal dos Direitos da Criança e do
Adolescente - ..."). `extrair_municipio_uf` só aceita o candidato de
município+UF quando o segmento seguinte é literalmente "Prefeitura
(Municipal)" ou "Câmara (Municipal)" (comparação exata, não substring) —
qualquer outra coisa (consórcio, conselho, sigla de autarquia) devolve
`None`, sem adivinhar, mesmo espírito de `access.extrair_municipio_uf`/
`imam.extrair_municipio`. Isso é seguro mesmo pro caso do CREA/MT: a regex
não-gulosa por si só acharia "MT" como UF (é código válido) com um
município bizarro ("Conselho Regional ... - CREA"), mas a validação do
órgão seguinte rejeita porque "Concurso Público" não é "Prefeitura"/
"Câmara".

**Decisão de escopo (`scripts/rodar_inepam.py`): só processa "Inscrições
Abertas"** — "Em Andamento" nesta banca, pelo nome da seção e por analogia
com o IMAM (outra banca mineira com nomenclatura idêntica de status, ver
`fontes/imam.py`), indica processo cuja fase de inscrição já passou
(processo seguiu pra prova/resultado); notificar usuário sobre uma vaga
cuja inscrição já fechou não serve ao produto. Este módulo ainda expõe
`listar_processos_pagina` (testado contra a fixture completa de "Em
Andamento", 26 itens reais, incluindo os casos de MG que já existem via
outras fontes como DOM/AMM-MG — ex: Pedra Dourada — overlap conhecido, não
resolvido aqui) para o caso de essa decisão ser revista depois.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

BASE_URL = "https://app.inepam.org.br"

__all__ = [
    "ItemListagem",
    "Documento",
    "BASE_URL",
    "extrair_municipio_uf",
    "extrair_tipo_numero_edital",
    "listar_processos_home",
    "listar_processos_pagina",
    "listar_documentos",
    "escolher_edital",
    "listar_funcoes",
    "extrair_periodo_inscricao",
]

#: as 27 UFs válidas — usado pra rejeitar match acidental de 2 letras
#: maiúsculas que não é UF nenhuma (ex: sigla de órgão de 2 letras).
_UFS_VALIDAS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}

#: rótulo exato (case-insensitive) do segmento logo após "Município - UF"
#: que confirma que a linha é mesmo prefeitura/câmara municipal — ver
#: docstring do módulo pro motivo de não usar substring/prefixo aqui
#: (diferente de `formatacao.separar_prefixo_orgao_municipal`, que espera
#: "Prefeitura Municipal de <cidade>"; aqui o nome da cidade já foi
#: extraído antes, então o segmento é só o rótulo do órgão sozinho).
_ORGAOS_MUNICIPAIS = {"prefeitura municipal", "prefeitura", "câmara municipal", "câmara"}

#: `.+?` não-guloso: acha a PRIMEIRA ocorrência de "(sep) UF (sep)" na
#: string inteira, não a última — importante pro caso do CREA/MT (ver
#: docstring), onde só a validação de órgão em `extrair_municipio_uf`
#: rejeita o falso-positivo.
_RE_MUNICIPIO_UF = re.compile(r"^\s*(?P<municipio>.+?)\s*[-/]\s*(?P<uf>[A-Za-z]{2})\s*[-/]\s*(?P<resto>.+)$")

_RE_TIPO_NUMERO = re.compile(
    r"(?P<tipo>Concurso P[uú]blico|Processo Seletivo(?:\s+(?:P[uú]blico|Simplificado))?)"
    r"\s*[NnºO°]+\.?\s*(?P<numero>\d+\s*/\s*\d{4})",
    re.IGNORECASE,
)

_RE_PERIODO = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")

#: id do `<div>` de cada seção da home -> status; ordem não importa.
_SECAO_PARA_STATUS = {
    "fm-section-aberta": "aberta",
    "fm-section-andamento": "andamento",
    "fm-section-finalizado": "finalizado",
}


@dataclass
class ItemListagem:
    id_instituicao: int
    id_concurso: int
    url: str
    #: texto bruto da linha (sempre lido do `<span>` de texto, nunca do
    #: atributo `data-descricao` — ver docstring do módulo).
    descricao: str
    #: `None` quando `descricao` não citar "Município (sep) UF (sep)
    #: Prefeitura/Câmara" reconhecível (consórcio, conselho, sigla de
    #: autarquia — ver `extrair_municipio_uf`).
    municipio: str | None
    uf: str | None
    #: "Prefeitura Municipal"/"Prefeitura"/"Câmara Municipal"/"Câmara" —
    #: só preenchido junto com `municipio`.
    orgao: str | None
    tipo_processo: str | None
    numero_edital: str | None
    status: str


@dataclass
class Documento:
    titulo: str
    url_pdf: str


def extrair_municipio_uf(descricao: str) -> tuple[str, str, str] | None:
    """Devolve `(municipio, uf, orgao)` se `descricao` citar claramente
    "Município (sep) UF (sep) Prefeitura/Câmara (Municipal)" no início da
    string; `None` caso contrário (consórcio, conselho, autarquia com
    sigla — ver docstring do módulo, não adivinha)."""
    match = _RE_MUNICIPIO_UF.match(descricao)
    if not match:
        return None

    uf = match.group("uf").upper()
    if uf not in _UFS_VALIDAS:
        return None

    resto = match.group("resto")
    orgao_bruto = re.split(r"\s*[-/]\s*", resto, maxsplit=1)[0].strip()
    if orgao_bruto.lower() not in _ORGAOS_MUNICIPAIS:
        return None

    municipio = re.sub(r"\s+", " ", match.group("municipio")).strip()
    if not municipio:
        return None

    return municipio, uf, orgao_bruto


def extrair_tipo_numero_edital(descricao: str) -> tuple[str, str] | None:
    """Devolve `(tipo_processo, numero_edital)` a partir de QUALQUER ponto
    de `descricao` (não só do início — "Nº"/"N°" às vezes aparece bem
    depois do órgão). `numero_edital` sempre normalizado sem espaços
    internos ("001/2026")."""
    match = _RE_TIPO_NUMERO.search(descricao)
    if not match:
        return None
    tipo = re.sub(r"\s+", " ", match.group("tipo")).strip()
    numero = re.sub(r"\s+", "", match.group("numero"))
    return tipo, numero


def _extrair_id_instituicao_concurso(href: str) -> tuple[int, int] | None:
    qs = parse_qs(urlparse(href).query)
    try:
        return int(qs["idInstituicao"][0]), int(qs["idConcurso"][0])
    except (KeyError, IndexError, ValueError):
        return None


def _parsear_linha(linha, status: str) -> ItemListagem | None:
    href = linha.get("data-href")
    if not href:
        return None
    ids = _extrair_id_instituicao_concurso(href)
    if ids is None:
        return None
    id_instituicao, id_concurso = ids

    spans = linha.find_all("span")
    if len(spans) < 2:
        return None
    # o último span é sempre o texto do processo (o(s) anterior(es) são a
    # setinha decorativa "›"/glyphicon) — mesma posição em "Inscrições
    # Abertas" (classe `fm-contest-link`) e na página "Em Andamento"
    # (classe `fm-row-text`), então não precisamos depender do nome da
    # classe.
    descricao = spans[-1].get_text(" ", strip=True)
    if not descricao:
        return None

    resolvido_municipio = extrair_municipio_uf(descricao)
    municipio, uf, orgao = resolvido_municipio if resolvido_municipio else (None, None, None)

    resolvido_tipo = extrair_tipo_numero_edital(descricao)
    tipo_processo, numero_edital = resolvido_tipo if resolvido_tipo else (None, None)

    return ItemListagem(
        id_instituicao=id_instituicao,
        id_concurso=id_concurso,
        url=f"{BASE_URL}/concurso/concursoPaginaInterna.do?idInstituicao={id_instituicao}&idConcurso={id_concurso}",
        descricao=descricao,
        municipio=municipio,
        uf=uf,
        orgao=orgao,
        tipo_processo=tipo_processo,
        numero_edital=numero_edital,
        status=status,
    )


def listar_processos_home(html: str) -> list[ItemListagem]:
    """Lê as 3 seções da home (`/home.do`) — "Inscrições Abertas" (id
    `fm-section-aberta`), "Em Andamento" (`fm-section-andamento`) e
    "Finalizados" (`fm-section-finalizado`). A home só mostra uma AMOSTRA
    de "Em Andamento"/"Finalizados" (tem link "Ver mais" pra lista
    completa) — ver `listar_processos_pagina` pra essas."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for secao_id, status in _SECAO_PARA_STATUS.items():
        secao = soup.find(id=secao_id)
        if secao is None:
            continue
        for linha in secao.select("tr[data-href]"):
            item = _parsear_linha(linha, status)
            if item is not None:
                itens.append(item)

    return itens


def listar_processos_pagina(html: str, status: str) -> list[ItemListagem]:
    """Lê a lista completa de uma página tipo `/concurso/
    concursosEmAndamento.do` (uma única tabela, sem seção por status —
    `status` é passado pelo chamador porque já sabe qual página buscou)."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []
    for linha in soup.select("tr[data-href]"):
        item = _parsear_linha(linha, status)
        if item is not None:
            itens.append(item)
    return itens


def _achar_painel_por_titulo(soup: BeautifulSoup, titulo: str):
    """`h3.string` não serve aqui: o `<h3>` tem um `<span>` de ícone ANTES
    do texto do título (`<h3><span class="glyphicon .../></span>Título
    </h3>`), então `Tag.string` é `None` (mais de um filho) — precisa
    comparar pelo texto completo (`get_text`), não pelo `.string`."""
    for h3 in soup.find_all("h3"):
        if titulo in h3.get_text(" ", strip=True):
            return h3.find_parent("div", class_="panel")
    return None


def listar_documentos(html: str) -> list[Documento]:
    """Lê a tabela "Arquivos Disponíveis" da página do concurso — o link
    de download vem em `data-href` (path relativo, ex: `/concurso/
    downloadAnexo.do?idAnexo=2711`), sem bloqueio."""
    soup = BeautifulSoup(html, "html.parser")
    painel = _achar_painel_por_titulo(soup, "Arquivos Disponíveis")
    if painel is None:
        return []

    documentos: list[Documento] = []
    for linha in painel.select("tr[data-href]"):
        spans = linha.find_all("span")
        if len(spans) < 2:
            continue
        titulo = spans[-1].get_text(" ", strip=True)
        if not titulo:
            continue
        documentos.append(Documento(titulo=titulo, url_pdf=urljoin(BASE_URL, linha["data-href"])))
    return documentos


def escolher_edital(documentos: list[Documento]) -> Documento | None:
    """Prioriza documento cujo título contenha "edital" (case-insensitive)
    e não seja "convocação" (aviso procedural, nunca tem cargo/salário) —
    mesmo padrão de `imam.escolher_edital`. **Sem data por documento nesta
    fonte** (a tabela "Arquivos Disponíveis" não expõe data, diferente do
    IMAM/FUNDEP): se houver mais de um candidato a "edital" (não visto na
    fixture real, que só tem 1 documento), pega o primeiro na ordem da
    página — assunção não validada contra caso real com retificação;
    reinvestigar se aparecer."""
    candidatos = [d for d in documentos if "edital" in d.titulo.lower()]
    sem_convocacao = [d for d in candidatos if "convocação" not in d.titulo.lower()]
    if sem_convocacao:
        candidatos = sem_convocacao
    if candidatos:
        return candidatos[0]
    return documentos[0] if documentos else None


def listar_funcoes(html: str) -> list[str]:
    """Lê a tabela "Funções Oferecidas" — nomes de cargo em HTML puro
    (sem salário/vagas, só existe no PDF do edital). Sem lista de cargos
    conhecida, sem filtro por nome — nenhuma especialidade (médica ou não)
    é descartada silenciosamente."""
    soup = BeautifulSoup(html, "html.parser")
    painel = _achar_painel_por_titulo(soup, "Funções Oferecidas")
    if painel is None:
        return []

    funcoes: list[str] = []
    for linha in painel.select("tr"):
        span = linha.find("span")
        if span is None:
            continue
        texto = span.get_text(" ", strip=True)
        if texto:
            funcoes.append(texto)
    return funcoes


def extrair_periodo_inscricao(html: str) -> tuple[date | None, date | None]:
    """Lê "Período de inscrições: DD/MM/AAAA a DD/MM/AAAA" do `<p
    class="fi-period">`. `(None, None)` se não achar o padrão."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("p", class_="fi-period")
    if tag is None:
        return None, None
    match = _RE_PERIODO.search(tag.get_text(" ", strip=True))
    if not match:
        return None, None

    def _parse(texto: str) -> date:
        dia, mes, ano = texto.split("/")
        return date(int(ano), int(mes), int(dia))

    return _parse(match.group(1)), _parse(match.group(2))
