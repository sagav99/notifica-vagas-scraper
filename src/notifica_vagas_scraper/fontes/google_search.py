"""Cliente da Google Custom Search API para descoberta ampla de concursos.

É o equivalente de busca web geral do ``google_news``: conserva as mesmas
queries focadas em médico, mas consulta páginas indexadas em geral — inclusive
editais que nunca foram notícia. A API cobra por consulta acima da cota diária
gratuita; por isso o chamador sempre limita a quantidade antes de fazer rede.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .google_news import QUERIES

BASE_URL = "https://www.googleapis.com/customsearch/v1"
COTA_DIARIA_GRATUITA = 100
JANELA_DIAS = 2

#: Janelas de recência disponíveis pro backfill retroativo (decisão do
#: usuário, 2026-09-08): em vez de só "tudo de uma vez" (sem filtro
#: nenhum, resultado dominado por ruído antigo), o backfill roda em
#: estágios — primeiro o que é mais provável ainda estar com inscrição
#: aberta (semana/mês), só then indo pra janelas maiores. Cada estágio
#: já cabe várias vezes dentro da cota diária gratuita (20 queries por
#: rodada, cota de 100/dia — ver `COTA_DIARIA_GRATUITA`), então dá pra
#: rodar todos os estágios no mesmo dia sem estourar. Sintaxe de
#: `dateRestrict`: https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list
JANELAS_BACKFILL: dict[str, str | None] = {
    "semana": "d7",
    "mes": "m1",
    "trimestre": "m3",
    "tudo": None,
}


@dataclass
class ItemBusca:
    titulo: str
    link: str
    resumo: str | None
    publicado_em: datetime | None


def montar_parametros(
    query: str, *, api_key: str, engine_id: str, backfill: bool = False, janela: str | None = None
) -> dict[str, str | int]:
    """Parâmetros de uma única consulta à API.

    No cron normal (`backfill=False`), ``dateRestrict`` pede resultados dos
    últimos dois dias. Com `backfill=True`, aceita `janela` (uma chave de
    `JANELAS_BACKFILL`: "semana"/"mes"/"trimestre"/"tudo") pra fazer o
    backfill em estágios de recência — `janela=None` (ou omitido) mantém o
    comportamento antigo de backfill sem filtro nenhum ("tudo").
    """
    parametros: dict[str, str | int] = {
        "key": api_key,
        "cx": engine_id,
        "q": query,
        "num": 10,
        "hl": "pt-BR",
        "gl": "br",
    }
    if not backfill:
        parametros["dateRestrict"] = f"d{JANELA_DIAS}"
    elif janela is not None:
        date_restrict = JANELAS_BACKFILL[janela]
        if date_restrict is not None:
            parametros["dateRestrict"] = date_restrict
    return parametros


def _parsear_data(texto: object) -> datetime | None:
    if not isinstance(texto, str) or not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None


def _encontrar_data(item: dict) -> datetime | None:
    metatags = item.get("pagemap", {}).get("metatags", [])
    if not isinstance(metatags, list):
        return None
    for metatag in metatags:
        if not isinstance(metatag, dict):
            continue
        for campo in ("article:published_time", "datePublished", "date", "publishdate"):
            data = _parsear_data(metatag.get(campo))
            if data is not None:
                return data
    return None


def listar_itens(resposta: dict) -> list[ItemBusca]:
    """Converte a resposta JSON da API em itens utilizáveis.

    Uma resposta válida sem ``items`` é simplesmente uma busca sem resultado.
    Campos opcionais ou itens incompletos são ignorados, sem interromper o lote.
    """
    bruto = resposta.get("items", [])
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
                publicado_em=_encontrar_data(item),
            )
        )
    return itens
