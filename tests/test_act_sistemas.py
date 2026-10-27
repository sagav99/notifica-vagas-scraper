from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.classificar_medico import eh_cargo_medico
from notifica_vagas_scraper.fontes import act_sistemas as act

FIX = Path(__file__).parent / "fixtures" / "act_sistemas"


def _ler(nome: str) -> str:
    return (FIX / nome).read_text(encoding="utf-8")


def _medicos(nome: str) -> list[act.Cargo]:
    return [c for c in act.parse_detalhe(_ler(nome)).cargos if eh_cargo_medico(c.nome)]


def test_listagem_legalle_so_abertos():
    itens = act.parse_listagem(_ler("legalle_index.html"), "legalle")
    assert {i.edital_id for i in itens} == {86, 88, 89}
    pg = next(i for i in itens if i.edital_id == 89)
    assert pg.orgao == "Município de Praia Grande"
    assert pg.inscricoes_inicio.isoformat() == "2026-10-27"
    assert pg.inscricoes_fim.isoformat() == "2026-11-22"
    assert pg.url_detalhe == "https://portal.institutolegalle.org.br/edital/ver/89"


def test_listagem_tupy_so_abertos():
    itens = act.parse_listagem(_ler("tupy_index.html"), "tupy")
    assert {i.edital_id for i in itens} == {90, 99, 99 + 1, 101}
    assert all(i.url_detalhe.startswith("https://portal.institutotupy.com.br/edital/ver/") for i in itens)


def test_detalhe_municipio_uf_e_pdf():
    d = act.parse_detalhe(_ler("legalle_ver_89.html"))
    assert (d.municipio, d.uf) == ("Praia Grande", "SC")
    assert d.inscricoes_fim.isoformat() == "2026-11-22"
    ed = act.escolher_edital(d.arquivos)
    assert ed is not None and "arquivo.php?file=edital/1/89/" in ed.url
    assert act.parse_detalhe(_ler("legalle_ver_88.html")).uf == "RS"


def test_legalle_rio_negrinho_5_medicos_com_salario():
    medicos = _medicos("legalle_ver_86.html")
    assert len(medicos) == 5
    nomes = " | ".join(c.nome for c in medicos)
    for esp in ("Clínico Geral", "Ginecologista", "Ortopedista", "Pediatra", "Psiquiatria"):
        assert esp in nomes
    assert all(c.salario == Decimal("18538.78") for c in medicos)
    assert all(c.requisitos for c in medicos)


def test_legalle_praia_grande_sc_medicos_que_faltavam():
    nomes = {c.nome for c in _medicos("legalle_ver_89.html")}
    assert nomes == {"Médico Ginecologista", "Médico Pediatra"}


def test_tupy_jaragua_6_especialidades_sem_descartar_nenhuma():
    # edital mais denso de cargo médico nas fixtures (6 especialidades).
    d = act.parse_detalhe(_ler("tupy_ver_101.html"))
    assert (d.municipio, d.uf) == ("Jaraguá do Sul", "SC")
    medicos = [c for c in d.cargos if eh_cargo_medico(c.nome)]
    assert len(medicos) == 6
    nomes = " | ".join(c.nome for c in medicos)
    for esp in ("Clínico Geral", "Neurologista", "Neurologista Pediátrico", "Otorrino", "Psiquiatra", "Reumatologista"):
        assert esp in nomes
    assert all(c.salario is None for c in medicos)  # Tupy: salário só no PDF


def test_tupy_corupa_veterinario_e_filtrado_pediatra_fica():
    d = act.parse_detalhe(_ler("tupy_ver_99.html"))
    assert any("Veterinário" in c.nome for c in d.cargos)  # parser não descarta; filtro é do runner
    assert [c.nome for c in d.cargos if eh_cargo_medico(c.nome)] == ["Médico Pediatra - 20h"]


def test_editais_sem_cargo_medico():
    for f in ("legalle_ver_88.html", "tupy_ver_100.html", "tupy_ver_90.html"):
        assert _medicos(f) == []


def test_todo_cargo_tem_nome_e_cargos_nao_somem():
    # contagem de spans de cargo no HTML == cargos parseados
    import re

    for f in ("legalle_ver_86.html", "legalle_ver_89.html", "tupy_ver_101.html", "tupy_ver_99.html", "tupy_ver_90.html"):
        html = _ler(f)
        esperado = len(re.findall(r'aria-controls="cargo\d+">', html))
        assert len(act.parse_detalhe(html).cargos) == esperado, f


def test_helpers():
    assert act.parse_salario("1.835,25") == Decimal("1835.25")
    assert act.parse_salario("R$ 810,50 mensais") == Decimal("810.50")
    assert act.parse_salario("a combinar") is None
    assert act.tipo_oportunidade("Concurso Público 001/2026") == "concurso_efetivo"
    assert act.tipo_oportunidade("Processo Seletivo ACT PS 002/2026/SEMSA") == "processo_seletivo_temporario"
    assert act.numero_edital("Concurso Público 001/2026") == "001/2026"
    assert act.identificador_externo("tupy", 101, "Médico Pediatra - 20h") == "act-tupy-101-medico-pediatra-20h"
    assert act.escolher_edital([]) is None
