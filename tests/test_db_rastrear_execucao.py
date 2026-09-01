import pytest

from notifica_vagas_scraper import db


def test_sucesso_grava_status_sucesso(monkeypatch):
    gravados = []
    monkeypatch.setattr(
        db, "_gravar_execucao", lambda script, **kw: gravados.append({"script": script, **kw})
    )

    with db.rastrear_execucao("rodar_instar.py"):
        pass

    assert len(gravados) == 1
    assert gravados[0]["script"] == "rodar_instar.py"
    assert gravados[0]["status"] == "sucesso"
    assert gravados[0]["detalhe"] is None


def test_falha_grava_status_falha_com_detalhe_e_relanca(monkeypatch):
    gravados = []
    monkeypatch.setattr(
        db, "_gravar_execucao", lambda script, **kw: gravados.append({"script": script, **kw})
    )

    with pytest.raises(ValueError, match="algo quebrou"):
        with db.rastrear_execucao("rodar_imeso.py"):
            raise ValueError("algo quebrou")

    assert len(gravados) == 1
    assert gravados[0]["status"] == "falha"
    assert "algo quebrou" in gravados[0]["detalhe"]


def test_falha_nao_engole_a_excecao_original(monkeypatch):
    monkeypatch.setattr(db, "_gravar_execucao", lambda script, **kw: None)

    with pytest.raises(RuntimeError):
        with db.rastrear_execucao("rodar_fgv.py"):
            raise RuntimeError("erro real")
