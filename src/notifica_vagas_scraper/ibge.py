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


def listar_municipios(uf: str) -> list[dict]:
    """Lista completa de municípios da UF: [{"codigo_ibge": int, "nome": str}, ...]."""
    resposta = requests.get(BASE_URL.format(uf=uf), timeout=15)
    resposta.raise_for_status()
    return [{"codigo_ibge": int(m["id"]), "nome": m["nome"]} for m in resposta.json()]


def buscar_codigo_ibge(nome_municipio: str, uf: str) -> int | None:
    alvo = _normalizar(nome_municipio)
    for municipio in listar_municipios(uf):
        if _normalizar(municipio["nome"]) == alvo:
            return municipio["codigo_ibge"]
    return None
