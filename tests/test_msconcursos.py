from datetime import date
from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.fontes import msconcursos

FIXTURES = Path(__file__).parent / "fixtures" / "msconcursos"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_extrair_municipio_uf_prefeitura_com_virgula():
    assert msconcursos.extrair_municipio_uf(
        "CONCURSO PÚBLICO - PREFEITURA DE SANTANA DE PARNAÍBA, SP - EDITAL N.º 05/2026"
    ) == ("Santana de Parnaíba", "SP")


def test_extrair_municipio_uf_prefeitura_municipal_com_hifen_sem_espaco():
    # achado real: "PROCESSO SELETIVO SIMPLIFICADO PARA MÉDICO - PREFEITURA
    # MUNICIPAL DE SANTANA DE PARNAÍBA-SP - EDITAL N.º 01/2026" usa hífen
    # colado na UF em vez de vírgula+espaço.
    assert msconcursos.extrair_municipio_uf(
        "PREFEITURA MUNICIPAL DE SANTANA DE PARNAÍBA-SP - EDITAL N.º 01/2026"
    ) == ("Santana de Parnaíba", "SP")


def test_extrair_municipio_uf_outra_uf():
    assert msconcursos.extrair_municipio_uf(
        "CONCURSO PÚBLICO - PREFEITURA MUNICIPAL DE ITAPERUÇU, PR - EDITAL N.º 01-2026"
    ) == ("Itaperuçu", "PR")


def test_extrair_municipio_uf_devolve_none_para_consorcio_intermunicipal():
    # CRIS atende vários municípios ao mesmo tempo — não mapeia pra 1 só,
    # tem que ser descartado, não "chutado" (mesmo padrão de avancasp.py).
    assert msconcursos.extrair_municipio_uf(
        "CONCURSO PÚBLICO - CONSÓRCIO REGIONAL INTERMUNICIPAL DE SAÚDE (CRIS) - (MÉDICO - PSF) - Edital N.º 01/2026."
    ) is None


def test_extrair_municipio_uf_devolve_none_sem_prefeitura():
    # Corpo de Bombeiros de SC — nem prefeitura é, sem UF citada no
    # padrão esperado.
    assert msconcursos.extrair_municipio_uf(
        "CURSO DE FORMAÇÃO DE SARGENTOS - MÉRITO INTELECTUAL - EDITAL 003-2026/DP/CBMSC"
    ) is None


def test_extrair_numero_edital_variacoes_de_formatacao():
    assert msconcursos.extrair_numero_edital("... EDITAL N.º 05/2026") == "05/2026"
    assert msconcursos.extrair_numero_edital("... EDITAL Nº 009/2025") == "009/2025"
    assert msconcursos.extrair_numero_edital("... EDITAL N° 05/2023") == "05/2023"
    assert msconcursos.extrair_numero_edital("... EDITAL N.º 01-2026") == "01-2026"
    assert msconcursos.extrair_numero_edital("EDITAL 003-2026/DP/CBMSC") == "003-2026"


def test_listar_concursos_abertos_le_so_a_secao_inscricoes_abertas():
    # a homepage real também lista "em andamento" (inscrição já fechada) e
    # "realizados" (histórico) com o MESMO html de card — só a seção
    # "abertas" pode virar notificação nova.
    itens = msconcursos.listar_concursos_abertos(_ler_fixture("home_listagem_concursos_ativos.html"))

    assert len(itens) == 2
    ids = {i.concurso_id for i in itens}
    assert ids == {54, 55}
    # concurso 47 ("MÉDICO E MÉDICO PLANTONISTA ... EDITAL N.º 02/2026") é
    # um edital ANTIGO do mesmo tipo de vaga, mas já encerrado — não pode
    # aparecer, senão duplicaria notificação de um concurso que não
    # aceita mais inscrição.
    assert 47 not in ids


def test_listar_concursos_abertos_extrai_concurso_medico_plantonista():
    itens = msconcursos.listar_concursos_abertos(_ler_fixture("home_listagem_concursos_ativos.html"))
    medico = next(i for i in itens if i.concurso_id == 54)

    assert medico.url == (
        "https://msconcursos.com.br/concurso/54/"
        "concurso-publico-para-medico-e-medico-plantonista-prefeitura-de-santana-de-parnaiba-sp"
    )
    assert medico.tipo_processo == "Concurso Público"
    assert medico.numero_edital == "05/2026"
    assert medico.municipio == "Santana de Parnaíba"
    assert medico.uf == "SP"
    assert medico.inscricoes_inicio == date(2026, 8, 19)
    assert medico.inscricoes_fim == date(2026, 9, 13)


