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
