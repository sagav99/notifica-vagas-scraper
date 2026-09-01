import pytest

from notifica_vagas_scraper import gemini_pdf


@pytest.fixture(autouse=True)
def _sem_rate_limit_real(monkeypatch):
    # sem isso, cada teste esperaria de verdade os 4.5s do rate limit
    monkeypatch.setattr(gemini_pdf, "_esperar_rate_limit", lambda: None)


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
    texto = '{"numero_edital": "01/2026", "orgao": "X", "data_publicacao": "2026-01-28", "inscricoes_inicio": null, "inscricoes_fim": null, "vagas": []}'
    monkeypatch.setattr(
        gemini_pdf.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = gemini_pdf.extrair_vagas_de_pdf(b"pdf falso", api_key="chave-teste")
    assert resultado["numero_edital"] == "01/2026"
    assert resultado["data_publicacao"] == "2026-01-28"
    assert resultado["vagas"] == []


def test_remove_cerca_de_markdown(monkeypatch):
    texto = '```json\n{"numero_edital": null, "orgao": null, "inscricoes_inicio": null, "inscricoes_fim": null, "vagas": [{"cargo": "X", "vagas_qtd": 1, "salario": 1000.0, "requisitos": null, "carga_horaria": null}]}\n```'
    monkeypatch.setattr(
        gemini_pdf.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto(texto))
    )

    resultado = gemini_pdf.extrair_vagas_de_pdf(b"pdf falso", api_key="chave-teste")
    assert resultado["vagas"][0]["cargo"] == "X"


def test_sem_api_key_levanta_erro(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gemini_pdf.ErroExtracaoGemini):
        gemini_pdf.extrair_vagas_de_pdf(b"pdf falso", api_key=None)


def test_resposta_sem_candidates_levanta_erro(monkeypatch):
    monkeypatch.setattr(gemini_pdf.requests, "post", lambda *a, **k: _RespostaFalsa({"error": "algo"}))
    with pytest.raises(gemini_pdf.ErroExtracaoGemini):
        gemini_pdf.extrair_vagas_de_pdf(b"pdf falso", api_key="chave-teste")


def test_json_invalido_levanta_erro(monkeypatch):
    monkeypatch.setattr(
        gemini_pdf.requests, "post", lambda *a, **k: _RespostaFalsa(_payload_com_texto("isso não é json"))
    )
    with pytest.raises(gemini_pdf.ErroExtracaoGemini):
        gemini_pdf.extrair_vagas_de_pdf(b"pdf falso", api_key="chave-teste")
