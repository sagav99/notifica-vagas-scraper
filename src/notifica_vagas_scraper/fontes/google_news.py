"""Descoberta ampla via Google News RSS — busca por termo, sem custo, sem
API key (`news.google.com/rss/search`). Pedido do usuário (2026-09-05):
complementar os scrapers site-a-site com uma varredura barata que pega
vaga já indexada/divulgada por veículo de notícia, sem depender de mapear
cada banca/prefeitura manualmente. Prioridade do produto (CLAUDE.md) é
saúde/médicos, então as queries são todas focadas nisso.

RSS de cada busca traz até ~100 itens recentes: título, link (redirect do
Google News, resolvido à parte — ver `scripts/rodar_descoberta_google_news.py`),
data de publicação e fonte. Formato RFC 822 de data (`pubDate`), parseado
com `email.utils.parsedate_to_datetime` (stdlib, sem dependência nova).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

BASE_URL = "https://news.google.com/rss/search"

#: Termos de busca focados em concurso médico (prioridade #1 do produto,
#: CLAUDE.md) — mistura termo genérico, geografia (MG/SP) e especialidade,
#: pra maximizar cobertura sem virar ruído (dedup por URL faz o resto,
#: ver `db.registrar_sinal_descoberta`/`inserir_vaga_com_evidencia`).
QUERIES: tuple[str, ...] = (
    "concurso médico",
    "concurso médico MG",
    "concurso médico SP",
    "concurso médico Minas Gerais",
    "concurso médico São Paulo",
    "edital médico prefeitura",
    "processo seletivo médico prefeitura",
    "concurso público médico plantonista",
    "concurso médico clínico geral",
    "concurso médico pediatra",
    "concurso médico ginecologista obstetra",
    "concurso médico psiquiatra",
    "concurso médico ortopedista",
    "concurso médico anestesiologista",
    "concurso médico cardiologista",
    "vagas médico concurso prefeitura",
    "concurso secretaria de saúde médico",
    "PSS médico prefeitura",
    "concurso médico da família ESF",
    "concurso hospital municipal médico",
)


@dataclass
class ItemNoticia:
    titulo: str
    link: str
    publicado_em: datetime | None
    fonte_nome: str | None


def montar_url_busca(query: str) -> str:
    return f"{BASE_URL}?q={quote(query)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


def _parsear_data(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        data = parsedate_to_datetime(texto)
    except (TypeError, ValueError):
        return None
    if data.tzinfo is None:
        data = data.replace(tzinfo=timezone.utc)
    return data


def listar_itens(xml_texto: str) -> list[ItemNoticia]:
    """Parseia o RSS devolvido por `montar_url_busca`. Devolve lista vazia
    (não levanta erro) se o XML vier malformado ou sem nenhum `<item>` —
    tratado como "sem resultado pra essa busca hoje", igual ao padrão de
    `instar.listar_itens_abertos` pro sentinela de lista vazia."""
    try:
        raiz = ET.fromstring(xml_texto)
    except ET.ParseError:
        return []

    itens = []
    for item in raiz.iterfind(".//item"):
        titulo = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not titulo or not link:
            continue
        fonte_el = item.find("source")
        itens.append(
            ItemNoticia(
                titulo=titulo,
                link=link,
                publicado_em=_parsear_data(item.findtext("pubDate")),
                fonte_nome=(fonte_el.text.strip() if fonte_el is not None and fonte_el.text else None),
            )
        )
    return itens


def filtrar_recentes(itens: list[ItemNoticia], *, dentro_de_dias: int, agora: datetime | None = None) -> list[ItemNoticia]:
    """Só itens publicados dentro da janela — cadência é 1x/dia, mas usar
    uma janela um pouco maior (padrão configurável pelo chamador) dá
    margem contra um ciclo do cron atrasado/pulado sem reprocessar o RSS
    inteiro de novo (dedup por URL já cobre reprocessamento sem duplicar
    dado, então folga aqui é sem custo real de dado errado, só de chamada
    extra de rede/Gemini pra item já visto)."""
    referencia = agora or datetime.now(timezone.utc)
    limite = referencia.timestamp() - dentro_de_dias * 86400
    return [item for item in itens if item.publicado_em is not None and item.publicado_em.timestamp() >= limite]
