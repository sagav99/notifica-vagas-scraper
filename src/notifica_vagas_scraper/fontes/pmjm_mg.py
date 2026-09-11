"""Parser da Prefeitura de João Monlevade/MG (site próprio,
`pmjm.mg.gov.br` — sem banca organizadora, seleção conduzida direto pela
Secretaria Municipal de Saúde).

Investigado pelo `pesquisador-fonte` (fixtures reais em
`docs/fixtures/pmjm_mg/` no repo principal: listagem completa de
`/concursos-publicos`, a página de detalhe do Edital 12/2026 — Médico
Plantonista Ortopedista —, o PDF escaneado do edital e o PDF de texto real
do "Resultado Preliminar" do mesmo processo, usado só pra conferência
cruzada). Achados:

- `GET /concursos-publicos` devolve, numa única página SEM paginação real
  (DataTables client-side, não AJAX), o histórico INTEIRO de processos
  seletivos/concursos desde 2011 — mais de 1000 linhas confirmadas na
  fixture. Cada linha tem: número/ano do edital (coluna "Nº/ Ano"),
  categoria ("Processos Seletivos" ou "Concurso"), súmula/título, data de
  publicação, e um link `<a href="https://www.pmjm.mg.gov.br/concursos_view/
  <id>">` pra página de detalhe — `<id>` é sequencial e estável, é a chave
  de descoberta de processo novo (`extrair_processo_id`). Sem bloqueio
  anti-bot em nenhuma parte do site, `requests` simples resolve tudo.
- Como a listagem cobre TODO o histórico (não só os vigentes), reprocessar
  cega todo item de saúde a cada cron seria caro à toa (PDF + chamada
  Gemini pra ~130 processos de saúde acumulados, repetido a cada 3 dias,
  disputando a mesma cota diária compartilhada por todas as fontes) —
  `db.listar_identificadores_processados` traz os `identificador_externo`
  já gravados pra esta fonte, e `extrair_ids_processados` decodifica o
  `<id>` de processo embutido em cada um (prefixo `pmjm-mg-<id>-`), pra
  `scripts/rodar_pmjm_mg.py` só baixar detalhe/PDF/chamar Gemini pros IDs
  genuinamente novos. Nunca é o `on conflict do nothing` de
  `inserir_vaga_com_evidencia` que faz esse corte (esse só evita duplicar
  linha, não evita o custo de rede/IA de reprocessar um item antigo).
- Cada processo normalmente tem 1 único cargo (diferente de fontes tipo
  FGV/Instituto Mais, que multiplexam dezenas de cargos no mesmo edital) —
  o risco aqui não é "descartar especialidade dentro do mesmo edital", é
  "não descobrir processo novo". `eh_cargo_saude` por isso é
  deliberadamente permissivo (lista ampla de radicais de profissão de
  saúde, incluindo o termo genérico "saúde"): prefere processar um
  processo não-médico a mais (custo: 1 chamada Gemini a mais) a arriscar
  não notificar um médico novo por keyword estreita demais.
- A página de detalhe (`concursos_view/<id>`) repete número/categoria/
  súmula/data da listagem numa tabela própria (`#tb_concursos`) e lista os
  documentos anexos numa segunda tabela (`#tb_anexos_concursos` — colunas
  Tipo/Descrição/Data/Arquivo). Achado real do Edital 12/2026: 3 anexos,
  todos com `Tipo="Edital"` (o campo "Tipo" NÃO distingue edital de
  abertura de resultado) — "Edital 12-2026 Médico Plantonista -
  ORTOPEDISTA" (09/07, o documento de abertura), "Resultado Preliminar PS
  12-2026..." (21/07) e "Resultado Final PS 12-2026..." (27/07). Só a
  DESCRIÇÃO diferencia — `escolher_pdf_edital` exclui qualquer anexo cuja
  descrição bata com resultado/homologação/classificação/convocação/
  recurso (documento que sai depois e não é fonte confiável de cargo de
  ABERTURA) e, entre os candidatos restantes, pega o de data mais recente
  (cobre o caso de uma retificação publicada depois do edital original,
  sem precisar de merge por seção como em `itamonte_mg.py` — aqui 1
  processo = 1 cargo, não há tabela de várias funções pra mesclar).
- **O PDF do Edital 12/2026 é escaneado/imagem, sem camada de texto
  extraível** (`pypdf`/`pdfplumber` devolvem string vazia pra sua única
  página) — diferente de Mauá/SP, onde o PDF tinha texto real só com
  colunas embaralhadas. Isso NÃO exige nenhum modo especial de
  `gemini_pdf`: a API do Gemini pra documento (`inline_data` com
  `mime_type="application/pdf"`, já usada por `gemini_pdf.
  extrair_vagas_de_pdf`) processa o PDF visualmente por padrão
  (renderiza cada página como imagem internamente), então já lê PDF
  escaneado sem qualquer mudança de código — só não dá pra extrair
  `taxa_inscricao`/tabela por regex determinístico como fallback (não tem
  texto pra regexar), então este parser depende 100% do Gemini pro
  conteúdo do PDF, sem nenhuma extração determinística paralela (diferente
  de `maua.extrair_salario_por_hora_uniforme`, que só existe porque o PDF
  de lá tem texto real).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

BASE_URL = "https://www.pmjm.mg.gov.br"
URL_LISTAGEM = f"{BASE_URL}/concursos-publicos"

#: fonte é dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais).
MUNICIPIO = "João Monlevade"
UF = "MG"

__all__ = [
    "BASE_URL",
    "URL_LISTAGEM",
    "MUNICIPIO",
    "UF",
    "ID_PREFIX",
    "ItemListagem",
    "Anexo",
    "DetalheProcesso",
    "listar_processos",
    "eh_cargo_saude",
    "filtrar_processos_saude",
    "extrair_processo_id",
    "extrair_detalhe",
    "escolher_pdf_edital",
    "extrair_ids_processados",
]


@dataclass
class ItemListagem:
    processo_id: int
    numero_edital: str | None
    categoria: str
    titulo: str
    data_publicacao: date | None
    url: str


@dataclass
class Anexo:
    tipo: str
    descricao: str
    data: date | None
    url: str


@dataclass
class DetalheProcesso:
    numero_edital: str | None
    titulo: str
    categoria: str
    data_publicacao: date | None
    anexos: list[Anexo]


def _parsear_data(texto: str | None) -> date | None:
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def _limpar_texto(no) -> str:
    return re.sub(r"\s+", " ", no.get_text(" ", strip=True)).strip()


def _url_absoluta(href: str) -> str:
    if href.startswith("http"):
        return href
    return BASE_URL + "/" + href.lstrip("/")


_RE_PROCESSO_ID = re.compile(r"concursos_view/(\d+)")


def extrair_processo_id(url: str) -> int | None:
    match = _RE_PROCESSO_ID.search(url)
    return int(match.group(1)) if match else None


def listar_processos(html: str) -> list[ItemListagem]:
    """Lê `/concursos-publicos`: tabela única (DataTables client-side, sem
    AJAX de paginação) com o histórico INTEIRO desde 2011 — ver docstring
    do módulo. Linha sem link reconhecível de `concursos_view/<id>` é
    ignorada (não deveria acontecer, mas não quebra o parsing das
    demais)."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table")
    if tabela is None or tabela.find("tbody") is None:
        return []

    itens: list[ItemListagem] = []
    for tr in tabela.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 9:
            continue

        link = tr.find("a", href=True)
        if link is None:
            continue
        processo_id = extrair_processo_id(link["href"])
        if processo_id is None:
            continue

        numero_edital = _limpar_texto(tds[2]) or None
        categoria = _limpar_texto(tds[3])
        # achado real: 1 linha antiga (processo 277, 2012) tem súmula
        # vazia de verdade na fonte — mantém a linha (título="") em vez
        # de descartar, mesmo padrão de `itamonte_mg.listar_documentos`
        # (item sem data reconhecível ainda entra na lista, não descarta
        # por falha de campo); `eh_cargo_saude("")` já é False sozinho, o
        # corte acontece em `filtrar_processos_saude`, nunca aqui.
        titulo = _limpar_texto(tds[4])
        data_publicacao = _parsear_data(tds[5].get_text(strip=True))

        itens.append(
            ItemListagem(
                processo_id=processo_id,
                numero_edital=numero_edital,
                categoria=categoria,
                titulo=titulo,
                data_publicacao=data_publicacao,
                url=_url_absoluta(link["href"]),
            )
        )

    return itens


