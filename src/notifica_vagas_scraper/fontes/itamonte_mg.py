"""Parser da Prefeitura de Itamonte/MG (site próprio, sem banca
organizadora intermediária pra descoberta — a organizadora CONSCAM só
aparece dentro dos PDFs).

Investigado pelo `pesquisador-fonte` (fixtures reais em
`docs/fixtures/itamonte_mg/` no repo principal — HTML da listagem e os 3
PDFs do Processo Seletivo nº 001/2026: edital original + 1ª e 2ª
rerratificações). Achado real: Médico ESF (6 vagas, 40h, R$ 17.802,73) e
Médico CAPS (1 vaga, 20h após a 1ª rerratificação reduzir de 40h — o
enunciado da 1ª rerratificação diz que atualiza também Dentista II, Educador
Físico, Farmacêutico, Fisioterapeuta e Nutricionista, mas comparando os
valores linha a linha só a carga horária de Dentista II e Médico – CAPS
mudou de fato; os demais foram reimpressos com o mesmo valor). CRM/MG
exigido.

Estrutura investigada:

- `GET /edital.php` — página única (sem paginação, sem filtro por tipo)
  com TODOS os atos publicados pela prefeitura (mais de mil): editais de
  licitação (Pregão, Dispensa, Concorrência, Credenciamento, Chamamento,
  Leilão), processos administrativos e, raramente, processo seletivo/
  concurso público. Cada item é um `<h5>DD/MM/AAAA - Título</h5>` seguido
  de um `<p><a href="...">Baixar Edital</a></p>` — sempre 1 título + 1
  link por item, sem paginação nem AJAX (`listar_documentos`). Só o que o
  título classifica como processo seletivo/concurso interessa pro cron
  (`eh_processo_seletivo`, `filtrar_processos_seletivos`) — mesmo cuidado
  de outras fontes que misturam licitação com processo seletivo no mesmo
  índice. HTML servido com charset real UTF-8 mas `<meta charset="iso-
  8859-1">` incorreto na página — itens antigos (anos anteriores) têm
  trechos em latin-1 de verdade misturados no meio do arquivo (mesmo
  arquivo com 2 encodings, provavelmente conteúdo colado de fontes
  diferentes ao longo do tempo); só o item mais recente (o que interessa
  pro cron) está em UTF-8 limpo. `scripts/rodar_itamonte_mg.py` força
  `resposta.encoding = resposta.apparent_encoding` antes de ler `.text`
  por causa disso.

- **O link de download não tem extensão de arquivo na URL** (ex:
  `.../licitacao/6f90c194798b02ccf0cfac0f8453a529.` — termina em ponto,
  sem nada depois) quando o conteúdo real é um ZIP; quando é um PDF
  direto, a URL termina em `.pdf` normalmente. `detectar_tipo_arquivo`
  nunca confia na extensão da URL — sempre olha os magic bytes do
  conteúdo baixado (`PK\\x03\\x04` = ZIP, `%PDF` = PDF). Achado real: o
  ato "Edital e Rerratificações Processo Seletivo 001/2026" é um ZIP
  contendo os 3 PDFs (original + as 2 rerratificações) — `extrair_pdfs`
  extrai todos os `.pdf` de dentro quando é ZIP, ou devolve o próprio
  conteúdo quando já é PDF puro.

- PDFs de texto real (texto selecionável, gerados pela organizadora
  CONSCAM) — sem necessidade de OCR/Gemini visual, `pdfplumber`/`pypdf`
  extrai o texto normalmente. Todo PDF da CONSCAM repete, no topo de CADA
  página, o mesmo bloco de endereço/rodapé institucional
  ("Florianópolis - Rua Esteves Junior..."); `_RODAPE_CONSCAM` remove
  esse bloco recorrente antes de rodar qualquer regex de tabela, senão
  ele se intromete no meio de uma linha de cargo cortada por quebra de
  página.

- **Quando há mais de uma versão do mesmo processo (rerratificações),
  cada rerratificação REIMPRIME a tabela inteira da seção que ela altera**
  (não só as linhas mudadas) — achado real: a 1ª rerratificação reimprime
  as 12 linhas inteiras de "Ensino Superior" (não só as 6 citadas no
  texto), inclusive renomeando alguns cargos (ex: "Educador Físico" vira
  "Educador Físico - NASF", "Farmacêutico" vira "Farmacêutico NASF") —
  por isso `mesclar_versoes` substitui a SEÇÃO inteira ("Ensino Médio
  e/ou Técnico" ou "Ensino Superior") pela versão mais recente que a
  reimprime, em vez de tentar casar cargo por cargo (que quebraria com a
  renomeação). A 2ª rerratificação desta fonte não toca a tabela de
  cargos (só reabre o cronograma de inscrições no Anexo V) — nesse caso
  `extrair_tabela_funcoes` devolve vazio pra ela, e a 1ª rerratificação
  continua sendo a versão vigente da tabela. `ordem_versao_documento`
  decide a ordem por CONTEÚDO do PDF ("Nª RERRATIFICAÇÃO"/"RERRATIFICAÇÃO"
  no corpo), não por nome de arquivo dentro do ZIP (não confiável).

- Datas de inscrição (`extrair_datas`) vêm do Anexo V ("CRONOGRAMA" —
  "Publicação do Edital", "Período de inscrições .../Reabertura do
  Período de inscrições ... Das HHhMMmin do dia DD/MM/AAAA às HHhMMmin do
  dia DD/MM/AAAA"). Mesma lógica de override por versão mais recente:
  `mesclar_datas` usa a primeira `data_publicacao` encontrada (não muda
  entre versões) e o último período de inscrições encontrado (a 2ª
  rerratificação reabre o prazo, então tem que vencer sobre o período do
  edital original).
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from bs4 import BeautifulSoup

from ..formatacao import parsear_salario_brl

BASE_URL = "https://www.itamonte.mg.gov.br"
URL_LISTAGEM = f"{BASE_URL}/edital.php"

#: fonte é dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais).
MUNICIPIO = "Itamonte"
UF = "MG"

__all__ = [
    "BASE_URL",
    "URL_LISTAGEM",
    "MUNICIPIO",
    "UF",
    "ItemListagem",
    "FuncaoTabela",
    "DatasProcesso",
    "listar_documentos",
    "eh_processo_seletivo",
    "filtrar_processos_seletivos",
    "extrair_numero_processo",
    "detectar_tipo_arquivo",
    "extrair_pdfs",
    "ordem_versao_documento",
    "extrair_tabela_funcoes",
    "mesclar_versoes",
    "extrair_datas",
    "mesclar_datas",
    "normalizar_cargo",
    "identificador_externo",
]


@dataclass
class ItemListagem:
    data: date | None
    titulo: str
    url: str


@dataclass
class FuncaoTabela:
    #: "Ensino Médio e/ou Técnico" | "Ensino Superior" — rótulo exato da
    #: subtabela onde a linha apareceu (ver docstring do módulo).
    secao: str
    cargo: str
    vagas: int | None
    carga_horaria: str | None
    salario: Decimal | None
    requisitos: str | None


@dataclass
class DatasProcesso:
    data_publicacao: date | None
    inscricoes_inicio: date | None
    inscricoes_fim: date | None


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def listar_documentos(html: str) -> list[ItemListagem]:
    """Lê `/edital.php`: cada `<h5>DD/MM/AAAA - Título</h5>` seguido de um
    `<p>` com um único `<a href="...">Baixar Edital</a>` (sem paginação).
    Item sem data reconhecível ainda entra na lista com `data=None` (não
    descarta por falha de parsing de data — só quem chama decide o que
    fazer)."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for h5 in soup.find_all("h5"):
        texto = re.sub(r"\s+", " ", h5.get_text(" ", strip=True)).strip()
        match = re.match(r"^(\d{2}/\d{2}/\d{4})\s*-\s*(.+)$", texto)
        data = _parsear_data(match.group(1)) if match else None
        titulo = match.group(2).strip() if match else texto
        if not titulo:
            continue

        paragrafo = h5.find_next_sibling("p")
        if paragrafo is None:
            continue
        link = paragrafo.find("a", href=True)
        if link is None:
            continue

        itens.append(ItemListagem(data=data, titulo=titulo, url=link["href"]))

    return itens


