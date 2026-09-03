from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.fontes import apm_sp, dom_amm_mg

FIXTURES = Path(__file__).parent / "fixtures" / "apm_sp"


def test_lista_7_entidades_reais_confirmadas():
    entidades = apm_sp.listar_entidades_apm_sp()
    assert len(entidades) == 7
    assert all(e.uf == "SP" for e in entidades)
    nomes = {e.nome for e in entidades}
    assert nomes == {
        "Barra do Turvo",
        "Bocaina",
        "Irapuã",
        "Itu",
        "Itupeva",
        "Mococa",
        "Taquarituba",
    }


def test_devolve_copia_nao_a_lista_original():
    entidades = apm_sp.listar_entidades_apm_sp()
    entidades.pop()
    assert len(apm_sp.listar_entidades_apm_sp()) == 7


def test_barra_do_turvo_tabela_com_cabecalho_cargo_nao_e_descartada():
    """Achado real (investigação apm_sp, 2026-09-03): esta matéria usa
    cabeçalho de tabela "Cargo" em vez de "Funções"/"Função" (padrão do
    AMM-MG). O parser compartilhado (`dom_amm_mg.parsear_materia`) tinha um
    bug que descartava a tabela inteira nesse caso, perdendo a vaga real
    de Auxiliar de Farmácia — regressão coberta aqui."""
    html = (FIXTURES / "barra_do_turvo_edital_processo_seletivo_04_2026_header_cargo.html").read_text(
        encoding="utf-8"
    )
    vagas = dom_amm_mg.parsear_materia(
        html, url="https://www.diariomunicipal.com.br/apm/materia/FA6A8276/exemplo"
    )

    assert len(vagas) == 1
    vaga = vagas[0]
    assert vaga.cargo == "AUXILIAR DE FARMÁCIA"
    assert vaga.vagas_qtd == 1
    assert vaga.salario == Decimal("2231.65")
    assert vaga.numero_edital == "04/2026"
    assert vaga.tipo_processo == "Processo Seletivo Simplificado"