def test_listar_vagas_html_nao_descarta_nenhuma_especialidade_medica():
    # o concurso real tem 17 cargos na seção de cargos, TODOS de nível
    # superior/médico (16 especialidades médicas + o plantonista) — a
    # fixture salva é a página completa, não um trecho, então o teste
    # prova que as 17 linhas aparecem, nenhuma descartada silenciosamente.
    vagas = msconcursos.listar_vagas_html(
        _ler_fixture("santana_parnaiba_medico_plantonista_edital05_2026.html")
    )

    assert len(vagas) == 17
    cargos = {v.cargo for v in vagas}
    assert cargos == {
        "MÉDICO ANGIOLOGISTA",
        "MÉDICO CLÍNICA MÉDICA",
        "MÉDICO COLPOSCOPISTA",
        "MÉDICO ENDOCRINOLOGISTA",
        "MÉDICO ENDOCRINOLOGISTA INFANTIL",
        "MÉDICO GASTROENTEROLOGISTA",
        "MÉDICO GINECOLOGISTA/OBSTETRA",
        "MÉDICO HEMATOLOGISTA",
        "MÉDICO MASTOLOGISTA",
        "MÉDICO NEUROPEDIATRA",
        "MÉDICO ORTOPEDISTA",
        "MÉDICO PEDIATRA",
        "MÉDICO PLANTONISTA 24H - URGÊNCIA E EMERGÊNCIA",
        "MÉDICO PSIQUIATRA",
        "MÉDICO PSIQUIATRA DA INFÂNCIA E ADOLESCÊNCIA",
        "MÉDICO ULTRASSONOGRAFISTA",
        "MÉDICO UROLOGISTA",
    }
    assert all(v.escolaridade == "NÍVEL SUPERIOR" for v in vagas)


def test_listar_vagas_html_medico_plantonista_24h_4_vagas():
    # achado real que motivou a investigação da fonte.
    vagas = msconcursos.listar_vagas_html(
        _ler_fixture("santana_parnaiba_medico_plantonista_edital05_2026.html")
    )
    plantonista = next(v for v in vagas if v.cargo == "MÉDICO PLANTONISTA 24H - URGÊNCIA E EMERGÊNCIA")

    assert plantonista.salario == Decimal("14975.66")
    assert plantonista.salario_texto == "R$ 14.975,66"
    assert plantonista.carga_horaria == "24h"
    assert plantonista.quantidade == 4
    assert plantonista.taxa_inscricao == Decimal("32.52")
    assert plantonista.etapas == "PROVA OBJETIVA E PROVA DE TÍTULOS"


def test_listar_vagas_html_nao_descarta_cargo_com_zero_vagas_no_momento():
    # "Quantidade de vagas: 0" ainda é uma linha real da tabela (7 das 17
    # especialidades médicas do concurso real estão assim) — decisão de
    # negócio sobre notificar ou não fica pro script, não pro parser (ver
    # docstring do módulo); o parser nunca decide o que existe ou não.
    vagas = msconcursos.listar_vagas_html(
        _ler_fixture("santana_parnaiba_medico_plantonista_edital05_2026.html")
    )
    clinica_medica = next(v for v in vagas if v.cargo == "MÉDICO CLÍNICA MÉDICA")

    assert clinica_medica.quantidade == 0
    assert clinica_medica.salario == Decimal("13087.86")


def test_listar_vagas_html_quantidade_com_zero_a_esquerda():
    # "Quantidade de vagas: 04" (Plantonista) e "01" (Angiologista, Urologista
    # etc.) têm zero à esquerda no HTML real — não pode virar None por
    # falha de regex.
    vagas = msconcursos.listar_vagas_html(
        _ler_fixture("santana_parnaiba_medico_plantonista_edital05_2026.html")
    )
    urologista = next(v for v in vagas if v.cargo == "MÉDICO UROLOGISTA")
    assert urologista.quantidade == 1


def test_identificador_externo_gera_chave_unica_por_cargo():
    vagas = msconcursos.listar_vagas_html(
        _ler_fixture("santana_parnaiba_medico_plantonista_edital05_2026.html")
    )
    ids = {msconcursos.identificador_externo(54, v) for v in vagas}

    assert len(ids) == len(vagas)  # nenhum cargo colide com outro
    assert "msconcursos-54-medico-plantonista-24h-urgencia-e-emergencia" in ids
    assert "msconcursos-54-medico-ginecologista-obstetra" in ids
