import pytest

from notifica_vagas_scraper import ibge


@pytest.fixture(autouse=True)
def _limpar_cache_e_sleep(monkeypatch):
    ibge._cache_por_uf.clear()
    monkeypatch.setattr(ibge.time, "sleep", lambda *_: None)
    yield
    ibge._cache_por_uf.clear()


class _RespostaFalsa:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


_PAYLOAD = [{"id": 3106200, "nome": "Belo Horizonte"}, {"id": 3170206, "nome": "Uberlândia"}]


def test_lista_municipios_e_cacheia(monkeypatch):
    chamadas = {"n": 0}

    def _get(*a, **k):
        chamadas["n"] += 1
        return _RespostaFalsa(_PAYLOAD)

    monkeypatch.setattr(ibge.requests, "get", _get)

    primeira = ibge.listar_municipios("MG")
    segunda = ibge.listar_municipios("MG")

    assert primeira == [
        {"codigo_ibge": 3106200, "nome": "Belo Horizonte"},
        {"codigo_ibge": 3170206, "nome": "Uberlândia"},
    ]
    assert segunda == primeira
    assert chamadas["n"] == 1  # 2ª chamada veio do cache, não bateu na rede de novo


def test_timeout_transitorio_tenta_de_novo_e_funciona(monkeypatch):
    chamadas = {"n": 0}

    def _get(*a, **k):
        chamadas["n"] += 1
        if chamadas["n"] < 2:
            raise ibge.requests.exceptions.Timeout("timeout")
        return _RespostaFalsa(_PAYLOAD)

    monkeypatch.setattr(ibge.requests, "get", _get)

    resultado = ibge.listar_municipios("MG")
    assert len(resultado) == 2
    assert chamadas["n"] == 2


def test_timeout_persistente_levanta_apos_tentativas_max(monkeypatch):
    chamadas = {"n": 0}

    def _get(*a, **k):
        chamadas["n"] += 1
        raise ibge.requests.exceptions.ConnectionError("falha de rede")

    monkeypatch.setattr(ibge.requests, "get", _get)

    with pytest.raises(ibge.requests.exceptions.ConnectionError):
        ibge.listar_municipios("MG")
    assert chamadas["n"] == ibge.TENTATIVAS_MAX


def test_buscar_codigo_ibge_encontra_por_nome_normalizado(monkeypatch):
    monkeypatch.setattr(ibge.requests, "get", lambda *a, **k: _RespostaFalsa(_PAYLOAD))
    assert ibge.buscar_codigo_ibge("belo horizonte", "MG") == 3106200


def test_buscar_codigo_ibge_nao_encontrado_devolve_none(monkeypatch):
    monkeypatch.setattr(ibge.requests, "get", lambda *a, **k: _RespostaFalsa(_PAYLOAD))
    assert ibge.buscar_codigo_ibge("Cidade Inexistente", "MG") is None
