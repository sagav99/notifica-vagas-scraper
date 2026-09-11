from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import pmjm_mg

FIXTURES = Path(__file__).parent / "fixtures" / "pmjm_mg"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_le_historico_completo_sem_perder_linha():
    html = _ler_fixture("concursos_publicos_listagem_completa.html")
    itens = pmjm_mg.listar_processos(html)

    # achado real da investigação: histórico inteiro desde 2011 numa
    # página só (sem paginação AJAX real) — confirma que o parser não
    # perde nenhuma das 1078 linhas da fixture.
    assert len(itens) == 1078
    assert len({item.processo_id for item in itens}) == 1078


def test_listar_processos_acha_edital_12_2026_ortopedista():
    html = _ler_fixture("concursos_publicos_listagem_completa.html")
    itens = pmjm_mg.listar_processos(html)

    item = next(i for i in itens if i.processo_id == 1420)
    assert item.numero_edital == "12/2026"
    assert item.categoria == "Processos Seletivos"
    assert "ORTOPEDISTA" in item.titulo
    assert item.data_publicacao == date(2026, 7, 9)
    assert item.url == "https://www.pmjm.mg.gov.br/concursos_view/1420"


def test_eh_cargo_saude_cobre_variacoes_reais_da_fixture():
    # achados reais da listagem completa — nenhuma dessas variações pode
    # ser descartada silenciosamente (prioridade #1 do produto).
    titulos_saude = [
        "Edital 12-2026 Médico Plantonista - ORTOPEDISTA",
        "Edital 09-2026  PEDIATRA - Médico Plantonista",
        "Edital 08-2026 Farmacêutico",
        "EDITAL Nº 03/2026 PROCESSO SELETIVO PÚBLICO PARA CIRURGIÃO DENTISTA – ESF",
        "EDITAL DE PROCESSO SELETIVO SIMPLIFICADO N 03 - PSICÓLOGO",
        "Edital 02-2026 NUTRICIONISTA",
        "Edital 01-2026 ENFERMEIRO",
        "EDITAL DE PROCESSO SELETIVO SIMPLIFICADO Nº 01 - ASSISTENTE SOCIAL",
        "EDITAL Nº 10/2025 - PROCESSO SELETIVO PÚBLICO PARA O CARGO DE MÉDICO PLANTONISTA (UROLOGISTA)",
        "PROCESSO SELETIVO PÚBLICO PARA O CARGO DE TERAPEUTA OCUPACIONAL E FISIOTERAPEUTA",
        "EDITAL Nº 06/2024 – SECRETARIA MUNICIPAL DE SAÚDE",
    ]
    for titulo in titulos_saude:
        assert pmjm_mg.eh_cargo_saude(titulo), f"não reconheceu cargo de saúde em: {titulo!r}"


def test_eh_cargo_saude_nao_confunde_cargo_claramente_nao_saude():
    assert not pmjm_mg.eh_cargo_saude("Edital 85/2026 Processo Seletivo - Especialista e Professor de História.")
    # mistura saúde + não-saúde no mesmo título ainda deve contar como
    # saúde (deliberadamente permissivo, ver docstring do módulo).
    assert pmjm_mg.eh_cargo_saude("PSICÓLOGO E TÉCNICO EM ESPORTE")


def test_filtrar_processos_saude_nao_descarta_nenhum_cargo_medico_real():
    html = _ler_fixture("concursos_publicos_listagem_completa.html")
    itens = pmjm_mg.listar_processos(html)
    saude = pmjm_mg.filtrar_processos_saude(itens)

    # cadência confirmada na investigação: ~1 processo médico/mês nos
    # últimos 20 meses, mais outros cargos de saúde intercalados — nunca
    # deve zerar nem cair pra um número muito menor que o real.
    titulos_saude = {item.titulo for item in saude}
    assert any("ORTOPEDISTA" in t for t in titulos_saude)
    assert any("CARDIOLOGISTA" in t.upper() for t in titulos_saude)
    assert any("GINECOLOGISTA" in t.upper() for t in titulos_saude)
    assert any("PEDIATRA" in t.upper() for t in titulos_saude)
    assert len(saude) >= 100  # ~131 confirmados na investigação


