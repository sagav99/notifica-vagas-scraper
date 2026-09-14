"""Parser do Instituto Consulplan (`institutoconsulplan.org.br`), banca
organizadora de concursos/processos seletivos que atende prefeituras,
câmaras e autarquias, em vários estados (MG/SP no escopo do projeto).
Achado pela checagem externa periódica no Google (2026-09-13), investigado
a fundo pelo `pesquisador-fonte` — fixtures reais em
`docs/fixtures/institutoconsulplan/` (repo principal). Ver TAREFAS.md,
item "Instituto Consulplan", para o achado completo.

Sem bloqueio real reproduzido na investigação (Cloudflare aparece no
header mas sem desafio JS/captcha em ~35 requisições — `requests`/`curl`
puro resolve tudo, ASP.NET server-rendered, sem SPA). PDF do edital é
texto real, extração limpa via `pypdf`, sem OCR necessário.

**Hub de descoberta: `sitemap.xml`** (47 URLs "vivas" na investigação),
não `/Concursos` (~296 links, maioria hash histórico opaco/encerrado).
Cada cliente tem 1 página em `/<slug>` — o slug NÃO segue regex fixo
(`pref-alvinopolis2026`, `prefalagoa2026` sem hífen, `unai2026` sem
prefixo "pref", `saaeb26` ano de 2 dígitos) — tratado como string opaca,
nunca usado pra extrair município/ano. `listar_urls_clientes` só exclui
o pequeno conjunto FIXO de URLs institucionais conhecidas (home,
/Concursos, /Institucional etc); tudo que sobra é candidato a cliente,
mas só é confirmado como MG/SP depois de baixar a página e ler o
`<title>` (formato estável: `"<Órgão> de <Cidade>/<UF> — Instituto
Consulplan"`, dá UF/cidade de graça sem geocoding) via
`identificar_cliente` — página institucional ou fora do padrão de título
devolve `None` (não precisa ser listada à mão em nenhum lugar).

**Página de cliente sem publicação ainda** (achado real: IPREMB/Betim-MG,
4 clientes MG novos em fase pré-lançamento) mostra só o texto "Este
concurso ainda não possui nenhuma publicação." em vez da tabela de
documentos — `pagina_sem_publicacao` detecta isso pra quem chama pular
sem erro (não é falha, é concurso que ainda não tem edital).

**Tabela de documentos** (`<table class="table table-hover">`, sem
`<thead>` — diferente de Kingpage/IBGP que têm cabeçalho pra identificar
a tabela certa; aqui só existe 1 tabela na página) tem 1 linha por
documento, cada linha com: [0] título do documento (texto do link, ex:
"Edital nº 1/2026 - Abertura", "Retificação I", "Análise preliminar dos
pedidos de isenção - Deferidos"), [1] data de publicação (`DD/MM/AAAA`,
também como link pro mesmo PDF), [2]/[3] ícones de visualização/áudio
(mesmo PDF, sem dado novo). Linhas vêm em ordem cronológica ascendente
nas fixtures reais, mas `escolher_edital_abertura` não depende disso —
escolhe por conteúdo do título ("edital" + "abertura") e usa a data mais
antiga como desempate, mesmo padrão de `kingpage.escolher_edital`.
**Sem cargo/salário em texto na própria página** (achado da triagem,
confirmado na investigação completa) — cargo só existe dentro do PDF do
edital, extração 100% via Gemini (mesmo padrão de FGV/Actcon/JCM/
Kingpage: sem fonte estruturada em HTML pra cargo aqui, diferente de
IBGP/Instituto Mais).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"

_NS_SITEMAP = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

#: URLs institucionais fixas do sitemap.xml (achado real, ver docstring do
#: módulo) — nunca são cliente, excluídas antes mesmo de baixar a página.
#: Qualquer outra URL do sitemap é candidata, confirmada ou não depois via
#: `identificar_cliente` (título fora do padrão -> `None`, sem precisar
#: manter essa lista atualizada pra excluir cliente de outro estado).
URLS_INSTITUCIONAIS = {
    "https://www.institutoconsulplan.org.br",
    "https://www.institutoconsulplan.org.br/Concursos",
    "https://www.institutoconsulplan.org.br/Institucional",
    "https://www.institutoconsulplan.org.br/TrabalheConosco",
    "https://www.institutoconsulplan.org.br/Certificados",
    "https://www.institutoconsulplan.org.br/Projetos",
    "https://www.institutoconsulplan.org.br/PoliticaCookies",
    "https://www.institutoconsulplan.org.br/PoliticaPrivacidade",
}

_TEXTO_SEM_PUBLICACAO = "este concurso ainda não possui nenhuma publicação"

#: `"<orgao completo>"` antes do separador final `"— Instituto Consulplan"`
#: (ou `"- Instituto Consulplan"`, hífen simples, por segurança) do
#: `<title>` da página do cliente.
_PADRAO_TITULO = re.compile(r"^(?P<orgao_completo>.+?)\s*(?:—|-)\s*Instituto Consulplan\s*$")

#: `"<orgao> de <Cidade>/<UF>"` dentro do `orgao_completo` acima — grupo do
#: órgão é GREEDY de propósito: quando o texto tem mais de uma ocorrência
#: de " de " (ex: "IPREMB - Instituto de Previdência Social do Município
#: de Betim/MG"), o backtracking do regex acha a ÚLTIMA ocorrência antes
#: de "<Cidade>/<UF>", que é sempre a divisória certa nos casos reais
#: confirmados (nenhum município do escopo tem " de " no próprio nome).
_PADRAO_ORGAO_CIDADE_UF = re.compile(r"^(?P<orgao>.+)\s+de\s+(?P<cidade>[^/]+)/(?P<uf>[A-Z]{2})$")

_PADRAO_NUMERO_EDITAL = re.compile(r"n[ºo°]?\s*(\d+[/\-]\d{2,4})", re.IGNORECASE)


@dataclass
class Cliente:
    url: str
    slug: str
    #: texto completo antes de "— Instituto Consulplan" no `<title>", ex:
    #: "Prefeitura Municipal de Bálsamo/SP" — usado como `orgao` de
    #: fallback quando o Gemini não extrai um nome de órgão do PDF.
    orgao: str
    cidade: str
    uf: str


@dataclass
class Documento:
    titulo: str
    data: date | None
    url_pdf: str


def listar_urls_sitemap(xml: str) -> list[str]:
    """`<loc>` de cada `<url>` do `sitemap.xml`, na ordem em que aparecem."""
    raiz = ET.fromstring(xml)
    return [el.text.strip() for el in raiz.findall("sm:url/sm:loc", _NS_SITEMAP) if el.text and el.text.strip()]


def listar_urls_clientes(xml: str) -> list[str]:
    """URLs do sitemap.xml que são candidatas a página de cliente — só
    exclui o conjunto fixo institucional conhecido (ver
    `URLS_INSTITUCIONAIS`); slug de cliente é opaco, não há regex de
    inclusão. Confirmação de MG/SP de fato só acontece depois de baixar
    a página, via `identificar_cliente`."""
    return [url for url in listar_urls_sitemap(xml) if url not in URLS_INSTITUCIONAIS]


def slug_do_cliente(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def identificar_cliente(html: str, url: str) -> Cliente | None:
    """Lê o `<title>` da página do cliente (formato `"<Órgão> de
    <Cidade>/<UF> — Instituto Consulplan"`, ver docstring do módulo).
    Devolve `None` quando o título não segue esse padrão — cobre tanto
    página institucional (título é só "Instituto Consulplan", sem
    separador antes) quanto qualquer formato inesperado; quem chama
    decide como tratar (`rodar_institutoconsulplan.py` só segue adiante
    com cliente identificado E `uf` em MG/SP)."""
    soup = BeautifulSoup(html, "html.parser")
    titulo = soup.title.string if soup.title and soup.title.string else None
    if not titulo:
        return None

    match_titulo = _PADRAO_TITULO.match(titulo.strip())
    if not match_titulo:
        return None

    orgao_completo = match_titulo.group("orgao_completo").strip()
    match_local = _PADRAO_ORGAO_CIDADE_UF.match(orgao_completo)
    if not match_local:
        return None

    return Cliente(
        url=url,
        slug=slug_do_cliente(url),
        orgao=orgao_completo,
        cidade=match_local.group("cidade").strip(),
        uf=match_local.group("uf").strip(),
    )


def pagina_sem_publicacao(html: str) -> bool:
    """`True` quando a página do cliente ainda não tem nenhum documento
    publicado (concurso anunciado mas sem edital ainda) — achado real:
    IPREMB/Betim-MG e outros 3 clientes MG novos achados via sitemap
    estavam nesse estado na investigação. Não é falha, é caso a pular."""
    soup = BeautifulSoup(html, "html.parser")
    texto = " ".join(soup.get_text().split()).lower()
    return _TEXTO_SEM_PUBLICACAO in texto


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def listar_documentos(html: str) -> list[Documento]:
    """Parseia a única tabela de documentos da página do cliente — sem
    `<thead>` (diferente de Kingpage/IBGP), então não dá pra achar por
    cabeçalho; usa a primeira (e única) `<table>` da página. Cada linha:
    [0] título+link do PDF, [1] data `DD/MM/AAAA`+link, [2]/[3] ícones
    (ignorados, mesmo PDF)."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table")
    if tabela is None:
        return []

    corpo = tabela.find("tbody") or tabela
    documentos: list[Documento] = []
    for linha in corpo.find_all("tr"):
        celulas = linha.find_all("td")
        if len(celulas) < 2:
            continue
        link_titulo = celulas[0].find("a", href=True)
        if link_titulo is None:
            continue
        titulo = link_titulo.get_text(strip=True)
        if not titulo:
            continue
        documentos.append(
            Documento(
                titulo=titulo,
                data=_parsear_data(celulas[1].get_text()),
                url_pdf=link_titulo["href"],
            )
        )
    return documentos