_RE_PROCESSO_SELETIVO = re.compile(r"processo\s+seletivo|concurso\s+p[úu]blico", re.IGNORECASE)


def eh_processo_seletivo(titulo: str) -> bool:
    """Filtro de título pra não confundir processo seletivo/concurso
    público com o grosso da listagem (licitação: Pregão, Dispensa,
    Concorrência, Credenciamento, Chamamento, Leilão)."""
    return bool(_RE_PROCESSO_SELETIVO.search(titulo))


def filtrar_processos_seletivos(itens: list[ItemListagem]) -> list[ItemListagem]:
    return [item for item in itens if eh_processo_seletivo(item.titulo)]


_RE_NUMERO_PROCESSO = re.compile(
    r"(?:processo\s+seletivo|concurso\s+p[úu]blico)\s*n?[ºo°.]*\s*(\d{1,4}/\d{2,4})",
    re.IGNORECASE,
)


def extrair_numero_processo(titulo: str) -> str | None:
    match = _RE_NUMERO_PROCESSO.search(titulo)
    return match.group(1) if match else None


def detectar_tipo_arquivo(conteudo: bytes) -> str:
    """Magic bytes — nunca confia na extensão da URL (ver docstring do
    módulo: o link de download às vezes não tem extensão nenhuma)."""
    if conteudo[:4] == b"PK\x03\x04":
        return "zip"
    if conteudo[:4] == b"%PDF":
        return "pdf"
    return "desconhecido"


