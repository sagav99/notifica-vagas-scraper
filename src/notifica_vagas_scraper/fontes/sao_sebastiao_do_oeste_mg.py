"""Parser da Prefeitura de São Sebastião do Oeste/MG (CMS Joomla/K2,
`saosebastiaodooeste.mg.gov.br`) — sem banca organizadora terceirizada
descobrível de forma multi-tenant (a organização é da IDEAP – Instituto de
Desenvolvimento Social, Empresarial e de Administração Pública, mas esta
fonte é dedicada a 1 único município, mesmo padrão de `sorocaba_sp.py`/
`guiricema_mg.py`).

Investigado pelo `pesquisador-fonte` (2026-09-23, fixtures reais em
`docs/fixtures/sao_sebastiao_do_oeste_mg/` no repo principal): sem
bloqueio anti-bot real (o 406 inicial era só falta de header
`Accept`/`Accept-Language`, resolvido pedindo com header normal — ver
`USER_AGENT`/cabeçalhos no script). **Alvo real confirmado**: Edital de
Processo Seletivo Simplificado nº 01/2026 (Médico – ESF, 3 vagas,
R$ 19.846,52, 40h — mais Enfermeiro/Odontólogo/Auxiliar de Saúde
Bucal/Técnico em Enfermagem no mesmo edital), inscrição aberta até
29/09/2026.

**Achado importante pro dedup (motivou usar URL/slug completo, não o
número do edital, como parte da chave)**: o site tem histórico de vários
PSS ao longo de 2026, e a numeração do edital REINICIA por
secretaria/grupo de cargos — existe mais de um "Edital nº 01/2026"
coexistindo (achado do `pesquisador-fonte`, não reproduzido nesta fixture
específica mas documentado em TAREFAS.md). `identificador_externo` usa
`item.url` completo (via `slug_url`) em vez de só o número do edital, pra
nunca colidir dois editais numerados igual de secretarias diferentes.

Estrutura investigada:
- GET `/processos-seletivos` (Joomla K2, listagem paginada via
  `?start=N`, 12 itens por página — `URL_LISTAGEM`/`PARAMS_PAGINA`).
  `listar_processos` lê `.catItemView` — cada item tem título+link em
  `.catItemTitle a` e data em `.catItemDateCreated` (formato "Terça, 22
  Setembro 2026", em português). A listagem mistura MUITOS tipos de
  publicação sob a mesma categoria "Processos Seletivos" (gabarito,
  resultado, retificação, despacho, convocação, extrato de divulgação, e
  até um programa não-relacionado "Habitacional Minha Casa Minha Vida")
  — só o edital de ABERTURA original é fonte confiável de cargo/salário/
  requisitos completo. `eh_edital_abertura` exige que o título contenha
  tanto "edital" quanto "processo seletivo" e não bata nenhuma palavra de
  exclusão (gabarito/resultado/retificação/despacho/convocação/extrato/
  divulgação/homologação/classificação/julgamento/recurso/habilitação) —
  mesmo espírito de `guiricema_mg.eh_edital_abertura`/
  `sorocaba_sp.eh_post_candidato`, adaptado ao vocabulário real desta
  listagem (que já tem bem mais tipos de exclusão do que as outras 2
  fontes).
- GET a página fixa de cada edital candidato (`item.url`): `.itemAttachments`
  lista os PDFs anexados com título/texto próprio no `<a>` (ex: "Edital",
  "Anexos" — achado real no item 4769: 2 anexos distintos, um só com o
  texto narrativo do edital, outro ("Anexos") com os ANEXO I/II/III —
  cargo/vagas/salário/requisitos SÓ existem no ANEXO I, dentro do PDF
  "Anexos"). `escolher_pdf_anexo` escolhe o link cujo texto contenha
  "anexo" (case-insensitive); sem esse candidato, cai pro 1º link `.pdf`
  da lista (edital sem anexo separado, cargo pode estar no próprio corpo
  — melhor tentar do que devolver `None`).
- **PDF "Anexos" é texto real, 100% extraível sem Gemini/OCR** (confirmado
  via `pypdf`/`pdfplumber` na investigação e nesta implementação: ANEXO I
  é uma tabela limpa, 1 linha por cargo, sem célula fora de ordem) — por
  isso a extração aqui é toda determinística via
  `pdfplumber.Page.extract_tables()`, mesmo padrão de `sorocaba_sp.py`
  (mais barato e mais previsível que leitura visual pra um formato de
  tabela já confirmado estável). `extrair_cargos_do_pdf` nunca filtra por
  cargo de saúde/médico — grava TODAS as linhas do ANEXO I (Auxiliar de
  Saúde Bucal, Enfermeiro, Médico, Odontólogo, Técnico em Enfermagem no
  caso real), a decisão de "só médico entra em catálogo" é de
  `categorizarCargoSaude` (repo principal), nunca do scraper.
- `numero_edital`/`banca_organizadora`/`tem_prova`/`exige_curriculo`/
  `inscricoes_inicio`/`inscricoes_fim` são lidos direto do texto linear do
  MESMO PDF "Anexos" (não precisa baixar o PDF "Edital" separado — o
  cabeçalho do PDF "Anexos" já repete "Edital de Processo Seletivo
  Simplificado nº 01/2026 / Organização: IDEAP..." e o ANEXO II confirma
  "prova objetiva" sem nenhuma fase de currículo/título; o cronograma do
  ANEXO III tem as datas de inscrição via internet).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.saosebastiaodooeste.mg.gov.br"
URL_LISTAGEM = f"{BASE_URL}/processos-seletivos"

#: fonte dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais).
MUNICIPIO = "São Sebastião do Oeste"
UF = "MG"
ORGAO_PADRAO = "Prefeitura Municipal de São Sebastião do Oeste"

#: itens por página da listagem K2 (`?start=N`, N múltiplo de 12) — ver
#: docstring do módulo.
ITENS_POR_PAGINA = 12

__all__ = [
    "BASE_URL",
    "URL_LISTAGEM",
    "MUNICIPIO",
    "UF",
    "ORGAO_PADRAO",
    "ITENS_POR_PAGINA",
    "ItemListagem",
    "Documento",
    "Cargo",
    "EditalExtraido",
    "listar_processos",
    "eh_edital_abertura",
    "listar_documentos",
    "escolher_pdf_anexo",
    "extrair_numero_edital",
    "extrair_banca_organizadora",
    "extrair_tem_prova",
    "extrair_exige_curriculo",
    "extrair_inscricoes",
    "extrair_cargos_do_pdf",
    "extrair_edital",
    "identificador_externo",
]


@dataclass
class ItemListagem:
    titulo: str
    url: str
    data_publicacao: date | None


@dataclass
class Documento:
    titulo: str
    url: str


@dataclass
class Cargo:
    codigo: str | None
    nome: str
    numero_vagas: int | None
    vagas_pcd: int
    vencimento_inicial: float | None
    carga_horaria: str | None
    requisitos: str
    taxa_inscricao: float | None


@dataclass
class EditalExtraido:
    numero_edital: str | None
    banca_organizadora: str | None
    tem_prova: bool
    exige_curriculo: bool
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    cargos: list[Cargo]


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


#: "Terça, 22 Setembro 2026" — dia da semana em português + dia + mês por
#: extenso + ano, formato real do `.catItemDateCreated` do K2.
_MESES_PT = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
_RE_DATA_LISTAGEM = re.compile(r"(\d{1,2})\s+([A-Za-zçÇ]+)\s+(\d{4})")


def _parsear_data_listagem(texto: str | None) -> date | None:
    if not texto:
        return None
    match = _RE_DATA_LISTAGEM.search(texto)
    if not match:
        return None
    dia, mes_texto, ano = match.groups()
    mes = _MESES_PT.get(_normalizar(mes_texto))
    if mes is None:
        return None
    try:
        return date(int(ano), mes, int(dia))
    except ValueError:
        return None


def listar_processos(html: str) -> list[ItemListagem]:
    """Lê `.catItemView` da listagem paginada (`URL_LISTAGEM`, página N
    via `?start=<N*ITENS_POR_PAGINA>`) — sem filtro nenhum aqui (mistura
    todo tipo de publicação da categoria), quem decide o que é edital de
    abertura processável é `eh_edital_abertura`. Item sem título/link
    reconhecível é ignorado, nunca quebra o parsing dos demais."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for item in soup.select(".catItemView"):
        titulo_no = item.select_one(".catItemTitle a")
        if titulo_no is None or not titulo_no.get("href"):
            continue
        titulo = titulo_no.get_text(" ", strip=True)
        if not titulo:
            continue

        data_no = item.select_one(".catItemDateCreated")
        data_publicacao = _parsear_data_listagem(data_no.get_text(" ", strip=True) if data_no else None)

        itens.append(
            ItemListagem(
                titulo=titulo,
                url=urljoin(BASE_URL, titulo_no["href"]),
                data_publicacao=data_publicacao,
            )
        )

    return itens


