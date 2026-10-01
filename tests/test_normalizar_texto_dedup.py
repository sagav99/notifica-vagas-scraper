from notifica_vagas_scraper.db import _normalizar_texto_dedup


def test_orgao_prefeitura_municipal_e_do_municipio_sao_iguais():
    a = _normalizar_texto_dedup("Prefeitura do Município de Corumbataí")
    b = _normalizar_texto_dedup("Prefeitura Municipal de Corumbataí")
    assert a == b == "prefeitura de corumbatai"


def test_orgao_distinto_continua_distinto():
    assert _normalizar_texto_dedup("Câmara Municipal de Corumbataí") != _normalizar_texto_dedup(
        "Prefeitura Municipal de Corumbataí"
    )