def escolher_edital_abertura(documentos: list[Documento]) -> Documento | None:
    """Acha o edital de ABERTURA (o único com cargo/salário/requisitos
    completos) entre os documentos de um cliente — ver docstring do
    módulo pro achado real de que o título já cita "Abertura" de forma
    confiável nas fixtures.

    1. Prioriza documento com "edital" E "abertura" no título — usa o
       mais antigo como desempate (não deveria haver mais de 1, mas
       segue o mesmo cuidado de `kingpage.escolher_edital`).
    2. Sem candidato "abertura", cai pro "edital" mais antigo (cobre
       formato de título fora do confirmado, sem quebrar).
    3. Sem nenhum "edital", cai pro documento mais antigo de todos
       (mesmo fallback final de `kingpage.escolher_edital`)."""
    if not documentos:
        return None

    candidatos_abertura = [
        d for d in documentos if "edital" in d.titulo.lower() and "abertura" in d.titulo.lower()
    ]
    if candidatos_abertura:
        return min(candidatos_abertura, key=lambda d: d.data or date.max)

    candidatos_edital = [d for d in documentos if "edital" in d.titulo.lower()]
    if candidatos_edital:
        return min(candidatos_edital, key=lambda d: d.data or date.max)

    return min(documentos, key=lambda d: d.data or date.max)


def extrair_numero_edital_do_titulo(titulo: str) -> str | None:
    """`"Edital nº 1/2026 - Abertura"` -> `"1/2026"` — usado só como
    fallback quando o Gemini não extrai `numero_edital` do PDF."""
    match = _PADRAO_NUMERO_EDITAL.search(titulo)
    return match.group(1) if match else None


def identificador_externo(slug_cliente: str, cargo: str) -> str:
    """Chave de dedup: slug do cliente (opaco, mas único e estável no
    sitemap) + cargo normalizado — mesmo padrão de `kingpage`/
    `instituto_mais` (sem id numérico de processo público neste site)."""
    slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
    return f"institutoconsulplan-{slug_cliente}-{slug_cargo}"
