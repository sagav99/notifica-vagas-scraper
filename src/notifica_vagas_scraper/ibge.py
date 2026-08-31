"""Lookup de código IBGE de município via API pública do IBGE.

Não é scraping de fonte adversarial — é referência geográfica oficial,
usada só pra popular `municipios.codigo_ibge` sem hardcoded/hallucinado.
"""

from __future__ import annotations

import unicodedata

import requests

BASE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"


def _normalizar(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return sem_acento.strip().lower()


def buscar_codigo_ibge(nome_municipio: str, uf: str) -> int | None:
    resposta = requests.get(BASE_URL.format(uf=uf), timeout=15)
    resposta.raise_for_status()
    alvo = _normalizar(nome_municipio)
    for municipio in resposta.json():
        if _normalizar(municipio["nome"]) == alvo:
            return int(municipio["id"])
    return None
