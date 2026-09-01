from datetime import date

import pytest

from notifica_vagas_scraper import quota_gemini, revisao_ia


@pytest.fixture(autouse=True)
def _sem_espera_real(monkeypatch):
    monkeypatch.setattr(revisao_ia, "_esperar_rate_limit", lambda: None)
    monkeypatch.setattr(revisao_ia.time, "sleep", lambda *_: None)
    # proximo_modelo() agora consulta o Postgres (quota_gemini.py,
    # migration 010) -- esses testes não têm DATABASE_URL nem se importam
    # com qual modelo é escolhido, só com o parsing/decisão da resposta.
    monkeypatch.setattr(quota_gemini, "proximo_modelo", lambda: quota_gemini.MODELO_PADRAO)
    monkeypatch.setattr(quota_gemini, "registrar_chamada", lambda: None)


class _RespostaFalsa:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            erro = revisao_ia.requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            erro.response = self
            raise erro

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


def test_503_transitorio_tenta_de_novo_e_aprova(monkeypatch):
    """Achado real rodando o backfill em produção (2026-09-01): 11 de 40
    vagas foram rejeitadas só por 503 momentâneo do Gemini, não por
    problema no dado — o retry existe pra não perder vaga boa por
    instabilidade de infra."""
    texto = '{"decisao": "aprovada", "motivo": "ok"}'
    chamadas = {"n": 0}

    def _post(*a, **k):
        chamadas["n"] += 1
        if chamadas["n"] < 3:
            return _RespostaFalsa({}, status_code=503)
        return _RespostaFalsa(_payload_com_texto(texto))

    monkeypatch.setattr(revisao_ia.requests, "post", _post)
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "aprovada"
    assert chamadas["n"] == 3


def test_503_persistente_esgota_tentativas_e_rejeita(monkeypatch):
    chamadas = {"n": 0}

    def _post(*a, **k):
        chamadas["n"] += 1
        return _RespostaFalsa({}, status_code=503)

    monkeypatch.setattr(revisao_ia.requests, "post", _post)
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "rejeitada"
    assert chamadas["n"] == revisao_ia.TENTATIVAS_MAX


def test_prompt_inclui_data_de_hoje_como_referencia(monkeypatch):
    # Achado real rodando o backfill de 209 vagas Instar em produção
    # (2026-09-01): o Gemini rejeitava vaga genuína julgando o ano do
    # edital "futuro implausível" por causa do próprio corte de
    # treinamento — o prompt agora ancora a data real de hoje.
    texto = '{"decisao": "aprovada", "motivo": "ok"}'
    capturado = {}

    def _post(url, params, json, timeout):
        capturado["prompt"] = json["contents"][0]["parts"][0]["text"]
        return _RespostaFalsa(_payload_com_texto(texto))

    monkeypatch.setattr(revisao_ia.requests, "post", _post)
    revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")

    hoje = date.today()
    assert hoje.isoformat() in capturado["prompt"]
    assert str(hoje.year) in capturado["prompt"]


def test_erro_4xx_nao_tenta_de_novo(monkeypatch):
    chamadas = {"n": 0}

    def _post(*a, **k):
        chamadas["n"] += 1
        return _RespostaFalsa({}, status_code=400)

    monkeypatch.setattr(revisao_ia.requests, "post", _post)
    resultado = revisao_ia.decidir_revisao({"cargo": "Enfermeiro"}, api_key="chave-teste")
    assert resultado["decisao"] == "rejeitada"
    assert chamadas["n"] == 1