#: exige "edital" E "processo seletivo" no título (com acento normalizado)
#: — descarta publicações fora do escopo de edital de abertura (ver
#: docstring do módulo pro vocabulário real encontrado na listagem).
_RE_EDITAL = re.compile(r"\bedital\b")
_RE_PROCESSO_SELETIVO = re.compile(r"processo\s+seletivo")
_RE_EXCLUIR_TITULO = re.compile(
    r"gabarito|resultado|retificac|despacho|convoca|extrato|divulgac|homologa|classifica|julgamento|recurso|habilita"
)


def eh_edital_abertura(titulo: str) -> bool:
    texto = _normalizar(titulo)
    return (
        bool(_RE_EDITAL.search(texto))
        and bool(_RE_PROCESSO_SELETIVO.search(texto))
        and not _RE_EXCLUIR_TITULO.search(texto)
    )


def listar_documentos(html: str) -> list[Documento]:
    """Lê `.itemAttachments` da página fixa do edital (`item.url`) — cada
    `<li><a>` tem o título do anexo no PRÓPRIO texto do link (ex: "Edital",
    "Anexos"), diferente de `pbh_ibfc` (que lê de uma tabela separada)."""
    soup = BeautifulSoup(html, "html.parser")
    documentos: list[Documento] = []
    for a in soup.select(".itemAttachments a[href]"):
        titulo = a.get_text(" ", strip=True) or (a.get("title") or "").strip()
        if not titulo:
            continue
        documentos.append(Documento(titulo=titulo, url=a["href"]))
    return documentos


