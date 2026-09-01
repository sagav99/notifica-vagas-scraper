import pytest

from notifica_vagas_scraper import gemini_util


def test_parseia_json_puro():
    resultado = gemini_util.parsear_json_resposta('{"a": 1, "b": null}')
    assert resultado == {"a": 1, "b": None}


def test_remove_cerca_de_markdown():
    resultado = gemini_util.parsear_json_resposta('```json\n{"a": 1}\n```')
    assert resultado == {"a": 1}


def test_escapa_backslash_solto_invalido():
    # Gemini às vezes devolve barra invertida solta dentro de um valor de
    # string (não é um escape JSON válido) — deve virar barra literal, não
    # explodir o parse com "Invalid \\uXXXX escape".
    resultado = gemini_util.parsear_json_resposta(r'{"motivo": "R$\pessoa"}')
    assert resultado == {"motivo": "R$\\pessoa"}


def test_preserva_escapes_validos():
    resultado = gemini_util.parsear_json_resposta(r'{"a": "linha 1\nlinha 2", "b": "é"}')
    assert resultado == {"a": "linha 1\nlinha 2", "b": "é"}


def test_json_genuinamente_invalido_ainda_levanta_erro():
    with pytest.raises(ValueError):
        gemini_util.parsear_json_resposta("isso não é json")
