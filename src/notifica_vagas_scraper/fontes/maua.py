"""Parser da fonte Mauá/SP: processo seletivo simplificado (PSS) de
médicos especialistas por tempo determinado, publicado no subdomínio
dedicado `processoseletivo.maua.sp.gov.br` (site próprio da Secretaria de
Saúde, banca própria — sem organizadora terceirizada).

Investigado pelo `pesquisador-fonte` (fixtures em `docs/fixtures/maua_sp/`
no repo principal: listagem/detalhe do `/concursos` formal — banca
IBAM-SP, não relevante aqui —, a home do subdomínio de PSS, o PDF real do
Edital 57/2026 e uma amostra de notícias da API `Noticias/ListarAjax`).
Achados:

- A home do subdomínio (`/Home/`) sempre expõe o edital de PSS VIGENTE:
  link "Edital de Abertura <numero>" pro PDF
  (`/public/docs/edital_abertura_<numero>.pdf`) e o período de inscrição
  em texto solto, sem precisar navegar a API de notícias do site
  principal (`www.maua.sp.gov.br/Noticias/ListarAjax`) pra descobrir qual
  é o processo mais recente — mais simples e robusto que casar
  título/palavra-chave de notícia (a API de notícias existe e foi
  confirmada na investigação, mas fica de fora por ora: a home já
  resolve "qual é o PSS vigente agora" sozinha, que é o que o produto
  precisa). Cadência real confirmada na investigação: 15 PSS de médicos
  abertos desde 2025, 6 só em 2026 (~1/mês) — cabe folgado dentro da
  cadência de checagem de 3 dias do produto.
- Editais de retificação, quando existem, ficam comentados em HTML
  (`<!-- ... -->`) na mesma página até a próxima retificação real ser
  publicada — `extrair_edital_vigente` só considera link `<a>` de
  verdade (BeautifulSoup não materializa conteúdo dentro de comentário
  HTML como elemento), então nunca pega um link morto/comentado.
- Cargo/vagas/requisitos/carga horária só existem numa tabela dentro do
  PDF do edital cujas colunas saem embaralhadas em qualquer extração de
  texto linear (`pypdf`/`pdfplumber` — achado real, texto e acentuação
  fora de ordem mesmo o PDF sendo texto real, não escaneado) — por isso
  vêm de `gemini_pdf.extrair_vagas_de_pdf` (lê o PDF visualmente), não de
  regex contra o texto extraído. Testado contra o Edital 57/2026 real (o
  caso mais denso disponível: 10 especialidades médicas no mesmo edital
  — Cardiologista, Dermatologista, Ginecologista, Hematologista,
  Neurologista, Neurologista Pediátrico, Pneumologista, Psiquiatra,
  Psiquiatra Pediátrico, Reumatologista) e confirma que nenhuma das 10 é
  descartada.
- Salário é uma remuneração uniforme POR HORA ("R$ 130,00/hora +
  benefícios", igual pra todo cargo do Edital 57/2026) — fora do que o
  prompt compartilhado de `gemini_pdf` aceita como `salario` numérico (só
  monta número pra remuneração mensal fixa ou plantão fixo, deixa `null`
  de propósito pra "por hora"/"por aula", ver PROMPT em `gemini_pdf.py`)
  — mudar esse prompt compartilhado por TODAS as fontes só por causa
  desta é decisão fora de escopo aqui. O valor numérico da hora é extraído
  à parte, por regex determinístico contra o texto puro do PDF
  (`extrair_salario_por_hora_uniforme`), só quando o MESMO número aparece
  em toda ocorrência "R$ .../hora" do documento (heurística de
  uniformidade) — se um edital futuro tiver valor de hora DIFERENTE por
  especialidade, a função devolve `None` em vez de arriscar aplicar o
  valor errado a algum cargo (quem chama decide o que fazer com `None`,
  ver `rodar_maua.py`). Gravado com `salario_tipo="hora"` (migration 022
  do repo principal, decisão do usuário 2026-09-11) — antes dessa
  migration não existia "por hora" no enum (só 'mensal'/'plantao',
  migration 013), então as 10 vagas do Edital 57/2026 foram gravadas
  como "plantao" por aproximação e corrigidas manualmente depois que o
  enum ganhou o valor certo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

BASE_URL = "https://processoseletivo.maua.sp.gov.br"
URL_HOME = f"{BASE_URL}/Home/"

#: fonte é dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais).
MUNICIPIO = "Mauá"
UF = "SP"

__all__ = [
    "BASE_URL",
    "URL_HOME",
    "MUNICIPIO",
    "UF",
    "EditalVigente",
    "extrair_edital_vigente",
    "extrair_salario_por_hora_uniforme",
    "identificador_externo",
]


@dataclass
class EditalVigente:
    numero_edital: str | None
    pdf_url: str
    inscricoes_inicio: date | None
    inscricoes_fim: date | None


def _parsear_data(texto: str) -> date | None:
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


_RE_NUMERO_EDITAL = re.compile(r"(\d+/\d{4})")


def extrair_edital_vigente(html: str) -> EditalVigente | None:
    """Lê a home do subdomínio (`/Home/`): link "Edital de Abertura
    <numero>" (só o PDF visível de verdade na página — link comentado em
    HTML de uma retificação antiga não vira elemento `<a>` pro
    BeautifulSoup, ver docstring do módulo) + período de inscrição em
    texto solto próximo do cabeçalho "Período de inscrição". `None` se
    não achar nenhum link de edital de abertura (página fora do ar/sem
    PSS vigente no momento — não deve travar o cron, só resultar em 0
    vaga processada nesse ciclo)."""
    soup = BeautifulSoup(html, "html.parser")

    link = None
    for a in soup.find_all("a", href=True):
        texto_link = a.get_text(strip=True)
        if a["href"].lower().endswith(".pdf") and re.match(
            r"^edital\s+de\s+abertura", texto_link, re.IGNORECASE
        ):
            link = a
            break
    if link is None:
        return None

    pdf_url = link["href"]
    if pdf_url.startswith("/"):
        pdf_url = BASE_URL + pdf_url

    match_numero = _RE_NUMERO_EDITAL.search(link.get_text(strip=True))
    numero_edital = match_numero.group(1) if match_numero else None

    texto_pagina = soup.get_text(" ", strip=True)
    inscricoes_inicio = inscricoes_fim = None
    idx = texto_pagina.find("Período de inscrição")
    if idx != -1:
        datas = re.findall(r"\d{2}/\d{2}/\d{4}", texto_pagina[idx : idx + 300])
        if len(datas) >= 2:
            inscricoes_inicio = _parsear_data(datas[0])
            inscricoes_fim = _parsear_data(datas[1])

    return EditalVigente(
        numero_edital=numero_edital,
        pdf_url=pdf_url,
        inscricoes_inicio=inscricoes_inicio,
        inscricoes_fim=inscricoes_fim,
    )


_RE_VALOR_HORA = re.compile(r"R\$\s*(\d+(?:[.,]\d{2})?)\s*/")


def extrair_salario_por_hora_uniforme(texto_pdf: str) -> float | None:
    """Ver docstring do módulo pro motivo de existir fora do prompt do
    Gemini. Cada ocorrência de "R$ <valor>/" só conta como remuneração por
    hora se a palavra "hora" aparecer nos 150 caracteres seguintes (a
    tabela do PDF embaralha colunas — "hora" pode ficar bem depois do
    valor, numa linha diferente da reconstrução espacial do
    pdfplumber/pypdf — mas nunca tão longe a ponto de pertencer a outra
    célula/linha da tabela, confirmado contra o Edital 57/2026 real: as
    10 ocorrências batem dentro de ~90 caracteres). `None` se não achar
    nenhum valor "R$ .../hora" no PDF, ou se achar mais de um valor
    DIFERENTE (edital com remuneração por hora não-uniforme entre cargos
    — não arrisca aplicar o valor errado a nenhum, quem chama decide o
    que fazer com `None`)."""
    valores: set[float] = set()
    for match in _RE_VALOR_HORA.finditer(texto_pdf):
        janela = texto_pdf[match.end() : match.end() + 150]
        if not re.search(r"\bhora", janela, re.IGNORECASE):
            continue
        bruto = match.group(1).replace(".", "").replace(",", ".")
        try:
            valores.add(float(bruto))
        except ValueError:
            continue
    if len(valores) != 1:
        return None
    return next(iter(valores))


def identificador_externo(numero_edital: str | None, cargo: str) -> str:
    """Chave de dedup: fonte é dedicada a 1 único município, então basta
    número do edital (quando identificado) + slug do cargo pra ser
    estável e sem colisão entre execuções (mesmo padrão de
    `fontes/itamonte_mg.py`)."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    slug_edital = re.sub(r"[^a-z0-9]+", "-", (numero_edital or "sem-numero").lower()).strip("-")
    return f"maua-sp-{slug_edital}-{slug_cargo}"
