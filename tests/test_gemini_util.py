from datetime import date

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


# --- campos estruturados novos (migration 018, 2026-09-10) ---


def test_parsear_data_iso_valida():
    assert gemini_util.parsear_data_iso("2026-09-10") == date(2026, 9, 10)


def test_parsear_data_iso_none_ou_invalida():
    assert gemini_util.parsear_data_iso(None) is None
    assert gemini_util.parsear_data_iso("data a definir") is None


def test_calcular_valor_hora_carga_semanal_simples():
    # 40h semanais ~= 173.33h/mês -> R$ 5.000 / 173.33 ~= R$ 28.85/h
    assert gemini_util.calcular_valor_hora(5000, "mensal", "40h semanais") == pytest.approx(28.85, abs=0.01)
    assert gemini_util.calcular_valor_hora(3000, "mensal", "20 horas") == pytest.approx(34.62, abs=0.01)


def test_calcular_valor_hora_none_quando_ambiguo_ou_ausente():
    assert gemini_util.calcular_valor_hora(None, "mensal", "40h") is None
    assert gemini_util.calcular_valor_hora(5000, None, "40h") is None
    assert gemini_util.calcular_valor_hora(5000, "mensal", None) is None
    # plantão não é jornada semanal fixa — nunca calcula
    assert gemini_util.calcular_valor_hora(1200, "plantao", "12h") is None
    # escala tipo "12x36" não é carga horária semanal simples — não inventa
    assert gemini_util.calcular_valor_hora(5000, "mensal", "12x36") is None
    assert gemini_util.calcular_valor_hora(5000, "mensal", "20h, com plantões aos sábados") is None


def test_campos_estruturados_extras_mapeia_e_calcula():
    extraido = {"taxa_inscricao": 80.0, "data_prova": "2026-11-15"}
    vaga = {
        "vagas_qtd": 3,
        "salario": 5000,
        "salario_tipo": "mensal",
        "carga_horaria": "40h semanais",
        "requisitos": "Ensino superior completo",
    }
    resultado = gemini_util.campos_estruturados_extras(extraido, vaga)
    assert resultado == {
        "numero_vagas": 3,
        "taxa_inscricao": 80.0,
        "carga_horaria": "40h semanais",
        "valor_hora": pytest.approx(28.85, abs=0.01),
        "data_prova": date(2026, 11, 15),
        "requisitos": "Ensino superior completo",
    }


def test_campos_estruturados_extras_tudo_ausente_vira_none():
    resultado = gemini_util.campos_estruturados_extras({}, {})
    assert resultado == {
        "numero_vagas": None,
        "taxa_inscricao": None,
        "carga_horaria": None,
        "valor_hora": None,
        "data_prova": None,
        "requisitos": None,
    }
