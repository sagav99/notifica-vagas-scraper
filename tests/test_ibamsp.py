from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.fontes import ibamsp

FIXTURES = Path(__file__).parent / "fixtures" / "ibamsp_sp"


def _ler_fixture(nome: str) -> str:
    # fixtures reais salvas pelo pesquisador-fonte vieram em ISO-8859-1
    # (confirmado com `file`/tentativa de decode utf-8), não utf-8 como as
    # outras fontes do projeto.
    return (FIXTURES / nome).read_text(encoding="latin-1")


def test_listar_processos_abertos_le_os_10_processos_ativos():
    itens = ibamsp.listar_processos_abertos(_ler_fixture("listagem_inscricoes_abertas.html"))

    assert len(itens) == 10
    ids = {i.processo_id for i in itens}
    assert ids == {186, 187, 189, 190, 191, 192, 193, 194, 195, 196}


def test_listar_processos_abertos_extrai_titulo_tipo_e_numero_edital():
    itens = ibamsp.listar_processos_abertos(_ler_fixture("listagem_inscricoes_abertas.html"))

    catanduva_03 = next(i for i in itens if i.processo_id == 196)
    assert catanduva_03.url == "https://www.ibamsp-concursos.org.br/informacoes/196/"
    assert catanduva_03.titulo == "CATANDUVA - PROCESSO SELETIVO - 03/2026"
    assert catanduva_03.tipo_processo == "Processo Seletivo"
    assert catanduva_03.numero_edital == "03/2026"

    # vestibular sem município reconhecível — o parser não descarta nem
    # tenta adivinhar, só devolve o título cru (casamento com município é
    # responsabilidade do chamador, ver docstring do módulo).
    vestibular = next(i for i in itens if i.processo_id == 186)
    assert vestibular.tipo_processo == "Vestibular"
    assert vestibular.numero_edital == "12/2026"
    assert "SANTA CASA" in vestibular.titulo


def test_listar_processos_abertos_mantem_3_editais_distintos_do_mesmo_concurso():
    # achado real: Mogi Mirim 02/2026 tem 3 processos (ids 187/189/190),
    # só diferenciados pelo sufixo "- Edital NN" no número — perder esse
    # sufixo colidiria os 3 na dedup de `db.inserir_vaga_com_evidencia`
    # (chave usa numero_edital).
    itens = ibamsp.listar_processos_abertos(_ler_fixture("listagem_inscricoes_abertas.html"))
    mogi = {i.processo_id: i for i in itens if i.titulo.startswith("MOGI MIRIM")}

    assert len(mogi) == 3
    assert mogi[187].numero_edital == "02/2026 - Edital 01"
    assert mogi[189].numero_edital == "02/2026 - Edital 02"
    assert mogi[190].numero_edital == "02/2026 - Edital 03"
    numeros = {item.numero_edital for item in mogi.values()}
    assert len(numeros) == 3


def test_listar_vagas_html_franca_nao_descarta_nenhuma_das_30_especialidades_medicas():
    # achado real que motivou o parser dinâmico de colunas: a tabela de
    # Franca tem só 5 colunas (sem Salário/Carga Horária) — um índice fixo
    # de coluna (como em avancasp.py) teria descartado a tabela inteira
    # por não bater `len(celulas) == 7`. Prioridade #1 do produto: nenhuma
    # especialidade médica pode sumir.
    vagas = ibamsp.listar_vagas_html(_ler_fixture("franca_01_2026_medicos.html"))

    assert len(vagas) == 30
    assert all(v.cargo.startswith("MEDICO") for v in vagas)
    assert "MEDICO PEDIATRA" in {v.cargo for v in vagas}
    assert "MEDICO PSIQUIATRA ADULTO" in {v.cargo for v in vagas}

    # tabela de Franca não tem coluna de Salário/Carga Horária — tem que
    # ficar None sem quebrar o parsing das outras colunas.
    pediatra = next(v for v in vagas if v.cargo == "MEDICO PEDIATRA")
    assert pediatra.salario is None
    assert pediatra.salario_texto is None
    assert pediatra.carga_horaria is None
    assert pediatra.escolaridade == "Ensino Superior"
    assert pediatra.taxa_inscricao == Decimal("94.00")


