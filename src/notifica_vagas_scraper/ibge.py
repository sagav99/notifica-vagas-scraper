"""Lookup de código IBGE de município via API pública do IBGE.

Não é scraping de fonte adversarial — é referência geográfica oficial,
usada só pra popular `municipios.codigo_ibge` sem hardcoded/hallucinado.
"""

from __future__ import annotations

import time
import unicodedata

import requests

BASE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
TENTATIVAS_MAX = 3
BACKOFF_INICIAL_S = 3.0

_cache_por_uf: dict[str, list[dict]] = {}


def _normalizar(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return sem_acento.strip().lower()


def listar_municipios(uf: str) -> list[dict]:
    """Lista completa de municípios da UF: [{"codigo_ibge": int, "nome": str}, ...].

    Cacheada em memória por UF (achado real: `buscar_codigo_ibge` é chamado
    1x por edital/matéria processado, cada chamada sem cache refazia o
    mesmo GET de +800 municípios repetidas vezes no mesmo processo —
    desperdício e mais chance de bater timeout transitório à toa) e com
    retry em timeout/erro de conexão (achado real, 2026-09-01: 3ª vez que
    o cron falha com timeout pra servicodados.ibge.gov.br — API pública
    do IBGE, instabilidade esporádica normal de terceiro, não bloqueio)."""
    if uf in _cache_por_uf:
        return _cache_por_uf[uf]

    ultimo_erro: Exception | None = None
    for tentativa in range(TENTATIVAS_MAX):
        if tentativa > 0:
            time.sleep(BACKOFF_INICIAL_S * tentativa)
        try:
            resposta = requests.get(BASE_URL.format(uf=uf), timeout=15)
            resposta.raise_for_status()
            municipios = [
                {"codigo_ibge": int(m["id"]), "nome": m["nome"]} for m in resposta.json()
            ]
            _cache_por_uf[uf] = municipios
            return municipios
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            ultimo_erro = exc
    assert ultimo_erro is not None
    raise ultimo_erro


def buscar_codigo_ibge(nome_municipio: str, uf: str) -> int | None:
    alvo = _normalizar(nome_municipio)
    for municipio in listar_municipios(uf):
        if _normalizar(municipio["nome"]) == alvo:
            return municipio["codigo_ibge"]
    return None