def extrair_pdfs(conteudo: bytes) -> list[bytes]:
    """Devolve o conteúdo de cada PDF encontrado. Se `conteudo` já for um
    PDF, devolve `[conteudo]`. Se for um ZIP (achado real desta fonte:
    processo com rerratificações vem compactado), extrai todo arquivo
    `.pdf` de dentro, na ordem em que aparece no ZIP (a ordem real de
    aplicação das versões não depende disso — ver `ordem_versao_documento`,
    que lê o conteúdo de cada PDF, não a ordem/nome dentro do ZIP). Lista
    vazia se não reconhecer o formato nem achar PDF nenhum dentro do
    ZIP."""
    tipo = detectar_tipo_arquivo(conteudo)
    if tipo == "pdf":
        return [conteudo]
    if tipo == "zip":
        pdfs = []
        with zipfile.ZipFile(BytesIO(conteudo)) as arquivo_zip:
            for nome_interno in arquivo_zip.namelist():
                if nome_interno.lower().endswith(".pdf"):
                    pdfs.append(arquivo_zip.read(nome_interno))
        return pdfs
    return []


#: "ª" é obrigatório aqui (não opcional) — achado real: sem essa
#: exigência, o número do próprio processo seletivo (ex: "Nº 001/2026")
#: pouco antes de "RERRATIFICAÇÃO" no cabeçalho do documento original
#: batia por engano (só "RERRATIFICAÇÃO", sem ordinal, é o caso do
#: original/1ª versão — ver docstring do módulo).
_RE_ORDEM_VERSAO = re.compile(r"(\d+)ª\s*RERRATIFICA", re.IGNORECASE)
_RE_RERRATIFICACAO_SEM_NUMERO = re.compile(r"RERRATIFICA", re.IGNORECASE)


def ordem_versao_documento(texto_pdf: str) -> int:
    """0 = edital original (sem "RERRATIFICAÇÃO" no corpo), 1 = 1ª
    rerratificação, 2 = 2ª rerratificação etc. — decide pelo CONTEÚDO do
    PDF (título "Nª RERRATIFICAÇÃO DO EDITAL..."), não pelo nome de
    arquivo dentro do ZIP (não confiável, ver docstring do módulo)."""
    match = _RE_ORDEM_VERSAO.search(texto_pdf)
    if match:
        return int(match.group(1))
    if _RE_RERRATIFICACAO_SEM_NUMERO.search(texto_pdf):
        return 1
    return 0