def test_listar_vagas_html_mogi_mirim_nao_descarta_nenhuma_das_39_linhas():
    # tabela de 7 colunas (Cód./Vaga/Escolaridade/Salário/Carga
    # Horária/Qtde./Valor de Inscrição) — 28 especialidades médicas + 3
    # médicos gerais (Regulador/Neurologista/Proctologista) + 6
    # dentistas + 2 enfermeiros = 39.
    vagas = ibamsp.listar_vagas_html(_ler_fixture("mogi_mirim_01_2026_edital03_medicos.html"))

    assert len(vagas) == 39
    cargos_medicos = {v.cargo for v in vagas if v.cargo.startswith("MÉDICO")}
    assert len(cargos_medicos) == 31  # 28 especialistas + regulador/neurologista/proctologista
    assert "MÉDICO ALERGOLOGISTA" in cargos_medicos
    assert "MÉDICO GINECO - OBSTETRA" in cargos_medicos
    assert "MÉDICO NEONATOLOGISTA" in cargos_medicos

    alergologista = next(v for v in vagas if v.cargo == "MÉDICO ALERGOLOGISTA")
    assert alergologista.escolaridade == "Ensino Superior"
    assert alergologista.salario == Decimal("4862.12")
    assert alergologista.salario_texto == "R$ 4.862,12"
    assert alergologista.carga_horaria == "20h/sem."
    assert alergologista.quantidade is None
    assert alergologista.cadastro_reserva is True
    assert alergologista.taxa_inscricao == Decimal("105.00")

    angiologista = next(v for v in vagas if v.cargo == "MÉDICO ANGIOLOGISTA")
    assert angiologista.quantidade == 1
    assert angiologista.cadastro_reserva is False


def test_listar_vagas_html_mogi_mirim_02_reconhece_coluna_vencimentos():
    # tenant/edital diferente do mesmo site rotula a coluna de salário
    # "Vencimentos (R$)" em vez de "Salário (R$)" — o parser tem que
    # reconhecer os dois rótulos.
    vagas = ibamsp.listar_vagas_html(_ler_fixture("mogi_mirim_02_2026_edital01_ativo_sem_medico.html"))

    assert len(vagas) == 9
    cargos = {v.cargo for v in vagas}
    assert "MÉDICO" not in cargos and not any("MÉDICO" in c or "MEDICO" in c for c in cargos)

    merendeira = next(v for v in vagas if v.cargo == "MERENDEIRA")
    assert merendeira.salario == Decimal("1643.08")
    assert merendeira.carga_horaria == "40h"
    assert merendeira.quantidade == 5
    assert merendeira.cadastro_reserva is False

    veterinario = next(v for v in vagas if v.cargo == "VETERINÁRIO")
    assert veterinario.cadastro_reserva is True
    assert veterinario.quantidade is None


def test_identificador_externo_gera_chave_unica_por_processo_e_cargo():
    vagas = ibamsp.listar_vagas_html(_ler_fixture("mogi_mirim_01_2026_edital03_medicos.html"))
    ids = {ibamsp.identificador_externo(190, v) for v in vagas}

    assert len(ids) == len(vagas)
    assert "ibamsp-190-medico-alergologista" in ids


def test_extrair_periodo_inscricoes():
    inicio, fim = ibamsp.extrair_periodo_inscricoes(
        _ler_fixture("mogi_mirim_01_2026_edital03_medicos.html")
    )
    assert inicio.isoformat() == "2026-02-26"
    assert fim.isoformat() == "2026-03-26"


def test_extrair_periodo_inscricoes_ausente_devolve_none():
    assert ibamsp.extrair_periodo_inscricoes("<html></html>") == (None, None)


def test_listar_documentos_e_escolher_edital_reaproveitados_de_proseleta():
    # Documento/escolher_edital vêm prontos de proseleta.py (mesma
    # plataforma da JCM/ACCESS/Avança SP) — só confere a integração com o
    # HTML real do IBAM-SP.
    documentos = ibamsp.listar_documentos(_ler_fixture("mogi_mirim_01_2026_edital03_medicos.html"))
    assert len(documentos) > 0

    edital = ibamsp.escolher_edital(documentos)
    assert edital is not None
    assert "edital" in edital.titulo.lower()
    assert edital.url_pdf.startswith("https://anexos.cdn.selecao.net.br/")
