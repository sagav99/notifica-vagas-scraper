import json
from pathlib import Path

import pytest

from notifica_vagas_scraper.classificar_medico import eh_cargo_medico

# Fixture compartilhável — os MESMOS casos deveriam ser espelhados em
# lib/admin/classificarSaude.ts no repo do site (notifica-vagas), que
# usa a mesma lógica em TypeScript (ver docstring do módulo). O
# espelhamento do lado TS é feito por outro agente, fora deste repo.
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "classificacao_cargo_medico.json"
_CASOS = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_cargos_medicos_com_prefixo():
    for cargo in [
        "MÉDICO - CARDIOLOGIA",
        "Médico Pediatra",
        "MEDICO PLANTONISTA",
        "Médica Clínica Geral",
    ]:
        assert eh_cargo_medico(cargo), cargo


def test_especialidade_sem_prefixo():
    assert eh_cargo_medico("Cardiologista")
    assert eh_cargo_medico("Clínica Médica")


def test_falsos_positivos_conhecidos():
    assert not eh_cargo_medico("Estagiário - Medicina")
    assert not eh_cargo_medico("Biomedicina")


def test_veterinario_nao_e_medico_humano():
    assert not eh_cargo_medico("Médico Veterinário")
    assert not eh_cargo_medico("Medicina Veterinária")


def test_outras_profissoes_de_saude_nao_sao_medico():
    assert not eh_cargo_medico("ENFERMEIRO SUBSTITUTO (SEDE E DISTRITOS)")
    assert not eh_cargo_medico("Cirurgião Dentista")
    assert not eh_cargo_medico("Fisioterapeuta")


def test_cargos_nao_saude():
    assert not eh_cargo_medico("Operador de Máquinas")
    assert not eh_cargo_medico(None)


@pytest.mark.parametrize("caso", _CASOS, ids=[repr(c["cargo"]) for c in _CASOS])
def test_fixture_classificacao_cargo_medico(caso):
    assert eh_cargo_medico(caso["cargo"]) is caso["eh_medico"], caso["cargo"]