_RODAPE_CONSCAM = re.compile(
    r"Florian[óo]polis\s*-\s*Rua Esteves Junior.*?www\.conscamweb\.com\.br",
    re.DOTALL,
)
_HEADER_TABELA = re.compile(
    r"Fun[çc][õo]es\s+Vagas\s+Carga\s+Hor[áa]ria\s+Sal[áa]rio\s+Base\s+Requisitos\s+Taxa\s+de\s+Inscri[çc][ãa]o"
)
_TITULOS_SECAO = ("Ensino Médio e/ou Técnico", "Ensino Superior")
_RE_TITULO_SECAO = re.compile(
    r"(Ensino M[ée]dio e/ou T[ée]cnico|Ensino Superior)(?=\s+(?:Fun[çc][õo]es|[A-ZÀ-Ý]))(?!\s+em\s)"
)
_RE_LINHA = re.compile(
    r"([^\d]+?)\s+(\d+)\s+(\d+)\s+Horas\s+Semanais\s+R\$\s*([\d.,]+)\s+(.+?)\s+R\$\s*([\d.,]+)"
)
#: bleed de célula "Requisitos" cortada por quebra de página (ex:
#: "registro no COREN/MG Técnico de Enfermagem – ESF") — quando o nome de
#: cargo capturado contém um trecho "<SIGLA>/MG" (registro de conselho de
#: classe), o que vem antes pertence à linha ANTERIOR, não ao cargo desta
#: linha.
_RE_CODIGO_CONSELHO = re.compile(r"^.*?\b[A-Za-zÀ-ÿ]{2,10}/MG\b\s*")


def _limpar_texto_base(texto_pdf: str) -> str:
    texto = _RODAPE_CONSCAM.sub(" ", texto_pdf)
    return re.sub(r"\s+", " ", texto).strip()


def _limpar_cargo(cargo: str) -> str:
    cargo = _RE_CODIGO_CONSELHO.sub("", cargo)
    # frase de introdução da tabela ("As funções, vagas, ... segue:", "...
    # para as funções de X, Y e Z:") sempre termina em ":" bem antes do
    # nome do primeiro cargo real da seção.
    if ":" in cargo:
        cargo = cargo.rsplit(":", 1)[-1]
    return cargo.strip()


def extrair_tabela_funcoes(texto_pdf: str) -> dict[str, list[FuncaoTabela]]:
    """Lê a(s) tabela(s) "Funções | Vagas | Carga Horária | Salário Base |
    Requisitos | Taxa de Inscrição" de UM PDF (item 1.2 do edital original,
    ou a reimpressão de uma rerratificação) — sem lista de cargos
    conhecida, sem filtro por nome nem por especialidade: nenhum cargo
    (médico ou não) é descartado. Devolve `{secao: [FuncaoTabela, ...]}`;
    dicionário vazio se o PDF não tiver tabela nenhuma (ex: 2ª
    rerratificação desta fonte, que só mexe no cronograma de inscrições,
    não na tabela de cargos)."""
    texto = _limpar_texto_base(texto_pdf)
    partes = _RE_TITULO_SECAO.split(texto)

    resultado: dict[str, list[FuncaoTabela]] = {}
    secao_atual: str | None = None
    for parte in partes:
        if parte in _TITULOS_SECAO:
            secao_atual = parte
            continue
        if secao_atual is None:
            continue

        trecho = _HEADER_TABELA.sub(" ", parte)
        linhas: list[FuncaoTabela] = []
        for match in _RE_LINHA.finditer(trecho):
            cargo_bruto, vagas, carga, salario, requisitos, _taxa = match.groups()
            cargo = _limpar_cargo(cargo_bruto)
            if not cargo:
                continue
            linhas.append(
                FuncaoTabela(
                    secao=secao_atual,
                    cargo=cargo,
                    vagas=int(vagas),
                    carga_horaria=f"{carga} Horas Semanais",
                    salario=parsear_salario_brl(salario),
                    requisitos=requisitos.strip() or None,
                )
            )
        if linhas:
            resultado.setdefault(secao_atual, []).extend(linhas)

    return resultado


def mesclar_versoes(textos_pdfs: list[str]) -> list[FuncaoTabela]:
    """Mescla a tabela de cargos de todas as versões de um mesmo processo
    (edital original + rerratificações, em qualquer ordem de entrada —
    a ordem real vem de `ordem_versao_documento`, não da ordem da lista).
    Quando uma versão mais recente reimprime uma seção inteira ("Ensino
    Médio e/ou Técnico" ou "Ensino Superior"), ela SUBSTITUI totalmente a
    versão anterior dessa seção (não faz merge cargo a cargo — ver
    docstring do módulo pro achado real que justifica isso: cargos são
    renomeados entre versões, ex: "Educador Físico" -> "Educador Físico -
    NASF"). Seção não tocada por nenhuma versão posterior continua com o
    valor da versão mais antiga que a definiu."""
    ordenados = sorted(textos_pdfs, key=ordem_versao_documento)

    por_secao: dict[str, list[FuncaoTabela]] = {}
    for texto in ordenados:
        tabela = extrair_tabela_funcoes(texto)
        for secao, linhas in tabela.items():
            por_secao[secao] = linhas  # substitui a seção inteira

    resultado: list[FuncaoTabela] = []
    for secao in _TITULOS_SECAO:
        resultado.extend(por_secao.get(secao, []))
    # seção fora do vocabulário conhecido (não deveria acontecer, mas não
    # descarta silenciosamente se acontecer).
    for secao, linhas in por_secao.items():
        if secao not in _TITULOS_SECAO:
            resultado.extend(linhas)

    return resultado


