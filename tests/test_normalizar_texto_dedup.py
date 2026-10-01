from notifica_vagas_scraper.db import _normalizar_texto_dedup


def test_orgao_prefeitura_municipal_e_do_municipio_sao_iguais():
    a = _normalizar_texto_dedup("Prefeitura do Município de Corumbataí")
    b = _normalizar_texto_dedup("Prefeitura Municipal de Corumbataí")
    assert a == b == "prefeitura de corumbatai"


def test_orgao_distinto_continua_distinto():
    assert _normalizar_texto_dedup("Câmara Municipal de Corumbataí") != _normalizar_texto_dedup(
        "Prefeitura Municipal de Corumbataí"
    )


def test_orgao_com_sufixo_de_secretaria_e_mesmo_orgao():
    from notifica_vagas_scraper.db import _normalizar_orgao_dedup as n

    base = n("Prefeitura de Belo Horizonte")
    assert n("Prefeitura de Belo Horizonte - SMSA") == base
    assert n("Prefeitura Municipal de Belo Horizonte - Secretaria Municipal de Saúde") == base
    assert n("Secretaria Municipal de Saúde de Belo Horizonte") == base


def test_cargo_com_hifen_nao_e_colapsado():
    assert _normalizar_texto_dedup("Médico - Cardiologia") != _normalizar_texto_dedup("Médico - Cirurgia Geral")
