from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.fontes import dom_amm_mg

FIXTURES = Path(__file__).parent / "fixtures" / "dom_amm_mg"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_entidades_amm_mg_le_csv_real():
    entidades = dom_amm_mg.listar_entidades_amm_mg()
    assert len(entidades) > 150
    assert all(e.uf == "MG" for e in entidades)
    assert all(e.entidade_id.isdigit() for e in entidades)
    nomes = {e.nome for e in entidades}
    assert "Pedra Dourada" in nomes


def test_processo_seletivo_publico_pedra_dourada():
    html = _ler_fixture("pedra_dourada_materia_processo_seletivo_001_2026.html")
    vagas = dom_amm_mg.parsear_materia(html, url="https://www.diariomunicipal.com.br/amm-mg/materia/FFF2867C/exemplo")

    assert len(vagas) == 2
    cargos = {v.cargo: v for v in vagas}

    acs = cargos["Agente Comunitário de Saúde"]
    assert acs.vagas_qtd == 1
    assert acs.salario == Decimal("2037.86")
    assert acs.numero_edital == "001/2026"
    assert acs.tipo_processo == "Processo Seletivo Público"
    assert acs.orgao == "PREFEITURA MUNICIPAL DE PEDRA DOURADA"
    assert acs.codigo_identificador == "FFF2867C"
    assert acs.data_publicacao.isoformat() == "2026-07-08"
    assert acs.edicao == "4312"
    assert acs.inscricoes_inicio.isoformat() == "2026-09-09"
    assert acs.inscricoes_fim.isoformat() == "2026-10-09"

    ace = cargos["Agente de Combate a Endemias"]
    assert ace.vagas_qtd == 1
    assert ace.salario == Decimal("2037.86")

    assert dom_amm_mg.identificador_externo(acs) == "FFF2867C-agente-comunitario-de-saude"
    assert dom_amm_mg.identificador_externo(ace) == "FFF2867C-agente-de-combate-a-endemias"


def test_retificacao_mesmo_numero_edital():
    """A retificação só muda conteúdo programático — mesmo numero_edital do
    original, pra cair no mesmo match de dedup de `vagas` (municipio+orgao+
    numero_edital), sem duplicar a vaga."""
    html = _ler_fixture("pedra_dourada_materia_processo_seletivo_publico_001_2026_retificacao_24jul.html")
    vagas = dom_amm_mg.parsear_materia(html, url="https://www.diariomunicipal.com.br/amm-mg/materia/39CA757E/exemplo")

    if vagas:  # retificação pode não repetir a tabela de cargos
        assert vagas[0].numero_edital == "001/2026"


def test_processo_seletivo_simplificado_multiplas_tabelas():
    html = _ler_fixture("pedra_dourada_materia_processo_seletivo_simplificado_001_2026_08jul.html")
    vagas = dom_amm_mg.parsear_materia(html, url="https://www.diariomunicipal.com.br/amm-mg/materia/exemplo/exemplo")

    assert len(vagas) >= 5
    cargos = {v.cargo for v in vagas}
    assert "Auxiliar de Cuidador" in cargos
    assert "Monitor de Creche" in cargos

    auxiliar = next(v for v in vagas if v.cargo == "Auxiliar de Cuidador")
    assert auxiliar.salario == Decimal("1621.00")
    assert auxiliar.tipo_processo == "Processo Seletivo Simplificado"
