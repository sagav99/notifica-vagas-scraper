import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.fontes import nosso_rumo

FIXTURES = Path(__file__).parent / "fixtures" / "nosso_rumo"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def _ler_fixture_json(nome: str) -> list[dict]:
    return json.loads(_ler_fixture(nome))


# --- listar_certames (homepage) --------------------------------------------


def test_listar_certames_encontra_os_77_certames_historicos_sem_duplicar():
    itens = nosso_rumo.listar_certames(_ler_fixture("home_listagem_completa.html"))
    ids = [i.id_projeto for i in itens]
    assert len(itens) == 77
    assert len(set(ids)) == 77  # achado real: card duplicado (destaque + histórico) não pode contar 2x


def test_listar_certames_acha_olimpia_01_2026_com_periodo_de_inscricao():
    itens = nosso_rumo.listar_certames(_ler_fixture("home_listagem_completa.html"))
    olimpia = next(i for i in itens if i.id_projeto == 587)
    assert "OLÍMPIA" in olimpia.nome_certame.upper()
    assert olimpia.periodo_inscricao == "03/09/2026 a 22/09/2026"


def test_listar_certames_acha_itanhaem_01_2026():
    itens = nosso_rumo.listar_certames(_ler_fixture("home_listagem_completa.html"))
    itanhaem = next(i for i in itens if i.id_projeto == 503)
    assert "ITANHAÉM" in itanhaem.nome_certame.upper()


def test_listar_certames_pagina_sem_certames_devolve_lista_vazia():
    assert nosso_rumo.listar_certames("<html><body>nada aqui</body></html>") == []


# --- montar_url_cargos -------------------------------------------------


def test_montar_url_cargos_monta_filtro_json_esperado():
    url = nosso_rumo.montar_url_cargos(587)
    assert url.startswith("https://nossorumo.org.br/Cargo/Pesquisar?filtros=")
    assert "mostrar_finalizado" in url
    # decodifica de volta pra conferir o conteúdo do filtro, não só a string crua
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    filtros = json.loads(query["filtros"][0])
    assert filtros == {"mobile": False, "id": 587, "mostrar_finalizado": True}


# --- listar_cargos / PRIORIDADE #1: nenhuma especialidade médica perdida ---


def test_listar_cargos_olimpia_nao_descarta_nenhuma_das_16_vagas_medicas():
    # edital mais denso das fixtures (47 cargos no total) — achado urgente
    # da investigação: 16 especialidades médicas distintas, todas com
    # "Inscrições Abertas", prazo 22/09/2026. PRIORIDADE #1 do produto
    # (CLAUDE.md): nenhuma pode ser descartada silenciosamente.
    cargos = nosso_rumo.listar_cargos(_ler_fixture_json("olimpia_01_2026_cargos_medico_ativo.json"))
    assert len(cargos) == 47

    medicos = [c for c in cargos if c.cargo.upper().startswith("MÉDICO")]
    assert len(medicos) == 16
    nomes_medicos = {c.cargo for c in medicos}
    assert nomes_medicos == {
        "MÉDICO AUDITOR",
        "MÉDICO CIRURGIÃO VASCULAR",
        "MÉDICO DERMATOLOGISTA",
        "MÉDICO ELETROFISIOLOGISTA",
        "MÉDICO GINECOOBSTETRA",
        "MÉDICO MASTOLOGISTA",
        "MÉDICO OTORRINOLARINGOLOGISTA",
        "MÉDICO PEDIATRA",
        "MÉDICO PROCTOLOGISTA",
        "MÉDICO PSIQUIATRA",
        "MÉDICO PSIQUIATRA INFANTIL",
        "MÉDICO RADIOLOGISTA",
        "MÉDICO REGULADOR",
        "MÉDICO REUMATOLOGISTA",
        "MÉDICO UROLOGISTA",
        "MÉDICO VETERINÁRIO",
    }
    assert all(c.status == "Inscrições Abertas" for c in medicos)
    assert all(c.vagas == 1 for c in medicos)
    assert all(c.salario == Decimal("7727.13") for c in medicos)


def test_listar_cargos_olimpia_tambem_mantem_cargos_nao_medicos_e_outros_cargos_de_saude():
    # confirma que o filtro de "não descartar" vale pro edital inteiro, não
    # só pros cargos médicos: biomédico, enfermeiro, farmacêutico,
    # fisioterapeuta, nutricionista, psicólogo (saúde não-médico) e cargos
    # fora da área de saúde também aparecem.
    cargos = nosso_rumo.listar_cargos(_ler_fixture_json("olimpia_01_2026_cargos_medico_ativo.json"))
    nomes = {c.cargo for c in cargos}
    assert "BIOMÉDICO" in nomes
    assert "ENFERMEIRO – 40 HORAS" in nomes
    assert "FARMACÊUTICO" in nomes
    assert "FISIOTERAPEUTA" in nomes
    assert "NUTRICIONISTA" in nomes
    assert "PSICÓLOGO" in nomes
    assert "MOTORISTA" in nomes  # não-saúde, confirma que não há filtro por área
    assert "PROCURADOR JURÍDICO" in nomes