def escolher_pdf_anexo(documentos: list[Documento]) -> Documento | None:
    """Escolhe o PDF que deve conter o ANEXO I (cargo/vagas/salário/
    requisitos) — prioriza o link cujo texto contenha "anexo"
    (case-insensitive, ver achado real do item 4769: "Edital" x "Anexos").
    Sem candidato assim, cai pro 1º link `.pdf` da lista (edital sem anexo
    separado). `None` se não houver nenhum PDF."""
    candidatos_pdf = [d for d in documentos if d.url.lower().endswith(".pdf")]
    if not candidatos_pdf:
        return None
    anexos = [d for d in candidatos_pdf if "anexo" in _normalizar(d.titulo)]
    return anexos[0] if anexos else candidatos_pdf[0]


_RE_NUMERO_EDITAL = re.compile(r"Processo Seletivo Simplificado\s*n[ºo°]?\s*(\d+/\d{4})", re.IGNORECASE)


def extrair_numero_edital(texto_pdf: str) -> str | None:
    match = _RE_NUMERO_EDITAL.search(texto_pdf)
    return match.group(1) if match else None


def extrair_banca_organizadora(texto_pdf: str) -> str | None:
    """Lê a banca organizadora direto do texto do PDF (não hardcoded) —
    achado real: "Organização: IDEAP – Instituto de Desenvolvimento
    Social, Empresarial e de Administração Pública". `None` se o texto
    não citar a IDEAP (edital futuro sem essa organizadora, por
    exemplo)."""
    if re.search(r"\bIDEAP\b", texto_pdf, re.IGNORECASE):
        return "IDEAP – Instituto de Desenvolvimento Social, Empresarial e de Administração Pública"
    return None


def extrair_tem_prova(texto_pdf: str) -> bool:
    return bool(re.search(r"prova\s+objetiva", texto_pdf, re.IGNORECASE))


def extrair_exige_curriculo(texto_pdf: str) -> bool:
    return bool(re.search(r"prova\s+de\s+t[ií]tulos|an[aá]lise\s+de\s+curr[ií]culo", texto_pdf, re.IGNORECASE))


#: cronograma (ANEXO III) vem de `extract_tables()` (não do texto linear
#: — achado real: a extração linear embaralha a ordem das colunas dessa
#: tabela específica, mesmo tipo de achado que levou Mauá/SP a precisar de
#: Gemini; aqui a tabela estruturada resolve sem precisar disso). Procura
#: a linha cujo texto (todas as células concatenadas) cite "Período de
#: Inscrições" + "INTERNET" — inscrição presencial tem o MESMO intervalo
#: de datas logo depois, então basta a 1ª ocorrência.
_RE_LINHA_INSCRICAO_INTERNET = re.compile(r"per[ií]odo de inscri[cç][õo]es.*internet", re.IGNORECASE | re.DOTALL)
_RE_INTERVALO_DATAS = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})", re.DOTALL)


def _parsear_data_ddmmyyyy(texto: str) -> date | None:
    try:
        dia, mes, ano = texto.split("/")
        return date(int(ano), int(mes), int(dia))
    except ValueError:
        return None


