import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _sem_banco_na_trava_de_cota(request, monkeypatch):
    if request.module.__name__.endswith("test_quota_gemini"):
        return
    from notifica_vagas_scraper import quota_gemini

    monkeypatch.setattr(quota_gemini, "garantir_folga_diaria", lambda modelo: None)


@pytest.fixture(autouse=True)
def _zera_estado_de_instabilidade_do_gemini(monkeypatch):
    """Disjuntor e lista de modelos instáveis são estado de módulo; sem isso um
    teste com 503 deixa o seguinte sem conseguir chamar a API."""
    from notifica_vagas_scraper import gemini_util, quota_gemini

    monkeypatch.setattr(gemini_util, "_falhas_seguidas", 0)
    monkeypatch.setattr(gemini_util, "_503_seguidos", {})
    monkeypatch.setattr(quota_gemini, "_instaveis_ate", {})
