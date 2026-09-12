import pytest

from notifica_vagas_scraper import auditoria_completude as ac


class _RespostaFalsa:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise __import__("requests").exceptions.HTTPError(response=self)


def test_checar_link_ok(monkeypatch):
    monkeypatch.setattr(ac.requests, "head", lambda *a, **k: _RespostaFalsa(200))
    resultado = ac.checar_link("https://exemplo.org/edital.pdf")
    assert resultado.acessivel


def test_checar_link_quebrado_cai_pra_get(monkeypatch):
    monkeypatch.setattr(ac.requests, "head", lambda *a, **k: _RespostaFalsa(405))
    monkeypatch.setattr(ac.requests, "get", lambda *a, **k: _RespostaFalsa(404))
    resultado = ac.checar_link("https://exemplo.org/edital.pdf")
    assert not resultado.acessivel
    assert "404" in resultado.motivo


def test_checar_link_erro_de_conexao(monkeypatch):
    import requests

    def _levanta(*a, **k):
        raise requests.ConnectionError("timeout")

    monkeypatch.setattr(ac.requests, "head", _levanta)
    resultado = ac.checar_link("https://exemplo.org/edital.pdf")
    assert not resultado.acessivel
    assert "erro de conexão" in resultado.motivo


def test_encontrar_dados_cargo_bate_normalizado():
    extraido = {"vagas": [{"cargo": "Médico Cardiologista", "salario": 8000}, {"cargo": "Enfermeiro", "salario": 3000}]}
    resultado = ac.encontrar_dados_cargo(extraido, "médico cardiologista")
    assert resultado == {"cargo": "Médico Cardiologista", "salario": 8000}


def test_encontrar_dados_cargo_nao_acha():
    extraido = {"vagas": [{"cargo": "Enfermeiro"}]}
    assert ac.encontrar_dados_cargo(extraido, "Médico") is None


def test_montar_campos_a_atualizar_so_preenche_o_que_esta_null():
    vaga_atual = {
        "taxa_inscricao": 50.0,  # já preenchida — não deve ser sobrescrita
        "data_prova": None,
        "banca_organizadora": None,
        "numero_vagas": None,
        "salario": 9000,  # já preenchida
    }
    extraido = {
        "taxa_inscricao": 999.0,  # diferente do já salvo — deve ser ignorado
        "data_prova": "2026-12-01",
        "banca_organizadora": "IBFC",
        "vagas": [],
    }
    dados_cargo = {"vagas_qtd": 2, "salario": 12000}

    campos = ac.montar_campos_a_atualizar(vaga_atual, extraido, dados_cargo)

    assert campos == {
        "data_prova": "2026-12-01",
        "banca_organizadora": "IBFC",
        "numero_vagas": 2,
    }
    assert "taxa_inscricao" not in campos
    assert "salario" not in campos


def test_montar_campos_a_atualizar_sem_dados_novos():
    vaga_atual = {"taxa_inscricao": None}
    assert ac.montar_campos_a_atualizar(vaga_atual, None, None) == {}
    assert ac.montar_campos_a_atualizar(vaga_atual, {}, None) == {}


def test_buscar_link_alternativo_sem_chave_devolve_none(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    resultado = ac.buscar_link_alternativo(cargo="Médico", orgao="Prefeitura", municipio="Cidade", uf="MG")
    assert resultado is None


def test_buscar_link_alternativo_acha_item(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"organic": [{"title": "Edital novo", "link": "https://exemplo.org/novo", "snippet": "..."}]}

    monkeypatch.setattr(ac.requests, "post", lambda *a, **k: _Resp())
    resultado = ac.buscar_link_alternativo(
        cargo="Médico", orgao="Prefeitura", municipio="Cidade", uf="MG", api_key="chave-fake"
    )
    assert resultado is not None
    assert resultado.link == "https://exemplo.org/novo"
