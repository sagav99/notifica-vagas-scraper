"""Parser da Prefeitura de Belo Horizonte/MG (PBH), banca organizadora
IBFC (Instituto Brasileiro de Formação e Capacitação) — diferente das
outras fontes deste projeto, **não é uma banca multi-tenant a descobrir**:
é um único município (Belo Horizonte) com histórico de concursos públicos
efetivos, achado 2026-09-03 numa checagem externa mais ampla (ver
TAREFAS.md, item "Belo Horizonte/MG (capital) como fonte"). Investigado
via fixtures reais em `docs/fixtures/pbh_ibfc/` (repo principal), sem
bloqueio confirmado (`ibfc.org.br` institucional dá 403, mas isso não
importa aqui — este parser nunca precisa desse domínio, ver abaixo).

**Concurso real a capturar**: Edital 01/2025 – SMSA (582 vagas, cargos de
Cirurgião-Dentista, Enfermeiro, MÉDICO — 33 linhas de especialidade
médica em ~19-20 especialidades distintas —, Técnico de Serviços de Saúde
e Técnico Superior de Saúde), homologado, com nomeações ainda em curso em
2026 (5º Ato de Nomeação em 29/08/2026 na fixture real).

**Cuidado real que motivou este parser ter um filtro defensivo próprio**:
existe também o Edital 155/2026 (`docs/fixtures/pbh_ibfc/
edital_155_2026_promocao_interna_NAO_e_concurso_externo.pdf`), cujo
próprio texto (1ª página) se identifica como "PROCEDIMENTO SELETIVO
INTERNO ... para promoção dos servidores públicos ... da classe B para a
classe C" — **promoção interna de servidor já efetivo, não concurso
externo pra candidato de fora**. Nunca deve virar vaga no produto.

Estrutura investigada:
- GET `{BASE_URL}/oportunidades-de-trabalho` (Drupal Views) com query
  string `field_modalidade_value=Concurso Público` (`PARAMS_LISTAGEM`) —
  o dropdown "Modalidade" tem só 5 valores possíveis ("Concurso Público",
  "Seleção Pública", "Processo Seletivo Simplificado", "Seleção Interna",
  "Estágio"); usar esse filtro na origem já basta pra excluir qualquer
  coisa rotulada como "interna" (o 155/2026 não aparece na fixture real
  filtrada por "Concurso Público", situação "- Todos -": 16 itens, todos
  concursos externos de verdade, de 01/2019 a 01/2026). `listar_processos`
  lê `.view-content-wrap .item` — cada item tem um link
  `.item_ar_licitacao a` com texto no formato "<Área> -Edital Nº <número>
  -<Modalidade>" e um resumo opcional `.item_ar_objeto` (pode vir só como
  "…", nesse caso `resumo=None`). **Sem paginação visível na fixture**
  (`div.views-infinite-scroll-content-wrapper` sugere infinite-scroll,
  mas os 16 itens da amostra real já cobrem 2019-2026 inteiro sem gatilho
  de "carregar mais" — assunção não 100% validada contra um cenário com
  MUITO mais concursos públicos históricos; revisitar se o total real
  divergir muito de 16 numa execução real).
- `eh_concurso_externo_processavel` é uma SEGUNDA camada de defesa, além
  do filtro de origem: rejeita qualquer item cujo texto (área + modalidade
  + número do edital) contenha o radical "intern" (cobre "interna" e
  "interno", maiúsculo/minúsculo, com ou sem acento) — protege mesmo se o
  filtro `PARAMS_LISTAGEM` for ampliado/removido no futuro (ex: pra achar
  concurso mal classificado). Ver `scripts/rodar_pbh_ibfc.py`.
- GET a página fixa de cada edital (ex: `/saude/oportunidades-de-
  trabalho/concurso-publico-01-2025`, `item.url`): uma única `<table>`
  com colunas Título/Link/Arquivo/Data listando TODOS os atos e anexos
  publicados pro concurso (editais, retificações, convocações,
  resultados, atos de nomeação — 58 linhas reais no Edital 01/2025).
  `listar_documentos` lê cada `<tr>` (pula o cabeçalho), tira o título de
  `div.field--name-field-title` e a data da última célula; o link do PDF
  em si **não tem posição de coluna fixa** (achado real: às vezes é a 2ª
  célula, às vezes a 3ª, dependendo de qual "ícone" — link externo pro
  Diário Oficial vs PDF — vem primeiro), por isso pega o primeiro `<a
  href>` da linha inteira que termina em `.pdf` (case-insensitive),
  ignorando links pra `dom-web.pbh.gov.br` (página do Diário Oficial, não
  um PDF baixável de cargo/salário).
- **Cargo/especialidade/jornada/vagas/salário só existem dentro do texto
  do PDF do edital (ANEXO I)** — como nas outras fontes só-PDF do
  projeto, precisa Gemini (`gemini_pdf.extrair_vagas_de_pdf`), regex puro
  na tabela linearizada é frágil demais (confirmado manualmente extraindo
  texto do PDF real: colunas de Cargo/Especialidade/Jornada colapsam sem
  separador confiável). `escolher_edital_com_anexo` decide qual PDF ler:
  prioriza o título que começa com "Edital" (não uma retificação isolada,
  que só lista o que MUDOU, não a tabela inteira) e contém "compilado"
  (achado real: "Edital 01/2025 - compilado após 3ª retificação" — texto
  consolidado com o ANEXO I já com todas as retificações aplicadas,
  confirmado no PDF real: cabeçalho "EDITAL 01/2025 – SMSA -
  RETIFICADO"); sem candidato "compilado" ainda (concurso recém-aberto,
  sem retificação), cai pro título "Edital ..." mais recente por data
  (o edital original já tem seu próprio ANEXO I). `None` se não houver
  nenhum documento cujo título comece com "Edital" (concurso sem edital
  publicado ainda — não deve acontecer na prática, mas sem adivinhar).

**Achado de peso pro dedup (motivou `montar_cargo_com_jornada`)**: a
MESMA especialidade pode aparecer várias vezes no ANEXO I com jornadas
(e portanto vagas/salários) DIFERENTES — confirmado extraindo manualmente
o texto do PDF real do Edital 01/2025: "Médico - Pediatria" tem 3 linhas
(12h/20 vagas/R$ 3.524,33, 20h/20 vagas/R$ 5.873,89, 24h/5 vagas/
R$ 7.048,66); "Médico - Cirurgia Geral" tem 2 (12h e 24h, salários bem
diferentes); "Médico de Família e Comunidade" tem 4 jornadas (12h/20h/
24h/40h). `db.inserir_vaga_com_evidencia` faz dedup por (município, órgão,
CARGO, edital) — sem incluir a jornada no texto do cargo gravado, essas
linhas colidiriam e só a primeira sobreviveria, perdendo silenciosamente
2 de 3 (ou 3 de 4) faixas de vaga/salário da MESMA especialidade médica.
`montar_cargo_com_jornada` sempre inclui a jornada (`carga_horaria`
devolvida pelo Gemini) no texto do cargo gravado quando disponível, pra
cada combinação virar uma linha distinta no banco.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://prefeitura.pbh.gov.br"
URL_LISTAGEM = f"{BASE_URL}/oportunidades-de-trabalho"

#: só "Concurso Público" — ver docstring do módulo pro motivo de já
#: excluir "Seleção Interna" (e as outras modalidades) na origem.
PARAMS_LISTAGEM = {"field_modalidade_value": "Concurso Público"}

#: fonte é fixa a um único município — não há descoberta de tenant aqui.
MUNICIPIO = "Belo Horizonte"
UF = "MG"


@dataclass
class ItemListagem:
    area: str
    numero_edital: str | None
    modalidade: str | None
    url: str
    #: `None` quando `.item_ar_objeto` só trouxer "…" (placeholder sem
    #: texto real) ou estiver ausente.
    resumo: str | None


@dataclass
class Documento:
    titulo: str
    url_pdf: str
    data: date | None


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


#: "<Área> -Edital Nº <número> -<Modalidade>" — hífen às vezes colado sem
#: espaço nenhum antes (achado real: "Behrens -Edital Nº 01/2026
#: -Concurso Público "), por isso `\s*-\s*` nos dois separadores.
_RE_ITEM_TITULO = re.compile(
    r"^(?P<area>.+?)\s*-\s*Edital\s*N[ºo°]\s*(?P<numero>.+?)\s*-\s*(?P<modalidade>.+?)\s*$",
    re.IGNORECASE,
)


def listar_processos(html: str) -> list[ItemListagem]:
    """Lê `.view-content-wrap .item` da listagem geral (`URL_LISTAGEM`
    filtrada por `PARAMS_LISTAGEM`) — sem lista de município nem cargo
    conhecida a priori, todo item processável (`.item_ar_licitacao a`
    com `href`) é incluído; quem decide se é concurso externo de verdade
    é `eh_concurso_externo_processavel`, não esta função."""
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for item in soup.select(".view-content-wrap .item"):
        link = item.select_one(".item_ar_licitacao a")
        if link is None or not link.get("href"):
            continue
        texto = link.get_text(" ", strip=True)
        if not texto:
            continue

        match = _RE_ITEM_TITULO.match(texto)
        if match:
            area = match.group("area").strip()
            numero_edital = re.sub(r"\s+", "", match.group("numero")).strip() or None
            modalidade = match.group("modalidade").strip()
        else:
            area, numero_edital, modalidade = texto, None, None

        objeto = item.select_one(".item_ar_objeto")
        resumo = objeto.get_text(" ", strip=True) if objeto else None
        if resumo in (None, "", "…", "..."):
            resumo = None

        itens.append(
            ItemListagem(
                area=area,
                numero_edital=numero_edital,
                modalidade=modalidade,
                url=urljoin(BASE_URL, link["href"]),
                resumo=resumo,
            )
        )

    return itens


def eh_concurso_externo_processavel(item: ItemListagem) -> bool:
    """Segunda camada de defesa (além do filtro `PARAMS_LISTAGEM` na
    origem) — ver docstring do módulo pro achado real (Edital 155/2026)
    que motivou isso. Rejeita item cujo texto (área + modalidade +
    número do edital, todos concatenados) contenha o radical "intern"
    (cobre "interna"/"interno", com ou sem acento, qualquer caixa)."""
    texto = _normalizar(" ".join(filter(None, [item.area, item.modalidade, item.numero_edital])))
    return "intern" not in texto


def _parsear_data(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        dia, mes, ano = texto.strip().split("/")
        return date(int(ano), int(mes), int(dia))
    except (ValueError, AttributeError):
        return None


def listar_documentos(html: str) -> list[Documento]:
    """Lê a única `<table>` da página fixa do edital (colunas Título/
    Link/Arquivo/Data) — ver docstring do módulo pro achado real de que a
    posição do link do PDF na linha não é fixa (por isso pega o primeiro
    `<a href>` da linha que termina em `.pdf`, não uma célula específica).
    Linha sem título ou sem nenhum link `.pdf` é ignorada (ato só com
    link pro Diário Oficial, sem PDF baixável — não é candidato a Anexo
    I de qualquer forma)."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table")
    if tabela is None:
        return []

    documentos: list[Documento] = []
    linhas = tabela.find_all("tr")
    for linha in linhas[1:]:  # 1ª linha é o cabeçalho (Título/Link/Arquivo/Data)
        celulas = linha.find_all("td")
        if not celulas:
            continue

        titulo_tag = celulas[0].find("div", class_="field--name-field-title")
        titulo = titulo_tag.get_text(" ", strip=True) if titulo_tag else None
        if not titulo:
            continue

        url_pdf = None
        for a in linha.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(".pdf"):
                url_pdf = urljoin(BASE_URL, href)
                break
        if url_pdf is None:
            continue

        data_texto = celulas[-1].get_text(" ", strip=True)
        documentos.append(Documento(titulo=titulo, url_pdf=url_pdf, data=_parsear_data(data_texto)))

    return documentos


