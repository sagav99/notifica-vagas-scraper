from datetime import date

from notifica_vagas_scraper import completude_gemini, db


def test_campos_datas_extras_fora_da_completude():
    # Edital sem essas datas é normal: não pode contar como vaga incompleta.
    assert set(db.CAMPOS_DATAS_EXTRAS) == {"data_pagamento_taxa", "data_resultado"}
    assert not set(db.CAMPOS_DATAS_EXTRAS) & set(db.CAMPOS_COMPLETUDE)


def test_converter_tipos_converte_datas_extras_e_descarta_invalida():
    convertido = completude_gemini._converter_tipos(
        {"data_pagamento_taxa": "2026-11-20", "data_resultado": "a definir"}
    )
    assert convertido == {"data_pagamento_taxa": date(2026, 11, 20)}


def test_prompt_pede_as_datas_extras_com_descricao():
    vaga = {"cargo": "Médico", "orgao": "Prefeitura", "nome": "Betim", "uf": "MG", "numero_edital": "001/2026"}
    prompt = completude_gemini.montar_prompt(vaga, list(db.CAMPOS_DATAS_EXTRAS), link="https://x/edital.pdf", pedir_link_pdf=False)
    assert "data_pagamento_taxa" in prompt and "vencimento do boleto" in prompt
    assert "data_resultado" in prompt


def test_atualizar_datas_extras_rejeita_campo_fora_da_lista():
    import pytest

    with pytest.raises(ValueError):
        db.atualizar_datas_extras(None, vaga_id="x", campos={"salario": 1})


def _carregar_script():
    import importlib.util
    from pathlib import Path

    caminho = Path(__file__).parent.parent / "scripts" / "completude_datas_extras.py"
    spec = importlib.util.spec_from_file_location("completude_datas_extras", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_registrar_aplica_as_datas_do_edital_em_cada_vaga(monkeypatch):
    modulo = _carregar_script()
    gravados, conferencias = [], []
    monkeypatch.setattr(modulo.db, "atualizar_datas_extras", lambda conn, vaga_id, campos: gravados.append((vaga_id, campos)))
    monkeypatch.setattr(
        modulo.db, "registrar_conferencia",
        lambda conn, vaga_id, conferido_por, resultado, detalhe=None: conferencias.append((vaga_id, conferido_por, resultado)),
    )
    aceitos = {"data_pagamento_taxa": date(2026, 11, 20)}
    for vaga_id in ("a", "b"):
        assert modulo.registrar(None, {"id": vaga_id}, "campo_preenchido|Fonte: x", aceitos) == "campo_preenchido"
    assert [g[0] for g in gravados] == ["a", "b"]
    assert all(c[1] == "datas_extras_gemini" for c in conferencias)


def test_registrar_sem_datas_so_registra_conferencia(monkeypatch):
    modulo = _carregar_script()
    gravados, conferencias = [], []
    monkeypatch.setattr(modulo.db, "atualizar_datas_extras", lambda *a, **k: gravados.append(1))
    monkeypatch.setattr(modulo.db, "registrar_conferencia", lambda conn, **k: conferencias.append(k["resultado"]))
    assert modulo.registrar(None, {"id": "a"}, "sem_alteracao|Fonte: x", {}) == "sem_alteracao"
    assert modulo.registrar(None, {"id": "b"}, "erro_gemini: boom", {}) == "erro_gemini"
    assert gravados == [] and conferencias == ["sem_alteracao", "erro_gemini"]
