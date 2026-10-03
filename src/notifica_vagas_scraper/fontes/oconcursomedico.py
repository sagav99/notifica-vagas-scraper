"""Conferência cruzada com O Concurso Médico (oconcursomedico.com), site
curado à mão que lista concursos de médico do país todo — achado
2026-10-03.

O site é uma SPA estática; a lista vem de um único GET num Google Apps
Script (`FEED_URL`), JSON com todos os concursos. Os campos são digitados
por uma equipe (já vimos erro: FHCGV listado como "Belém PA" com título
de outro estado), então **só usamos como índice de descoberta**: de cada
item aproveitamos cidade/UF, datas de inscrição e o link do edital
(`editais[].link`, que aponta pro PDF no site da banca/prefeitura). Cargo,
salário e vagas continuam vindo do PDF via Gemini, nunca do texto deles —
mesmo princípio do Ache Concursos e da PCI.

Uma requisição por execução (a cada 3 dias), sem navegação no site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

FEED_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbyOO3_5cmla4VOvW6lvImUPdGKurBnXEGIuUNJO3S1SJfhK-2wYFs_gf-lnStW0MOBL/exec"
)
SITE_URL = "https://oconcursomedico.com"
STATUS_INTERESSANTES = {"abertas", "previsto"}


@dataclass
class Candidato:
    id: int
    titulo: str
    cidade: str
    uf: str
    status: str
    inscricoes_inicio: date | None
    inscricoes_fim: date | None
    edital_url: str | None


def _parsear_data(valor: object) -> date | None:
    if not isinstance(valor, str):
        return None
    try:
        return datetime.strptime(valor.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _url_do_edital(item: dict) -> str | None:
    """Primeiro link http(s) do anexo chamado "Edital". Anexo sem link
    (string vazia) é comum em concurso ainda "previsto"."""
    editais = item.get("editais")
    if not isinstance(editais, list):
        return None
    for anexo in editais:
        if not isinstance(anexo, dict):
            continue
        nome = str(anexo.get("nome") or "").strip().lower()
        link = str(anexo.get("link") or "").strip()
        if nome.startswith("edital") and link.startswith(("http://", "https://")):
            return link
    return None


def listar_candidatos(feed: dict | str, ufs: tuple[str, ...] = ("MG", "SP")) -> list[Candidato]:
    dados = json.loads(feed) if isinstance(feed, str) else feed
    candidatos: list[Candidato] = []
    for item in dados.get("concursos", []):
        status = str(item.get("status") or "").strip().lower()
        uf = str(item.get("uf") or "").strip().upper()
        cidade = str(item.get("cidade") or "").strip()
        if status not in STATUS_INTERESSANTES or uf not in ufs or not cidade:
            continue
        candidatos.append(
            Candidato(
                id=int(item["id"]),
                titulo=str(item.get("titulo") or "").strip(),
                cidade=cidade,
                uf=uf,
                status=status,
                inscricoes_inicio=_parsear_data(item.get("inscricaoInicio")),
                inscricoes_fim=_parsear_data(item.get("inscricaoFim")),
                edital_url=_url_do_edital(item),
            )
        )
    return candidatos