def test_extrair_detalhe_le_cabecalho_e_anexos_do_edital_12_2026():
    html = _ler_fixture("concursos_view_1420_medico_ortopedista.html")
    detalhe = pmjm_mg.extrair_detalhe(html)

    assert detalhe.numero_edital == "12/2026"
    assert detalhe.categoria == "Processos Seletivos"
    assert "ORTOPEDISTA" in detalhe.titulo
    assert detalhe.data_publicacao == date(2026, 7, 9)

    assert len(detalhe.anexos) == 3
    descricoes = {anexo.descricao for anexo in detalhe.anexos}
    assert "Edital 12-2026 Médico Plantonista - ORTOPEDISTA" in descricoes
    assert "Resultado Preliminar PS 12-2026 Médico Plantonista (Ortopedista)" in descricoes
    assert "Resultado Final PS 12-2026 Médico Plantonista (Ortopedista)" in descricoes


def test_escolher_pdf_edital_ignora_resultado_e_pega_a_abertura():
    # achado real: os 3 anexos do processo 1420 têm Tipo="Edital" —
    # só a Descrição diferencia abertura de resultado/homologação.
    html = _ler_fixture("concursos_view_1420_medico_ortopedista.html")
    detalhe = pmjm_mg.extrair_detalhe(html)

    anexo = pmjm_mg.escolher_pdf_edital(detalhe.anexos)
    assert anexo is not None
    assert anexo.descricao == "Edital 12-2026 Médico Plantonista - ORTOPEDISTA"
    assert anexo.url == "https://www.pmjm.mg.gov.br/concursos/d884ec4ef59af2a07c06d85a0df75f7d.pdf"
    assert anexo.data == date(2026, 7, 9)


def test_escolher_pdf_edital_so_com_resultado_devolve_none():
    # segurança: nunca extrai cargo/salário a partir de um documento de
    # resultado/homologação — se só isso existir, devolve None e quem
    # chama tenta de novo no próximo cron (ver docstring do módulo).
    anexos = [
        pmjm_mg.Anexo(tipo="Edital", descricao="Resultado Final PS 99-2026", data=date(2026, 8, 1), url="/concursos/x.pdf"),
        pmjm_mg.Anexo(
            tipo="Edital", descricao="Resultado Preliminar PS 99-2026", data=date(2026, 7, 20), url="/concursos/y.pdf"
        ),
    ]
    assert pmjm_mg.escolher_pdf_edital(anexos) is None


def test_escolher_pdf_edital_pega_retificacao_mais_recente_quando_existe():
    anexos = [
        pmjm_mg.Anexo(tipo="Edital", descricao="Edital 05-2026 Enfermeiro", data=date(2026, 5, 1), url="/concursos/a.pdf"),
        pmjm_mg.Anexo(
            tipo="Edital", descricao="1ª Retificação Edital 05-2026 Enfermeiro", data=date(2026, 5, 10), url="/concursos/b.pdf"
        ),
        pmjm_mg.Anexo(
            tipo="Edital", descricao="Resultado Preliminar PS 05-2026", data=date(2026, 6, 1), url="/concursos/c.pdf"
        ),
    ]
    anexo = pmjm_mg.escolher_pdf_edital(anexos)
    assert anexo is not None
    assert anexo.url == "/concursos/b.pdf"


def test_extrair_ids_processados_decodifica_id_do_formato_usado_pelo_helper_compartilhado():
    # formato real montado por processamento_pdf_gemini.processar_pdf_e_gravar_vagas
    # com id_prefix=pmjm_mg.ID_PREFIX e processo_id=item.processo_id.
    identificadores = {
        "pmjm-mg-1420-medico-plantonista-ortopedista",
        "pmjm-mg-1310-medico-esf",
        "outra-fonte-999-cargo-x",  # de outra fonte, nunca deve colidir
    }
    ids = pmjm_mg.extrair_ids_processados(identificadores)
    assert ids == {1420, 1310}


def test_extrair_processo_id():
    assert pmjm_mg.extrair_processo_id("https://www.pmjm.mg.gov.br/concursos_view/1420") == 1420
    assert pmjm_mg.extrair_processo_id("/concursos_view/7") == 7
    assert pmjm_mg.extrair_processo_id("https://www.pmjm.mg.gov.br/outra-pagina") is None