#: deliberadamente permissivo — ver docstring do módulo pro motivo
#: ("não descobrir processo novo" é o risco real aqui, não "processar
#: cargo não-saúde a mais"). Inclui "saude"/"saúde" cru porque vários
#: títulos citam só "Secretaria Municipal de Saúde" sem nomear o cargo.
_RE_CARGO_SAUDE = re.compile(
    r"m[eé]dic[oa]?s?\b"
    r"|enfermeir"
    r"|fisioterapeut"
    r"|psic[oó]log"
    r"|nutricionist"
    r"|fonoaudi[oó]log"
    r"|odontolog"
    r"|dentist"
    r"|farmac[eê]utic"
    r"|assistente\s+social"
    r"|biom[eé]dic"
    r"|veterin[aá]ri"
    r"|terapeuta\s+ocupacional"
    r"|agente\s+comunit[aá]rio\s+de\s+sa[uú]de"
    r"|sa[uú]de",
    re.IGNORECASE,
)


def eh_cargo_saude(titulo: str) -> bool:
    return bool(_RE_CARGO_SAUDE.search(titulo))


def filtrar_processos_saude(itens: list[ItemListagem]) -> list[ItemListagem]:
    return [item for item in itens if eh_cargo_saude(item.titulo)]


def extrair_detalhe(html: str) -> DetalheProcesso:
    """Lê `concursos_view/<id>`: tabela de cabeçalho (`#tb_concursos` —
    Nº/Ano, Tipo, Súmula, Data) + tabela de anexos (`#tb_anexos_concursos`
    — Tipo, Descrição, Data, Arquivo). Campo de cabeçalho ausente vira
    string vazia/`None`, nunca derruba o parsing."""
    soup = BeautifulSoup(html, "html.parser")

    campos: dict[str, str] = {}
    tabela_cabecalho = soup.find("table", id="tb_concursos")
    if tabela_cabecalho is not None:
        for tr in tabela_cabecalho.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) != 2:
                continue
            rotulo = tds[0].get_text(strip=True).rstrip(":")
            campos[rotulo] = _limpar_texto(tds[1])

    anexos: list[Anexo] = []
    tabela_anexos = soup.find("table", id="tb_anexos_concursos")
    if tabela_anexos is not None and tabela_anexos.find("tbody") is not None:
        for tr in tabela_anexos.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            link = tr.find("a", href=True)
            if link is None:
                continue
            anexos.append(
                Anexo(
                    tipo=_limpar_texto(tds[0]),
                    descricao=_limpar_texto(tds[1]),
                    data=_parsear_data(tds[2].get_text(strip=True)),
                    url=_url_absoluta(link["href"]),
                )
            )

    return DetalheProcesso(
        numero_edital=campos.get("Nº/ Ano") or campos.get("Nº/Ano") or None,
        titulo=campos.get("Súmula", ""),
        categoria=campos.get("Tipo", ""),
        data_publicacao=_parsear_data(campos.get("Data")),
        anexos=anexos,
    )


