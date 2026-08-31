from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.fontes import imeso

FIXTURES = Path(__file__).parent / "fixtures" / "imeso"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_editais_encontra_uruana_de_minas():
    itens = imeso.listar_editais(_ler_fixture("edital_listagem.html"))

    assert len(itens) == 117
    uruana = next(i for i in itens if i.edital_id == 134)
    assert uruana.entidade == "PREFEITURA MUNICIPAL DE URUANA DE MINAS/MG"
    assert uruana.tipo_processo == "Processo Seletivo Público"
    assert uruana.numero_edital == "001/2026"
    assert uruana.inscricoes_inicio.isoformat() == "2026-08-17"
    assert uruana.inscricoes_fim.isoformat() == "2026-09-17"
    assert uruana.status == "Inscrições Abertas"


def test_listar_editais_status_vem_da_aba_nao_do_botao():
    # achado real: 99/117 itens têm o mesmo texto de botão ("Mais detalhes")
    # dos itens "futuros"/"andamento" mesmo estando encerrados — status tem
    # que vir da aba (tab-pane), não do botão.
    itens = imeso.listar_editais(_ler_fixture("edital_listagem.html"))
    from collections import Counter

    contagem = Counter(i.status for i in itens)
    assert contagem["Encerrado"] == 99
    assert contagem["Inscrições Abertas"] == 2
    assert contagem["Em andamento (inscrições encerradas)"] == 9
    assert contagem["Futuro (inscrições ainda não abertas)"] == 5
    assert contagem["Suspenso/cancelado"] == 2


def test_parsear_edital_consorcio_cidasg():
    html = _ler_fixture("edital_ver_131_cidasg.html")
    vagas = imeso.parsear_edital(html, url="https://portal.imeso.com.br/edital/ver/131")

    assert len(vagas) == 6
    cargos = {v.cargo: v for v in vagas}

    engenheiro = cargos["ENGENHEIRO CIVIL"]
    assert engenheiro.salario == Decimal("5000.00")
    assert engenheiro.municipio == "São Pedro do Suaçuí"
    assert engenheiro.uf == "MG"
    assert engenheiro.numero_edital == "001/2026"
    assert engenheiro.tipo_processo == "Concurso Público"
    assert engenheiro.orgao == "CONSÓRCIO INTERMUNICIPAL PARA O DESENVOLVIMENTO DO ALTO SUAÇUÍ GRANDE (CIDASG)"
    assert engenheiro.inscricoes_inicio.isoformat() == "2026-07-27"
    assert engenheiro.inscricoes_fim.isoformat() == "2026-08-27"

    assert cargos["NUTRICIONISTA"].salario == Decimal("3000.00")


def test_parsear_edital_prefeitura_uruana_de_minas():
    html = _ler_fixture("edital_ver_134_uruana_de_minas.html")
    vagas = imeso.parsear_edital(html, url="https://portal.imeso.com.br/edital/ver/134")

    assert len(vagas) == 4
    assert all(v.municipio == "Uruana de Minas" and v.uf == "MG" for v in vagas)
    assert all(v.salario == Decimal("3242.00") for v in vagas)

    ace = next(v for v in vagas if v.cargo == "AGENTE DE COMBATE ÀS ENDEMIAS")
    assert ace.requisitos == "Ensino Médio Completo"


def test_identificador_externo_por_edital_e_cargo():
    html = _ler_fixture("edital_ver_131_cidasg.html")
    vagas = imeso.parsear_edital(html, url="https://portal.imeso.com.br/edital/ver/131")
    ids = {imeso.identificador_externo(v, edital_id=131) for v in vagas}

    assert len(ids) == len(vagas)  # todo cargo gera chave única
    assert "imeso-131-engenheiro-civil" in ids
