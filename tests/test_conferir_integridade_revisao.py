import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "conferir_integridade_revisao", Path(__file__).parent.parent / "scripts" / "conferir_integridade_revisao.py"
)
conferir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conferir)


def test_municipio_do_orgao_prefeitura():
    assert conferir.municipio_do_orgao("Prefeitura de São Joaquim da Barra") == "sao joaquim da barra"
    assert conferir.municipio_do_orgao("Prefeitura Municipal de Itabira - Secretaria de Saúde") == "itabira"
    assert conferir.municipio_do_orgao("Município de Franca/SP") == "franca"


def test_orgao_que_nao_e_prefeitura_nao_entra_na_checagem():
    assert conferir.municipio_do_orgao("Hospital Metropolitano Odilon Behrens") is None
    assert conferir.municipio_do_orgao("FAMESP") is None
    assert conferir.municipio_do_orgao(None) is None
