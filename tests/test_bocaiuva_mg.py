from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import bocaiuva_mg

FIXTURES = Path(__file__).parent / "fixtures" / "bocaiuva_mg"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_le_listagem_sem_perder_linha():
    html = _ler_fixture("listagem_concursos_publicos.html")
    itens = bocaiuva_mg.listar_processos(html)

    # achado real: esta listagem tem 79 linhas (bem menor que a de João
    # Monlevade — pode ser só uma janela recente, não o histórico
    # completo; o parser não assume nada sobre o tamanho).
    assert len(itens) == 79
    assert len({item.processo_id for item in itens}) == 79


def test_listar_processos_le_coluna_area_ausente_em_joao_monlevade():
    # achado real: diferente de João Monlevade, a listagem de Bocaiúva
    # tem uma coluna "Área" (secretaria responsável) — o parser genérico
    # (xfind_cms) lê essa coluna dinamicamente pelo cabeçalho, nunca por
    # posição fixa.
    html = _ler_fixture("listagem_concursos_publicos.html")
    itens = bocaiuva_mg.listar_processos(html)

    item = next(i for i in itens if i.processo_id == 81)
    assert item.numero_edital == "3/2026"
    assert item.categoria == "Edital"
    assert item.area == "Saúde"
    assert "03/2026" in item.titulo
    assert item.data_publicacao == date(2026, 4, 16)
    assert item.url == "https://www.bocaiuva.mg.gov.br/concursos_view/81"


def test_filtrar_processos_saude_nao_descarta_processo_cuja_sumula_nao_cita_saude():
    # achado crítico de prioridade #1: pelo menos 5 dos 10 processos
    # Área="Saúde" da fixture têm súmula SEM nenhuma palavra de saúde —
    # só a coluna Área revela isso. Se o filtro checasse só o título,
    # esses processos (incluindo o Edital 03/2026, que tem convocação de
    # médico confirmada) seriam descartados silenciosamente antes de
    # baixar o PDF.
    html = _ler_fixture("listagem_concursos_publicos.html")
    itens = bocaiuva_mg.listar_processos(html)
    saude = bocaiuva_mg.filtrar_processos_saude(itens)

    ids_saude = {item.processo_id for item in saude}
    # processo 81 (Edital 03/2026) — súmula "EDITAL DE PROCESSO SELETIVO
    # SIMPLIFICADO N 03/2026 MODALIDADE: ANÁLISE DE TÍTULOS E EXPERIÊNCIA
    # PROFISSIONAL" não cita "saúde"/"médico" em lugar nenhum; só Área.
    assert 81 in ids_saude
    item_81 = next(i for i in itens if i.processo_id == 81)
    assert not bocaiuva_mg.eh_cargo_saude(item_81.titulo)  # sem área, não bateria
    assert bocaiuva_mg.eh_cargo_saude(item_81.titulo, item_81.area)  # com área, bate

    # processo 57 (Processo Seletivo 01/2026) — súmula genérica demais
    # pra citar saúde, só a Área denuncia.
    assert 57 in ids_saude

    # nenhum dos 15 processos Área=Saúde ou súmula-com-saúde da
    # investigação real foi descartado.
    assert len(saude) == 15


def test_extrair_detalhe_le_template_card_sem_tabela_tb_concursos():
    # achado real: Bocaiúva usa um template de detalhe diferente de João
    # Monlevade (sem <table id="tb_concursos">, layout em cards com
    # <label> + valor) — extrair_detalhe precisa reconhecer os dois.
    html = _ler_fixture("edital_03_2026_saude_medico_detalhe.html")
    detalhe = bocaiuva_mg.extrair_detalhe(html)

    assert detalhe.numero_edital == "3/2026"
    assert detalhe.categoria == "Edital"
    assert "03/2026" in detalhe.titulo
    assert detalhe.data_publicacao == date(2026, 4, 16)
    assert len(detalhe.anexos) == 12


def test_extrair_detalhe_edital_06_2025_processos_seletivos():
    html = _ler_fixture("edital_06_2025_saude_medico_enfermeiro_detalhe.html")
    detalhe = bocaiuva_mg.extrair_detalhe(html)

    assert detalhe.numero_edital == "6/2025"
    assert detalhe.categoria == "Processos Seletivos"
    assert "SAÚDE" in detalhe.titulo.upper()
    assert detalhe.data_publicacao == date(2026, 1, 14)
    assert len(detalhe.anexos) == 77


def test_escolher_pdf_edital_03_2026_pega_retificacao_ignora_convocacoes():
    # achado real: 12 anexos, a maioria "Convocação" (excluídos) +
    # "Homologação"/"Resultado preliminar" (excluídos) — sobram o edital
    # original (16/04) e a "Retificação edital 03/2026" (04/05, sem
    # nenhuma palavra de exclusão no Tipo nem na Descrição) — pega a mais
    # recente.
    html = _ler_fixture("edital_03_2026_saude_medico_detalhe.html")
    detalhe = bocaiuva_mg.extrair_detalhe(html)

    anexo = bocaiuva_mg.escolher_pdf_edital(detalhe.anexos)
    assert anexo is not None
    assert anexo.descricao == "Retificação edital 03/2026"
    assert anexo.data == date(2026, 5, 4)
    assert anexo.url == "https://www.bocaiuva.mg.gov.br/licitacoes/92144bd45665291994b8b0b454aa6ee2.pdf"


def test_escolher_pdf_edital_06_2025_ignora_convocacao_mesmo_sem_a_palavra_na_descricao():
    # achado crítico: existe 1 anexo real (22/04/2026, tipo="Convocação")
    # cuja Descrição é "recepcionista zona urbana (processo 06/2025)
    # SECRETARIA DE SAÚDE" — NÃO contém a palavra "convocação", e é mais
    # recente que o edital de abertura real (04/02/2026). Se
    # escolher_pdf_edital checasse só a Descrição, escolheria esse anexo
    # por engano. A checagem contra o campo Tipo evita isso.
    html = _ler_fixture("edital_06_2025_saude_medico_enfermeiro_detalhe.html")
    detalhe = bocaiuva_mg.extrair_detalhe(html)

    excluido = next(a for a in detalhe.anexos if a.data == date(2026, 4, 22))
    assert "recepcionista" in excluido.descricao.lower()
    assert "convoca" not in excluido.descricao.lower()
    assert excluido.tipo == "Convocação"

    anexo = bocaiuva_mg.escolher_pdf_edital(detalhe.anexos)
    assert anexo is not None
    assert anexo.data == date(2026, 2, 4)
    assert anexo.tipo == "Edital"
    assert "EDITAL" in anexo.descricao.upper()
    assert anexo.url == "https://www.bocaiuva.mg.gov.br/licitacoes/6f19c606953153665c5c3b3868079ad7.pdf"


def test_extrair_ids_processados_decodifica_id_do_prefixo_proprio():
    identificadores = {
        "bocaiuva-mg-81-medico-plantonista",
        "bocaiuva-mg-56-enfermeiro",
        "pmjm-mg-1420-medico-plantonista-ortopedista",  # de outra fonte, nunca deve colidir
    }
    ids = bocaiuva_mg.extrair_ids_processados(identificadores)
    assert ids == {81, 56}


def test_extrair_processo_id():
    assert bocaiuva_mg.extrair_processo_id("https://www.bocaiuva.mg.gov.br/concursos_view/81") == 81
    assert bocaiuva_mg.extrair_processo_id("/concursos_view/7") == 7
    assert bocaiuva_mg.extrair_processo_id("https://www.bocaiuva.mg.gov.br/outra-pagina") is None
