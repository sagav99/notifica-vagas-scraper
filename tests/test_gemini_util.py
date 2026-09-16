import time
from datetime import date

import pytest

from notifica_vagas_scraper import gemini_util, quota_gemini


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
    extraido = {
        "taxa_inscricao": 80.0,
        "data_prova": "2026-11-15",
        "banca_organizadora": "IBFC",
        "tem_prova": True,
        "exige_curriculo": False,
    }
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
        "banca_organizadora": "IBFC",
        "tem_prova": True,
        "exige_curriculo": False,
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
        "banca_organizadora": None,
        "tem_prova": None,
        "exige_curriculo": None,
    }


# --- rate limit por modelo: RPM + TPM (achado 2026-09-16) ---


@pytest.fixture(autouse=True)
def _limpa_estado_global_do_limiter():
    # os dicts de estado são globais no módulo (de propósito, ver
    # docstring) -- sem isolar entre testes, um teste vaza estado pro
    # próximo e os tempos de espera calculados ficam imprevisíveis.
    gemini_util._ultima_chamada_por_modelo.clear()
    gemini_util._janela_tokens_por_modelo.clear()
    yield
    gemini_util._ultima_chamada_por_modelo.clear()
    gemini_util._janela_tokens_por_modelo.clear()


def test_esperar_rate_limit_e_por_modelo_independente(monkeypatch):
    # 2 modelos diferentes não esperam um pelo relógio RPM do outro --
    # só entram no mesmo "balde" se forem o mesmo modelo.
    chamadas_sleep = []
    monkeypatch.setattr(time, "sleep", lambda s: chamadas_sleep.append(s))

    gemini_util.esperar_rate_limit(quota_gemini.MODELO_PADRAO)
    gemini_util.esperar_rate_limit(quota_gemini.MODELO_FALLBACK)

    assert chamadas_sleep == []  # 1ª chamada de cada modelo, nunca espera


def test_esperar_rate_limit_espera_no_mesmo_modelo(monkeypatch):
    chamadas_sleep = []
    monkeypatch.setattr(time, "sleep", lambda s: chamadas_sleep.append(s))

    gemini_util.esperar_rate_limit(quota_gemini.MODELO_PADRAO)
    gemini_util.esperar_rate_limit(quota_gemini.MODELO_PADRAO)

    assert len(chamadas_sleep) == 1
    assert chamadas_sleep[0] == pytest.approx(gemini_util.INTERVALO_MINIMO_ENTRE_CHAMADAS_S, abs=0.1)


def test_aguardar_orcamento_tpm_nao_espera_dentro_do_teto(monkeypatch):
    chamadas_sleep = []
    monkeypatch.setattr(time, "sleep", lambda s: chamadas_sleep.append(s))
    gemini_util.registrar_tokens_usados(quota_gemini.MODELO_PADRAO, 100_000)

    gemini_util.aguardar_orcamento_tpm(quota_gemini.MODELO_PADRAO, 50_000)

    assert chamadas_sleep == []


def test_aguardar_orcamento_tpm_espera_quando_estouraria(monkeypatch):
    # Achado real 2026-09-16: várias chamadas de PDF grande (gemini_pdf.py)
    # em sequência podiam estourar as 250k TPM mesmo respeitando o
    # intervalo de RPM entre chamadas -- este teste é a regressão disso.
    # Relógio simulado: `sleep` avança o mesmo relógio que `monotonic`
    # lê, senão o loop de espera giraria pra sempre num teste (tempo real
    # não passa só porque o mock de sleep "aceitou" o argumento).
    agora = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: agora[0])
    chamadas_sleep = []

    def _sleep(segundos):
        chamadas_sleep.append(segundos)
        agora[0] += segundos

    monkeypatch.setattr(time, "sleep", _sleep)
    gemini_util.registrar_tokens_usados(quota_gemini.MODELO_PADRAO, 230_000)

    gemini_util.aguardar_orcamento_tpm(quota_gemini.MODELO_PADRAO, 30_000)

    assert len(chamadas_sleep) >= 1


def test_aguardar_orcamento_tpm_e_por_modelo_independente(monkeypatch):
    chamadas_sleep = []
    monkeypatch.setattr(time, "sleep", lambda s: chamadas_sleep.append(s))
    gemini_util.registrar_tokens_usados(quota_gemini.MODELO_PADRAO, 240_000)

    # o fallback tem orçamento de TPM próprio -- não deveria esperar por
    # causa do uso do padrão.
    gemini_util.aguardar_orcamento_tpm(quota_gemini.MODELO_FALLBACK, 30_000)

    assert chamadas_sleep == []


def test_registrar_tokens_usados_expira_da_janela_apos_60s(monkeypatch):
    agora = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: agora[0])

    gemini_util.registrar_tokens_usados(quota_gemini.MODELO_PADRAO, 240_000)
    agora[0] += 61  # janela de 60s expirou

    janela = gemini_util._purgar_janela_tpm(quota_gemini.MODELO_PADRAO)
    assert sum(tokens for _, tokens in janela) == 0


class _RespostaFalsaComUso:
    def __init__(self, tokens_reais: int):
        self._tokens_reais = tokens_reais

    def json(self):
        return {"usageMetadata": {"totalTokenCount": self._tokens_reais}}


def test_chamar_api_registra_uso_real_da_resposta(monkeypatch):
    monkeypatch.setattr(gemini_util, "esperar_rate_limit", lambda modelo: None)
    monkeypatch.setattr(gemini_util, "aguardar_orcamento_tpm", lambda modelo, tokens_estimados: None)
    monkeypatch.setattr(quota_gemini, "registrar_chamada", lambda modelo: None)
    monkeypatch.setattr(
        gemini_util.requests, "post", lambda *a, **k: _RespostaFalsaComUso(tokens_reais=12345)
    )

    gemini_util.chamar_api(
        "http://fake", {}, chave="x", modelo=quota_gemini.MODELO_PADRAO, timeout=10, tokens_estimados=999
    )

    janela = gemini_util._purgar_janela_tpm(quota_gemini.MODELO_PADRAO)
    assert sum(tokens for _, tokens in janela) == 12345  # uso real, não a estimativa
