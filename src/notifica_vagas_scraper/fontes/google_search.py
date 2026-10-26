"""Cliente da Serper (google.serper.dev) para descoberta ampla de concursos.

É o equivalente de busca web geral do ``google_news``: conserva as mesmas
queries focadas em médico, mas consulta páginas indexadas em geral —
inclusive editais que nunca foram notícia. Antes usava a Google Custom
Search JSON API direto; trocado pra Serper em 2026-09-09 (decisão do
usuário) depois de dias sem conseguir resolver bloqueio de faturamento na
conta Google — Serper devolve resultado do Google de verdade (mesma
cobertura/qualidade), sem precisar de Google Cloud Console/faturamento.

Diferença de modelo de cota importante: a Google Custom Search API tinha
cota diária grátis que renovava (100/dia pra sempre); o free tier da
Serper é um saldo único de créditos que **não renova** (2.500 no
cadastro) — por isso o chamador limita queries por execução pra esticar
esse saldo por meses, não só por dia (ver ``QUERIES_POR_EXECUCAO``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .google_news import QUERIES as QUERIES_NEWS

BASE_URL = "https://google.serper.dev/search"

#: Consultas da Serper (1 crédito cada). Enxugadas em 2026-10-24 (decisão
#: do usuário, análise de `serper_consultas_log`): ficam as que mais
#: renderam vaga médica aprovada; as demais repetiam link já trazido por
#: estas (ou rendiam 0). O Google News RSS (grátis) segue com a lista
#: completa em `google_news.QUERIES`.
QUERIES: tuple[str, ...] = (
    "concurso público médico plantonista",
    "concurso médico ortopedista",
    "concurso médico generalista",
    "concurso médico ginecologista obstetra",
    "vagas médico concurso prefeitura",
    "concurso médico PSF",
    "concurso médico",
    "concurso médico SP",
)

#: Tiradas em 2026-10-26 para abrir espaço ao Sul sem aumentar o total:
#: "concurso médico psiquiatra" (64 vagas/17 execuções) e "concurso médico da
#: família ESF" (88; sobrepõe "PSF"), as de menor rendimento das 10.
#:
#: Sul (PR/SC/RS), terça e quinta, os dias em que MG/SP não rodam (pedido do
#: usuário, 2026-10-26): 1 consulta por estado.
QUERIES_SUL: tuple[str, ...] = (
    "concurso público médico Paraná",
    "concurso público médico Santa Catarina",
    "concurso público médico Rio Grande do Sul",
)

#: 8 queries seg/qua/sex (24) + 3 do Sul ter/qui (6) = 30 créditos/semana,
#: o mesmo orçamento de antes da inclusão do Sul.
QUERIES_POR_EXECUCAO = 10

#: Janelas de recência disponíveis pro backfill retroativo (decisão do
#: usuário, 2026-09-08): em vez de só "tudo de uma vez" (sem filtro
#: nenhum, resultado dominado por ruído antigo), o backfill roda em
#: estágios — primeiro o que é mais provável ainda estar com inscrição
#: aberta (semana/mês), só então indo pra janelas maiores. Sintaxe do
#: parâmetro ``tbs`` (mesmo filtro de data da busca normal do Google,
#: documentado pela Serper): ``qdr:d<N>`` filtra pelos últimos N dias.
JANELAS_BACKFILL: dict[str, str | None] = {
    "semana": "qdr:d7",
    "mes": "qdr:d30",
    "trimestre": "qdr:d90",
    "tudo": None,
}

#: cron normal (fora de backfill) restringe aos últimos 3 dias: roda
#: seg/qua/sex, então a segunda precisa cobrir sexta+fim de semana.
JANELA_DIAS = 3


@dataclass
class ItemBusca:
    titulo: str
    link: str
    resumo: str | None
    publicado_em: datetime | None
    consulta: str | None = None


#: Terça pega desde sexta (fim de semana sem edital novo) e quinta desde
#: quarta; 4 dias cobre os dois sem custo extra (1 crédito por consulta).
JANELA_DIAS_SUL = 4


def montar_parametros(
    query: str, *, backfill: bool = False, janela: str | None = None, janela_dias: int = JANELA_DIAS
) -> dict[str, str | int]:
    """Corpo (JSON) de uma única consulta à Serper.

    No cron normal (`backfill=False`), ``tbs`` pede resultados dos
    últimos dois dias. Com `backfill=True`, aceita `janela` (uma chave de
    `JANELAS_BACKFILL`: "semana"/"mes"/"trimestre"/"tudo") pra fazer o
    backfill em estágios de recência — `janela=None` (ou omitido) mantém o
    comportamento antigo de backfill sem filtro nenhum ("tudo").
    """
    parametros: dict[str, str | int] = {
        "q": query,
        "num": 10,
        "hl": "pt",
        "gl": "br",
    }
    if not backfill:
        parametros["tbs"] = f"qdr:d{janela_dias}"
    elif janela is not None:
        tbs = JANELAS_BACKFILL[janela]
        if tbs is not None:
            parametros["tbs"] = tbs
    return parametros


def listar_itens(resposta: dict) -> list[ItemBusca]:
    """Converte a resposta JSON da Serper em itens utilizáveis.

    Resultados orgânicos vêm em ``organic`` (não ``items``, como na antiga
    Google Custom Search API). Uma resposta válida sem ``organic`` é
    simplesmente uma busca sem resultado. Campos opcionais ou itens
    incompletos são ignorados, sem interromper o lote.

    ``publicado_em`` sempre fica `None`: a Serper devolve data em texto
    livre e formato inconsistente (relativo tipo "6 days ago", absoluto
    tipo "4 de out. de 2024", ou ausente) — sem valor estruturado
    confiável pra parsear, e nenhuma lógica downstream depende desse campo
    hoje (só informativo), então não vale arriscar parsing frágil.
    """
    bruto = resposta.get("organic", [])
    if not isinstance(bruto, list):
        return []
    itens: list[ItemBusca] = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        titulo = item.get("title")
        link = item.get("link")
        if not isinstance(titulo, str) or not titulo.strip() or not isinstance(link, str) or not link.strip():
            continue
        resumo = item.get("snippet")
        itens.append(
            ItemBusca(
                titulo=titulo.strip(),
                link=link.strip(),
                resumo=resumo.strip() if isinstance(resumo, str) and resumo.strip() else None,
                publicado_em=None,
            )
        )
    return itens
