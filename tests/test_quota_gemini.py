import json

from notifica_vagas_scraper import quota_gemini


def test_sem_arquivo_comeca_no_modelo_padrao(monkeypatch, tmp_path):
    monkeypatch.setattr(quota_gemini, "CAMINHO_CONTADOR", tmp_path / "contagem.json")
    assert quota_gemini.proximo_modelo() == quota_gemini.MODELO_PADRAO


def test_registrar_chamada_incrementa(monkeypatch, tmp_path):
    caminho = tmp_path / "contagem.json"
    monkeypatch.setattr(quota_gemini, "CAMINHO_CONTADOR", caminho)

    quota_gemini.registrar_chamada()
    quota_gemini.registrar_chamada()

    dados = json.loads(caminho.read_text())
    assert dados["contagem"] == 2


def test_troca_para_fallback_apos_limite(monkeypatch, tmp_path):
    monkeypatch.setattr(quota_gemini, "CAMINHO_CONTADOR", tmp_path / "contagem.json")
    for _ in range(quota_gemini.LIMITE_ANTES_DE_TROCAR):
        quota_gemini.registrar_chamada()

    assert quota_gemini.proximo_modelo() == quota_gemini.MODELO_FALLBACK


def test_dia_diferente_reseta_contagem(monkeypatch, tmp_path):
    caminho = tmp_path / "contagem.json"
    caminho.write_text(json.dumps({"data": "2000-01-01", "contagem": 999}))
    monkeypatch.setattr(quota_gemini, "CAMINHO_CONTADOR", caminho)

    assert quota_gemini.proximo_modelo() == quota_gemini.MODELO_PADRAO


def test_modelo_forcado_por_env_sobrepoe_contagem(monkeypatch, tmp_path):
    monkeypatch.setattr(quota_gemini, "CAMINHO_CONTADOR", tmp_path / "contagem.json")
    monkeypatch.setenv("GEMINI_MODELO_FORCADO", "gemini-3.1-flash-lite")

    assert quota_gemini.proximo_modelo() == "gemini-3.1-flash-lite"
