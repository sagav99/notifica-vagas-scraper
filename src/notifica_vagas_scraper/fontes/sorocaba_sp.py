"""Parser da fonte Prefeitura de Sorocaba/SP: WordPress (portal de
notícias `noticias.sorocaba.sp.gov.br`), banca organizadora Fundação
VUNESP. Investigado pelo `pesquisador-fonte` (fixtures reais em
`docs/fixtures/sorocaba_sp/` no repo principal): `api_search_concurso.json`,
`listagem_recente_wp_json.json`, `media_exemplo_post_com_pdf.json`,
`edital_abertura_01_2026_saude.pdf` (Concurso Público 01/2026, alvo real: 1
Técnico de Enfermagem + 6 especialidades médicas), e
`noticia_edital01_2026_bloqueada_periodo_eleitoral.html`/
`posts_fevereiro_2026_vazio_bloqueado.json` (achado: o post do Concurso
01/2026 está temporariamente indisponível — tanto no HTML quanto na API
REST — por regra de "período eleitoral" que o próprio WordPress aplica.
**Não é bloqueio anti-bot nem bug**: é uma regra de conteúdo por data.
Este parser é genérico e não tenta contornar isso — simplesmente não verá
esse post específico enquanto o bloqueio durar; quando o período eleitoral
terminar (ou o conteúdo for restaurado), o parser volta a capturá-lo
normalmente no próximo cron.

Achados que moldam este módulo (diferente do `fontes/wordpress_editais.py`
genérico, que usa `search=` + Gemini pro PDF):

- `/wp-json/wp/v2/search` tem índice fraco/não confiável pra esta fonte
  (achado real, `api_search_concurso.json`: retorna "Concurso elegerá Miss
  e Mister Melhor Idade..." e convocação antiga de GCM, mas NÃO o edital de
  concurso público de fato) — a busca de descoberta usa sempre
  `/wp-json/wp/v2/posts?per_page=N&orderby=date` (listagem recente,
  `listar_posts_recentes`) + filtro por palavra-chave em
  `title.rendered`/`slug` (`eh_post_candidato`), nunca `search=`.
- PDF do edital é texto real, tabela de cargo/salário/requisitos extraível
  de forma limpa via `pypdf` (confirmado no Edital de Abertura 01/2026,
  0 caracteres embaralhados, diferente do achado de Mauá/SP com colunas
  fora de ordem) — por isso a extração de cargo/salário/requisitos AQUI é
  100% determinística via regex (`extrair_cargos_do_pdf`), sem Gemini:
  mais barato e mais previsível que uma leitura visual pra um formato de
  tabela já confirmado estável.
- `banca_organizadora` ("Fundação VUNESP") e `tipo_oportunidade`
  ("concurso_efetivo", regime estatutário conforme item 1.5 do Edital) são
  lidos direto do texto do próprio PDF (`extrair_banca_organizadora`), não
  hardcoded como fato externo — mesmo quando o valor é público e estável,
  ler do documento real é mais robusto a mudança de banca num edital
  futuro.
- `tem_prova`/`exige_curriculo` também são determinísticos: o Edital
  01/2026 só cita "Prova Objetiva" (Capítulo VII) pra todos os cargos, sem
  nenhuma fase de "análise de currículo"/"prova de títulos" — daí
  `tem_prova=True` sempre que o PDF cita prova objetiva/escrita, e
  `exige_curriculo` só vira `True` se o PDF citar título/currículo como
  fase de avaliação (nenhum encontrado neste edital real).
- Salário é remuneração por hora ("R$ 108,40/hora" pros 6 cargos médicos,
  "R$ 23,49/hora" pro Técnico de Enfermagem) — gravado com
  `salario_tipo="hora"` (migration 022 do repo principal).
- Extração de cargo via `pdfplumber.Page.extract_tables()` (não texto
  linear + regex): o texto linear (`pypdf`/`pdfplumber.extract_text()`)
  embaralha a tabela — "Total de vagas"/"Jornada" viram colunas soltas
  antes das linhas de cargo (mesmo tipo de achado que levou Mauá/SP a usar
  Gemini) — mas `extract_tables()` reconstrói célula por célula
  corretamente (confirmado contra as 2 páginas do Edital 01/2026: 7
  cargos, nenhuma célula fora de ordem). A tabela se repete 1x por página
  nova do PDF com o MESMO cabeçalho literal (`"Cargos", "Total\\nde\\nvagas",
  ...`) — `extrair_cargos_do_pdf` reconhece o cabeçalho e pula, sem
  precisar saber de antemão em quais páginas a tabela aparece.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"

BASE_URL = "https://noticias.sorocaba.sp.gov.br"
URL_POSTS = f"{BASE_URL}/wp-json/wp/v2/posts"
URL_MEDIA = f"{BASE_URL}/wp-json/wp/v2/media"

#: fonte dedicada a 1 único município — sem necessidade de casar título
#: contra catálogo (diferente de fontes multi-município tipo FGV/Instituto
#: Mais).
MUNICIPIO = "Sorocaba"
UF = "SP"
ORGAO_PADRAO = "Prefeitura Municipal de Sorocaba"

#: palavra-chave em `title.rendered`/`slug` que indica candidato a edital
#: de concurso/processo seletivo — mesmo espírito de `eh_edital_abertura`
#: de `guiricema_mg.py`, adaptado pra listagem genérica de posts (sem
#: campo de categoria confiável aqui).
_RE_PALAVRA_CHAVE = re.compile(r"concurso\s+p[uú]blico|edital|processo\s+seletivo", re.IGNORECASE)

#: título/slug que indica claramente NÃO ser edital de abertura (notícia
#: sobre concurso de beleza, convocação, resultado etc.) — mesmo cuidado
#: de `guiricema_mg._RE_EXCLUIR_TITULO`.
_RE_EXCLUIR = re.compile(
    r"resultado|homologa|classifica|convoca|julgamento|recurso|habilita|miss|mister",
    re.IGNORECASE,
)

__all__ = [
    "USER_AGENT",
    "BASE_URL",
    "URL_POSTS",
    "URL_MEDIA",
    "MUNICIPIO",
    "UF",
    "ORGAO_PADRAO",
    "Post",
    "Cargo",
    "EditalExtraido",
    "listar_posts_recentes",
    "eh_post_candidato",
    "extrair_pdf_do_html",
    "escolher_pdf_de_media",
    "extrair_numero_edital",
    "extrair_banca_organizadora",
    "extrair_tem_prova",
    "extrair_exige_curriculo",
    "extrair_cargos_do_pdf",
    "extrair_edital",
    "identificador_externo",
]


@dataclass
class Post:
    id: int
    titulo: str
    slug: str
    data: date | None
    link: str
    conteudo_html: str


def _parsear_data(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto).date()
    except ValueError:
        return None


def listar_posts_recentes(dados_json: list[dict]) -> list[Post]:
    """Lê a resposta de `GET /wp-json/wp/v2/posts?per_page=N&orderby=date`
    (schema real confirmado: `id`, `slug`, `title.rendered`,
    `content.rendered`, `link`, `date`). Item sem `id` reconhecível é
    ignorado (nunca quebra o parsing dos demais)."""
    posts: list[Post] = []
    for item in dados_json:
        if not isinstance(item, dict) or "id" not in item:
            continue
        posts.append(
            Post(
                id=item["id"],
                titulo=item.get("title", {}).get("rendered", ""),
                slug=item.get("slug", ""),
                data=_parsear_data(item.get("date")),
                link=item.get("link", ""),
                conteudo_html=item.get("content", {}).get("rendered", ""),
            )
        )
    return posts


def eh_post_candidato(post: Post) -> bool:
    """`True` quando título OU slug indicam concurso/edital/processo
    seletivo E não batem a exclusão óbvia (notícia sobre concurso de
    beleza, convocação, resultado etc. — ver docstring do módulo)."""
    texto = f"{post.titulo} {post.slug}"
    return bool(_RE_PALAVRA_CHAVE.search(texto)) and not _RE_EXCLUIR.search(texto)


_RE_HREF_PDF = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)


def extrair_pdf_do_html(conteudo_html: str) -> str | None:
    """1ª tentativa de achar o PDF do edital: link direto dentro do
    `content.rendered` do post. `None` se não achar nenhum (quem chama cai
    pro fallback via `/wp-json/wp/v2/media?parent=<id>`,
    `escolher_pdf_de_media`)."""
    match = _RE_HREF_PDF.search(conteudo_html)
    return match.group(1) if match else None


def escolher_pdf_de_media(dados_media_json: list[dict]) -> str | None:
    """2º fallback: resposta de `GET /wp-json/wp/v2/media?parent=<id>`
    (schema real: `mime_type`/`source_url`) — usa o 1º anexo PDF
    encontrado. `None` se a lista vier vazia ou sem nenhum PDF."""
    for item in dados_media_json:
        if isinstance(item, dict) and item.get("mime_type") == "application/pdf" and item.get("source_url"):
            return item["source_url"]
    return None


_RE_NUMERO_EDITAL = re.compile(r"CONCURSO P[UÚ]BLICO\s*N[ºo°]?\s*(\d+/\d{4})", re.IGNORECASE)


def extrair_numero_edital(texto_pdf: str) -> str | None:
    match = _RE_NUMERO_EDITAL.search(texto_pdf)
    return match.group(1) if match else None


def extrair_banca_organizadora(texto_pdf: str) -> str | None:
    """Lê a banca organizadora direto do texto do PDF (não hardcoded) —
    Edital 01/2026 cita "Fundação VUNESP" no preâmbulo. `None` se o texto
    não citar nenhuma banca conhecida (edital futuro sem organizadora
    terceirizada, por exemplo)."""
    if re.search(r"funda[cç][aã]o\s+vunesp", texto_pdf, re.IGNORECASE):
        return "Fundação VUNESP"
    return None


def extrair_tem_prova(texto_pdf: str) -> bool:
    return bool(re.search(r"prova\s+objetiva|prova\s+escrita", texto_pdf, re.IGNORECASE))


def extrair_exige_curriculo(texto_pdf: str) -> bool:
    return bool(re.search(r"prova\s+de\s+t[ií]tulos|an[aá]lise\s+de\s+curr[ií]culo", texto_pdf, re.IGNORECASE))


@dataclass
class Cargo:
    nome: str
    numero_vagas: int
    vagas_ampla_concorrencia: int
    vagas_pcd: int
    valor_hora: float
    requisitos: str
    carga_horaria_semanal: int | None


#: cabeçalho literal da tabela de cargos (item 1.2 do Edital), repetido em
#: cada página nova do PDF (ver docstring do módulo) — colunas exatas
#: devolvidas por `pdfplumber.Page.extract_tables()` pra essa tabela
#: específica. Uma linha que bate este padrão é o cabeçalho (pulada), não
#: dado de cargo.
_COLUNAS_CABECALHO = ("Cargos", "Salário (R$)", "Requisitos Exigidos")

_RE_VALOR_HORA = re.compile(r"(\d+,\d{2})/hora")


def _texto_limpo(bruto: str | None) -> str:
    return re.sub(r"\s+", " ", bruto or "").strip()


def _valor_para_float(bruto: str) -> float:
    return float(bruto.replace(".", "").replace(",", "."))


def _eh_linha_cabecalho(linha: list[str | None]) -> bool:
    celulas = [_texto_limpo(c) for c in linha]
    return len(celulas) >= 3 and celulas[0] == _COLUNAS_CABECALHO[0] and _COLUNAS_CABECALHO[1] in celulas


def extrair_cargos_do_pdf(tabelas: list[list[list[str | None]]]) -> list[Cargo]:
    """Parseia a tabela "Dos Cargos" (item 1.2 do Edital) a partir das
    tabelas já extraídas via `pdfplumber.Page.extract_tables()` (1 tabela
    por página onde a tabela de cargos aparece — passe TODAS as tabelas de
    TODAS as páginas do PDF, cabeçalho repetido/tabela de outra seção como
    "Taxa de Inscrição" é filtrado por não bater `_eh_linha_cabecalho` nem
    ter 7 colunas). Espera 7 colunas por linha: cargo, total de vagas,
    vagas ampla concorrência, vagas PCD, "<valor>/hora", requisitos,
    jornada semanal. Linha fora desse formato (célula faltando, sem
    "/hora" na coluna de salário) é pulada, não derruba o parsing das
    demais — devolve lista vazia se nenhuma tabela bater o formato
    esperado (edital fora do padrão usual, não é erro em si)."""
    cargos: list[Cargo] = []
    for tabela in tabelas:
        if not tabela:
            continue
        for linha in tabela:
            if _eh_linha_cabecalho(linha) or len(linha) != 7:
                continue
            nome_cargo = _texto_limpo(linha[0])
            match_salario = _RE_VALOR_HORA.search(_texto_limpo(linha[4]))
            if not nome_cargo or match_salario is None:
                continue
            try:
                total = int(_texto_limpo(linha[1]))
                ampla = int(_texto_limpo(linha[2]))
                pcd = int(_texto_limpo(linha[3]))
            except ValueError:
                continue

            jornada_texto = _texto_limpo(linha[6])
            jornada = int(jornada_texto) if jornada_texto.isdigit() else None

            cargos.append(
                Cargo(
                    nome=nome_cargo,
                    numero_vagas=total,
                    vagas_ampla_concorrencia=ampla,
                    vagas_pcd=pcd,
                    valor_hora=_valor_para_float(match_salario.group(1)),
                    requisitos=_texto_limpo(linha[5]),
                    carga_horaria_semanal=jornada,
                )
            )
    return cargos


@dataclass
class EditalExtraido:
    numero_edital: str | None
    banca_organizadora: str | None
    tem_prova: bool
    exige_curriculo: bool
    cargos: list[Cargo]


def extrair_edital(texto_pdf: str, tabelas: list[list[list[str | None]]]) -> EditalExtraido:
    """`texto_pdf`: texto linear de todas as páginas (`extract_text()`,
    usado só pra regex de preâmbulo — numero/banca/prova/currículo, sem
    depender de estrutura de coluna). `tabelas`: `extract_tables()` de
    todas as páginas, usado só pra `extrair_cargos_do_pdf` (ver docstring
    do módulo pro motivo de cargo vir de tabela e não de texto linear)."""
    return EditalExtraido(
        numero_edital=extrair_numero_edital(texto_pdf),
        banca_organizadora=extrair_banca_organizadora(texto_pdf),
        tem_prova=extrair_tem_prova(texto_pdf),
        exige_curriculo=extrair_exige_curriculo(texto_pdf),
        cargos=extrair_cargos_do_pdf(tabelas),
    )


def identificador_externo(numero_edital: str | None, cargo: str) -> str:
    """Chave de dedup: fonte é dedicada a 1 único município, então basta
    número do edital (quando identificado) + slug do cargo (mesmo padrão
    de `fontes/maua.py`)."""
    slug_cargo = re.sub(
        r"[^a-z0-9]+",
        "-",
        unicodedata.normalize("NFKD", cargo).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip("-")
    slug_edital = re.sub(r"[^a-z0-9]+", "-", (numero_edital or "sem-numero").lower()).strip("-")
    return f"sorocaba-sp-{slug_edital}-{slug_cargo}"