#: título tem que COMEÇAR com "Edital" — exclui atos como "1ª RETIFICAÇÃO
#: DO EDITAL 01/2025" (só lista o que mudou, não a tabela inteira) e
#: "Convocação ...", "Resultado ...", "Ato de nomeação ...".
_RE_EDITAL_INICIO = re.compile(r"^edital\b", re.IGNORECASE)


def escolher_edital_com_anexo(documentos: list[Documento]) -> Documento | None:
    """Escolhe o PDF do edital que deve conter o ANEXO I (cargo/
    especialidade/jornada/vagas/salário) vigente — ver docstring do
    módulo pro achado real que motivou priorizar "compilado". `None` se
    nenhum documento tiver título começando com "Edital" (sem edital
    publicado ainda)."""
    candidatos = [d for d in documentos if _RE_EDITAL_INICIO.match(d.titulo.strip())]
    if not candidatos:
        return None
    compilados = [d for d in candidatos if "compilad" in d.titulo.lower()]
    grupo = compilados or candidatos
    return max(grupo, key=lambda d: d.data or date.min)


def montar_cargo_com_jornada(cargo: str, carga_horaria: str | None) -> str:
    """Ver docstring do módulo ("Achado de peso pro dedup") — sempre
    inclui a jornada no texto do cargo gravado quando conhecida, pra
    diferentes jornadas da MESMA especialidade não colidirem no dedup de
    `db.inserir_vaga_com_evidencia`. Não duplica se `carga_horaria` já
    estiver contido no texto do `cargo` (Gemini eventualmente pode
    devolver isso junto, embora o PROMPT peça campos separados)."""
    cargo_limpo = (cargo or "").strip()
    carga_limpa = (carga_horaria or "").strip()
    if not carga_limpa or carga_limpa.lower() in cargo_limpo.lower():
        return cargo_limpo
    return f"{cargo_limpo} ({carga_limpa})"


def identificador_externo(numero_edital: str | None, cargo_com_jornada: str) -> str:
    """Chave de dedup da evidência: número do edital (único por concurso
    nesta fonte, um único município) + slug do cargo JÁ COM a jornada
    embutida (ver `montar_cargo_com_jornada`) — duas linhas da mesma
    especialidade em jornadas diferentes geram identificadores
    diferentes, nenhuma é descartada por colisão de id."""
    slug_edital = re.sub(r"[^a-z0-9]+", "-", _normalizar(numero_edital or "sem-edital")).strip("-")
    slug_cargo = re.sub(r"[^a-z0-9]+", "-", _normalizar(cargo_com_jornada)).strip("-")
    return f"pbh-ibfc-{slug_edital}-{slug_cargo}"