_RE_PUBLICACAO_EDITAL = re.compile(r"Publica[çc][ãa]o do Edital\s+(\d{2}/\d{2}/\d{4})")
_RE_PERIODO_INSCRICOES = re.compile(
    r"(?:Reabertura d[oa] Per[íi]odo de inscri[çc][õo]es|Per[íi]odo de inscri[çc][õo]es)"
    r".*?Das\s+\d{1,2}h\d{2}min\s+do dia\s+(\d{2}/\d{2}/\d{4})\s+[àa]s\s+\d{1,2}h\d{2}min\s+do dia\s+(\d{2}/\d{2}/\d{4})"
)


def extrair_datas(texto_pdf: str) -> DatasProcesso:
    """Lê o Anexo V ("CRONOGRAMA") de UM PDF: data de publicação do
    edital e período de inscrições (o mesmo padrão de frase cobre tanto
    "Período de inscrições" do edital original quanto "Reabertura do
    Período de inscrições" de uma rerratificação que reabre o prazo).
    Campo fica `None` quando o PDF não menciona aquele dado (ex:
    rerratificação que só mexe na tabela de cargos, não no cronograma)."""
    texto = _limpar_texto_base(texto_pdf)

    match_publicacao = _RE_PUBLICACAO_EDITAL.search(texto)
    data_publicacao = _parsear_data(match_publicacao.group(1)) if match_publicacao else None

    match_periodo = _RE_PERIODO_INSCRICOES.search(texto)
    inscricoes_inicio = _parsear_data(match_periodo.group(1)) if match_periodo else None
    inscricoes_fim = _parsear_data(match_periodo.group(2)) if match_periodo else None

    return DatasProcesso(
        data_publicacao=data_publicacao,
        inscricoes_inicio=inscricoes_inicio,
        inscricoes_fim=inscricoes_fim,
    )


def mesclar_datas(textos_pdfs: list[str]) -> DatasProcesso:
    """`data_publicacao` fica com a PRIMEIRA versão que a menciona (não
    muda entre rerratificações); `inscricoes_inicio`/`inscricoes_fim`
    ficam com a ÚLTIMA versão que os menciona (uma rerratificação pode
    reabrir o prazo de inscrição, e esse novo prazo tem que vencer sobre
    o do edital original — achado real: 2ª rerratificação desta fonte)."""
    ordenados = sorted(textos_pdfs, key=ordem_versao_documento)

    data_publicacao: date | None = None
    inscricoes_inicio: date | None = None
    inscricoes_fim: date | None = None
    for texto in ordenados:
        datas = extrair_datas(texto)
        if data_publicacao is None and datas.data_publicacao is not None:
            data_publicacao = datas.data_publicacao
        if datas.inscricoes_inicio is not None and datas.inscricoes_fim is not None:
            inscricoes_inicio = datas.inscricoes_inicio
            inscricoes_fim = datas.inscricoes_fim

    return DatasProcesso(
        data_publicacao=data_publicacao,
        inscricoes_inicio=inscricoes_inicio,
        inscricoes_fim=inscricoes_fim,
    )


def normalizar_cargo(cargo: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", cargo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def identificador_externo(numero_processo: str | None, cargo: str) -> str:
    """Chave de dedup: fonte é dedicada a 1 único município, então basta
    número do processo (quando identificado) + slug do cargo pra ser
    estável e sem colisão entre execuções."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    slug_processo = re.sub(r"[^a-z0-9]+", "-", (numero_processo or "sem-numero").lower()).strip("-")
    return f"itamonte-mg-{slug_processo}-{slug_cargo}"
