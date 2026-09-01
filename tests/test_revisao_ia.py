import pytest

from notifica_vagas_scraper import revisao_ia


@pytest.fixture(autouse=True)
def _sem_rate_limit_real(monkeypatch):
    monkeypatch.setattr(revisao_ia, "_esperar_rate_limit", lambda: None)


class _RespostaFalsa:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _payload_com_texto(texto: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": texto}]}}]}


def test_aprova_quando_gemini_decide_aprovar(monkeypatch):
    texto = '{"decisao": "aprovada", "motivo": "Dados completos e consistentes."}'
    monkeypatch.setattr(
        revisao_ia.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado == {"decisao": "aprovada", "motivo": "Dados completos e consistentes."}


def test_rejeita_quando_gemini_decide_rejeitar(monkeypatch):
    texto = '{"decisao": "rejeitada", "motivo": "Cargo genérico demais."}'
    monkeypatch.setattr(
        revisao_ia.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = revisao_ia.decidir_revisao({"cargo": "vaga"}, api_key="chave-teste")
    assert resultado["decisao"] == "rejeitada"


def test_remove_cerca_de_markdown(monkeypatch):
    texto = '```json\n{"decisao": "aprovada", "motivo": "ok"}\n```'
    monkeypatch.setattr(
        revisao_ia.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "aprovada"


def test_sem_api_key_rejeita_por_padrao(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key=None)
    assert resultado["decisao"] == "rejeitada"


def test_erro_de_rede_rejeita_por_padrao(monkeypatch):
    def _levanta(*a, **k):
        raise revisao_ia.requests.exceptions.ConnectionError("falha de rede")

    monkeypatch.setattr(revisao_ia.requests, "post", _levanta)
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "rejeitada"
    assert "Erro ao consultar o Gemini" in resultado["motivo"]


def test_json_invalido_rejeita_por_padrao(monkeypatch):
    monkeypatch.setattr(
        revisao_ia.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto("não é json"))
    )
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "rejeitada"


def test_decisao_fora_do_formato_rejeita_por_padrao(monkeypatch):
    texto = '{"decisao": "talvez", "motivo": "não tenho certeza"}'
    monkeypatch.setattr(
        revisao_ia.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "rejeitada"
    assert "fora do formato esperado" in resultado["motivo"]


def test_resposta_sem_candidates_rejeita_por_padrao(monkeypatch):
    monkeypatch.setattr(
        revisao_ia.requests, "post", lambda *a, **k: _RespostaFalsa({"error": "algo"})
    )
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "rejeitada"
