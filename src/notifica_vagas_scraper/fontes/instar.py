"""Parser da plataforma Instar Tecnologia — endpoint de dados abertos de
concursos usado por várias prefeituras de MG (achado em 2026-09-01,
discussão do usuário fora desta sessão, ver TAREFAS.md do repo principal).

Endpoint: `GET {url_prefeitura}/portal/dados-abertos/concursos/{ano}` —
JSON público, sem autenticação, sem CSRF, sem paginação real. Formato:
`{"dados": [...]}`, onde cada item tem `titulo`, `situacao` ("Aberto" ou
"Concluído", entre outros ainda não catalogados), `modalidade` (texto
livre, ex: "Processo Seletivo", "Editais Temporários"), e `descricao`
(HTML rico, às vezes com uma `<table>` de cargo/formação/período, às
vezes só texto corrido). Quando não há nenhum registro, `dados` vem como
`[["Nenhum registro encontrado."]]` (lista de string, não de objeto) —
tratar como fonte sem resultado, não como erro.

Municípios confirmados: `dados/municipios_instar.csv` (231 municípios,
76 de MG + 155 de SP — corrigido 2026-09-02, o texto antigo dizia "230
de MG" mas a própria lista sempre teve as duas UFs; triagem em
2026-09-01 contra as ~1240 URLs de `public.municipios` já confirmadas —
ver docs/dados/triagem_instar_wordpress_2026-09-01.csv no repo
principal).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from importlib import resources

SITUACAO_ABERTA = "aberto"


@dataclass
class MunicipioInstar:
    codigo_ibge: int
    nome: str
    uf: str
    url_prefeitura: str


def listar_municipios_instar() -> list[MunicipioInstar]:
    """Municípios com plataforma Instar confirmada (ver módulo docstring)."""
    caminho = resources.files("notifica_vagas_scraper.dados").joinpath("municipios_instar.csv")
    with caminho.open("r", encoding="utf-8", newline="") as f:
        return [
            MunicipioInstar(
                codigo_ibge=int(linha["codigo_ibge"]),
                nome=linha["nome"],
                uf=linha["uf"],
                url_prefeitura=linha["url_prefeitura"],
            )
            for linha in csv.DictReader(f)
        ]


def url_dados_abertos(url_prefeitura: str, ano: int) -> str:
    return url_prefeitura.rstrip("/") + f"/portal/dados-abertos/concursos/{ano}"


def listar_itens_abertos(payload: dict) -> list[dict]:
    """Filtra `payload["dados"]` pelos itens com situacao "Aberto"
    (case-insensitive). Trata o sentinela de "sem registro"
    (`[["Nenhum registro encontrado."]]`, itens que são lista em vez de
    dict) devolvendo lista vazia em vez de levantar erro."""
    itens = payload.get("dados") or []
    return [
        item
        for item in itens
        if isinstance(item, dict) and (item.get("situacao") or "").strip().lower() == SITUACAO_ABERTA
    ]