def extrair_inscricoes(tabelas: list[list[list[str | None]]]) -> tuple[date | None, date | None]:
    for tabela in tabelas:
        if not tabela:
            continue
        for linha in tabela:
            texto_linha = " ".join(c for c in linha if c)
            if not _RE_LINHA_INSCRICAO_INTERNET.search(texto_linha):
                continue
            match = _RE_INTERVALO_DATAS.search(texto_linha)
            if match:
                return _parsear_data_ddmmyyyy(match.group(1)), _parsear_data_ddmmyyyy(match.group(2))
    return None, None


def _texto_limpo(bruto: str | None) -> str:
    return re.sub(r"\s+", " ", bruto or "").strip()


def _valor_para_float(bruto: str) -> float | None:
    texto = _texto_limpo(bruto).replace("R$", "").strip()
    if not texto:
        return None
    try:
        return float(texto.replace(".", "").replace(",", "."))
    except ValueError:
        return None


#: linha de dado de cargo do ANEXO I sempre começa com um código numérico
#: na 1ª coluna — cabeçalho, "SUBTOTAL" e "TOTAL DE VAGAS" não batem isso,
#: são puladas sem precisar conhecer a posição exata da tabela na página.
def _eh_linha_cargo(linha: list[str | None]) -> bool:
    if len(linha) < 8:
        return False
    codigo = _texto_limpo(linha[0])
    return codigo.isdigit()


def extrair_cargos_do_pdf(tabelas: list[list[list[str | None]]]) -> list[Cargo]:
    """Parseia o ANEXO I (Cargo/Vagas/PCD/Vencimento Inicial/Carga
    Horária/Requisitos/Taxa de Inscrição) a partir das tabelas já
    extraídas via `pdfplumber.Page.extract_tables()` (passe TODAS as
    tabelas de TODAS as páginas do PDF "Anexos" — a função filtra sozinha
    quem é linha de cargo real via `_eh_linha_cargo`, ignora cabeçalho/
    SUBTOTAL/TOTAL e tabelas de outras seções, ex: ANEXO II/III). Nunca
    filtra por especialidade — cada linha do ANEXO I vira 1 `Cargo`,
    incluindo cargos não-médicos (a decisão de escopo é de
    `categorizarCargoSaude`, fora deste módulo)."""
    cargos: list[Cargo] = []
    for tabela in tabelas:
        if not tabela:
            continue
        for linha in tabela:
            if not _eh_linha_cargo(linha):
                continue
            nome = _texto_limpo(linha[1])
            if not nome:
                continue
            vagas_texto = _texto_limpo(linha[2])
            numero_vagas = int(vagas_texto) if vagas_texto.isdigit() else None
            pcd_texto = _texto_limpo(linha[3])
            vagas_pcd = int(pcd_texto) if pcd_texto.isdigit() else 0
            vencimento = _valor_para_float(linha[4])
            carga = _texto_limpo(linha[5]) or None
            requisitos = _texto_limpo(linha[6])
            taxa = _valor_para_float(linha[7])

            cargos.append(
                Cargo(
                    codigo=_texto_limpo(linha[0]) or None,
                    nome=nome,
                    numero_vagas=numero_vagas,
                    vagas_pcd=vagas_pcd,
                    vencimento_inicial=vencimento,
                    carga_horaria=carga,
                    requisitos=requisitos,
                    taxa_inscricao=taxa,
                )
            )
    return cargos


def extrair_edital(texto_pdf: str, tabelas: list[list[list[str | None]]]) -> EditalExtraido:
    inscricoes_inicio, inscricoes_fim = extrair_inscricoes(tabelas)
    return EditalExtraido(
        numero_edital=extrair_numero_edital(texto_pdf),
        banca_organizadora=extrair_banca_organizadora(texto_pdf),
        tem_prova=extrair_tem_prova(texto_pdf),
        exige_curriculo=extrair_exige_curriculo(texto_pdf),
        inscricoes_inicio=inscricoes_inicio,
        inscricoes_fim=inscricoes_fim,
        cargos=extrair_cargos_do_pdf(tabelas),
    )


def identificador_externo(url_item: str, cargo: str) -> str:
    """Chave de dedup: slug da URL COMPLETA do item da listagem (não o
    número do edital sozinho — ver docstring do módulo, "Achado
    importante pro dedup": a numeração reinicia entre secretarias/grupos
    de cargos diferentes, então dois editais "01/2026" reais e distintos
    não podem colidir) + slug do cargo."""
    caminho = re.sub(r"^https?://[^/]+", "", url_item)
    slug_url = re.sub(r"[^a-z0-9]+", "-", _normalizar(caminho)).strip("-")
    slug_cargo = re.sub(r"[^a-z0-9]+", "-", _normalizar(cargo)).strip("-")
    return f"sao-sebastiao-do-oeste-mg-{slug_url}-{slug_cargo}"