def test_listar_cargos_olimpia_extrai_campos_do_medico_pediatra():
    cargos = nosso_rumo.listar_cargos(_ler_fixture_json("olimpia_01_2026_cargos_medico_ativo.json"))
    pediatra = next(c for c in cargos if c.cargo == "MÉDICO PEDIATRA")

    assert pediatra.id_cargo == 9989
    assert pediatra.id_projeto == 587
    assert pediatra.status == "Inscrições Abertas"
    assert pediatra.vagas == 1
    assert pediatra.salario == Decimal("7727.13")
    assert "OLÍMPIA" in pediatra.concurso.upper()
    assert "<br>" not in pediatra.concurso
    assert pediatra.cidade_trabalho == "Olímpia"
    assert pediatra.edital_arquivo == "587-3790-639239628303393947.pdf"
    assert pediatra.edital_nome is not None and "Retificado" in pediatra.edital_nome


def test_listar_cargos_itanhaem_142_vagas_de_acs_em_andamento():
    # achado de 2026-09-06: 142 vagas de ACS, saúde não-médico, inscrição
    # já fechada ("Em Andamento") — módulo não filtra por status (isso é
    # responsabilidade do script), então todas as 11 linhas aparecem aqui.
    cargos = nosso_rumo.listar_cargos(_ler_fixture_json("itanhaem_01_2026_cargos_acs.json"))
    assert len(cargos) == 11
    assert all(c.status == "Em Andamento" for c in cargos)
    assert sum(c.vagas or 0 for c in cargos) == 142
    assert all(c.cargo.startswith("ACS -") for c in cargos)


def test_listar_cargos_ignora_item_sem_nome_de_cargo():
    dados = [{"id_cargo": 1, "id_projeto": 587, "status": "Inscrições Abertas", "cargo": "", "vagas": "1"}]
    assert nosso_rumo.listar_cargos(dados) == []


def test_listar_cargos_lista_vazia_devolve_lista_vazia():
    assert nosso_rumo.listar_cargos([]) == []


# --- extrair_nome_concurso ----------------------------------------------


def test_extrair_nome_concurso_remove_br_e_normaliza_espaco():
    bruto = "Concurso Público<br>PREFEITURA MUNICIPAL DA ESTÂNCIA TURÍSTICA DE OLÍMPIA/SP<br>01/2026"
    assert (
        nosso_rumo.extrair_nome_concurso(bruto)
        == "Concurso Público - PREFEITURA MUNICIPAL DA ESTÂNCIA TURÍSTICA DE OLÍMPIA/SP - 01/2026"
    )


# --- parsear_salario -----------------------------------------------------


def test_parsear_salario_formato_brasileiro():
    assert nosso_rumo.parsear_salario("R$ 7.727,13") == Decimal("7727.13")


def test_parsear_salario_sem_milhar():
    assert nosso_rumo.parsear_salario("R$ 2.429,38") == Decimal("2429.38")


def test_parsear_salario_vazio_ou_none_devolve_none():
    assert nosso_rumo.parsear_salario("") is None
    assert nosso_rumo.parsear_salario(None) is None


# --- parsear_vagas ---------------------------------------------------------


def test_parsear_vagas_numero_simples():
    assert nosso_rumo.parsear_vagas("142") == 142


def test_parsear_vagas_zero_nao_vira_none():
    # cadastro de reserva (achado real: Porangaba Médico Generalista com 0
    # vaga) ainda é um número válido, não pode virar None/descartado.
    assert nosso_rumo.parsear_vagas("0") == 0


def test_parsear_vagas_vazio_ou_none_devolve_none():
    assert nosso_rumo.parsear_vagas("") is None
    assert nosso_rumo.parsear_vagas(None) is None


# --- parsear_periodo_inscricao --------------------------------------------


def test_parsear_periodo_inscricao_formato_esperado():
    inicio, fim = nosso_rumo.parsear_periodo_inscricao("03/09/2026 a 22/09/2026")
    assert inicio == date(2026, 9, 3)
    assert fim == date(2026, 9, 22)


def test_parsear_periodo_inscricao_vazio_ou_none_devolve_none_none():
    assert nosso_rumo.parsear_periodo_inscricao(None) == (None, None)
    assert nosso_rumo.parsear_periodo_inscricao("") == (None, None)
    assert nosso_rumo.parsear_periodo_inscricao("sem data nenhuma") == (None, None)


# --- extrair_numero_edital --------------------------------------------


def test_extrair_numero_edital_acha_o_ultimo_segmento():
    assert (
        nosso_rumo.extrair_numero_edital(
            "Concurso Público - PREFEITURA MUNICIPAL DA ESTÂNCIA TURÍSTICA DE OLÍMPIA/SP - 01/2026"
        )
        == "01/2026"
    )


def test_extrair_numero_edital_none_sem_padrao():
    assert nosso_rumo.extrair_numero_edital("texto qualquer sem número nenhum") is None


# --- classificar_tipo_oportunidade -----------------------------------------


def test_classificar_tipo_oportunidade_concurso_publico():
    assert nosso_rumo.classificar_tipo_oportunidade("Concurso Público - PREFEITURA ... - 01/2026") == "concurso_efetivo"


def test_classificar_tipo_oportunidade_processo_seletivo():
    assert (
        nosso_rumo.classificar_tipo_oportunidade("Processo Seletivo Simplificado - PREFEITURA ... - 01/2026")
        == "processo_seletivo_temporario"
    )


def test_classificar_tipo_oportunidade_desconhecido_devolve_none():
    assert nosso_rumo.classificar_tipo_oportunidade("texto qualquer sem padrão nenhum") is None


# --- identificador_externo -----------------------------------------------


def test_identificador_externo_estavel():
    assert nosso_rumo.identificador_externo(9989) == "nosso-rumo-9989"
