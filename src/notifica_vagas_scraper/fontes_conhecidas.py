"""Lista curada de matérias já mapeadas manualmente (via subagente
pesquisador-fonte) pra rodar o pipeline enquanto a busca automatizada no
DOM/AMM-MG (formulário com token CSRF, ver docs/investigacao_fontes) não
está implementada. Cada item vira uma checagem no cron.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MateriaConhecida:
    municipio: str
    uf: str
    url: str
    fonte_nome: str = "Diário Oficial dos Municípios Mineiros (AMM-MG)"
    fonte_url: str = "https://www.diariomunicipal.com.br/amm-mg/"


MATERIAS_DOM_AMM_MG: list[MateriaConhecida] = [
    MateriaConhecida(
        municipio="Pedra Dourada",
        uf="MG",
        url="https://www.diariomunicipal.com.br/amm-mg/materia/FFF2867C/e60dcd27c9fbf985355894afa4e22ee8e60dcd27c9fbf985355894afa4e22ee8",
    ),
]
