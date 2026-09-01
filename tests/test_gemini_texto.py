import pytest

from notifica_vagas_scraper import gemini_texto


@pytest.fixture(autouse=True)
def _sem_rate_limit_real(monkeypatch):
    monkeypatch.setattr(gemini_texto, "_esperar_rate_limit", lambda: None)


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


def test_extrai_json_puro(monkeypatch):
    texto = '{"numero_edital": "17/26", "orgao": "X", "data_publicacao": null, "inscricoes_inicio": null, "inscricoes_fim": null, "vagas": [{"cargo": "Agente de Comunicação", "vagas_qtd": null, "salario": null, "requisitos": null, "carga_horaria": null}]}'
    monkeypatch.setattr(
        gemini_texto.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = gemini_texto.extrair_vagas_de_texto("titulo", "texto", api_key="chave-teste")
    assert resultado["numero_edital"] == "17/26"
    assert resultado["vagas"][0]["cargo"] == "Agente de Comunicação"


def test_texto_sem_vaga_real_devolve_lista_vazia(monkeypatch):
    texto = '{"numero_edital": null, "orgao": null, "data_publicacao": null, "inscricoes_inicio": null, "inscricoes_fim": null, "vagas": []}'
    monkeypatch.setattr(
        gemini_texto.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = gemini_texto.extrair_vagas_de_texto(
        "Eleição do Conselho Tutelar", "texto sobre eleição", api_key="chave-teste"
    )
    assert resultado["vagas"] == []


def test_remove_cerca_de_markdown(monkeypatch):
    texto = '```json\n{"numero_edital": null, "orgao": null, "data_publicacao": null, "inscricoes_inicio": null, "inscricoes_fim": null, "vagas": []}\n```'
    monkeypatch.setattr(
        gemini_texto.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = gemini_texto.extrair_vagas_de_texto("titulo", "texto", api_key="chave-teste")
    assert resultado["vagas"] == []


def test_sem_api_key_levanta_erro(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gemini_texto.ErroExtracaoGemini):
        gemini_texto.extrair_vagas_de_texto("titulo", "texto", api_key=None)


def test_resposta_sem_candidates_levanta_erro(monkeypatch):
    monkeypatch.setattr(gemini_texto.requests, "post", lambda *a, **k: _RespostaFalsa({"error": "algo"}))
    with pytest.raises(gemini_texto.ErroExtracaoGemini):
        gemini_texto.extrair_vagas_de_texto("titulo", "texto", api_key="chave-teste")
