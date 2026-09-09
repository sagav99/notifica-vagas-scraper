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

from .google_news import QUERIES

BASE_URL = "https://google.serper.dev/search"

#: saldo de créditos grátis do cadastro é fixo (2.500, sem renovação) —
#: 20 queries por execução, 1x/dia, dura ~125 dias (~4 meses) antes de
#: precisar de plano pago ou reavaliar (decisão do usuário, 2026-09-09).
QUERIES_POR_EXECUCAO = 20

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

#: cron normal (fora de backfill) restringe aos últimos 2 dias.
JANELA_DIAS = 2


@dataclass
class ItemBusca:
    titulo: str
    link: str
    resumo: str | None
    publicado_em: datetime | None


def montar_parametros(
    query: str, *, backfill: bool = False, janela: str | None = None
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
        parametros["tbs"] = f"qdr:d{JANELA_DIAS}"
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