#: documento que sai DEPOIS da abertura e não é fonte confiável de
#: cargo/salário/requisitos de abertura (pode listar só aprovados, sem
#: repetir a tabela do cargo) — nunca escolhido como "edital vigente" pra
#: extração (ver docstring do módulo).
_RE_EXCLUIR_ANEXO = re.compile(
    r"resultado|homologa|classifica|convoca|julgamento|recurso|habilita",
    re.IGNORECASE,
)


def escolher_pdf_edital(anexos: list[Anexo]) -> Anexo | None:
    """Escolhe o anexo PDF que representa a versão vigente do edital de
    ABERTURA (original ou retificação) — nunca resultado/homologação/
    classificação/convocação (ver `_RE_EXCLUIR_ANEXO` e docstring do
    módulo). Entre os candidatos restantes, pega o de data mais recente
    (cobre retificação publicada depois do edital original). `None` se só
    houver documentos de resultado (edital de abertura ainda não
    listado, ou removido) — quem chama não insere nada nesse ciclo e
    tenta de novo no próximo cron, nunca inventa dado a partir de um
    documento de resultado."""
    candidatos = [
        anexo
        for anexo in anexos
        if not _RE_EXCLUIR_ANEXO.search(anexo.descricao) and anexo.url.lower().endswith(".pdf")
    ]
    if not candidatos:
        return None

    com_data = [anexo for anexo in candidatos if anexo.data is not None]
    if com_data:
        return max(com_data, key=lambda anexo: anexo.data)
    return candidatos[0]


#: usado tanto por `scripts/rodar_pmjm_mg.py` (como `id_prefix` de
#: `processamento_pdf_gemini.processar_pdf_e_gravar_vagas`, que monta
#: `identificador_externo=f"{id_prefix}-{processo_id}-{slug_cargo}"`)
#: quanto por `extrair_ids_processados` (decodifica o `<id>` de volta) —
#: 1 constante só, pra nunca dessincronizar os dois lados.
ID_PREFIX = "pmjm-mg"


def _regex_processo_id(prefixo: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefixo)}-(\d+)-")


def extrair_ids_processados(identificadores_processados: set[str], *, prefixo: str = ID_PREFIX) -> set[int]:
    """Decodifica o `<id>` de processo embutido em cada `identificador_externo`
    já gravado pra esta fonte (formato `<prefixo>-<id>-<slug-cargo>`,
    montado por `processamento_pdf_gemini.processar_pdf_e_gravar_vagas` em
    `scripts/rodar_pmjm_mg.py` com `id_prefix=ID_PREFIX`) — usado pra pular
    processo já visto ANTES de baixar detalhe/PDF/chamar Gemini de novo
    (ver docstring do módulo pro motivo: a listagem cobre o histórico
    inteiro desde 2011, reprocessar tudo a cada cron seria caro à toa).
    Não depende de como o cargo foi transformado em slug — só do `<id>`
    numérico entre os dois `-` fixos."""
    padrao = _regex_processo_id(prefixo)
    ids: set[int] = set()
    for identificador in identificadores_processados:
        match = padrao.match(identificador)
        if match:
            ids.add(int(match.group(1)))
    return ids
