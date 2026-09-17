from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import guiricema_mg

FIXTURES = Path(__file__).parent / "fixtures" / "guiricema"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_le_listagem_sem_perder_item():
    html = _ler_fixture("listagem_processos_seletivos.html")
    itens = guiricema_mg.listar_processos(html)

    assert len(itens) == 15
    assert len({item.processo_id for item in itens}) == 15


def test_listar_processos_le_edital_alvo_medico_esf():
    html = _ler_fixture("listagem_processos_seletivos.html")
    itens = guiricema_mg.listar_processos(html)

    alvo = next(i for i in itens if i.processo_id == 80642)
    assert alvo.titulo == "EDITAL DE PROCESSO SELETIVO PUBLICO CADASTRO RESERVA Nº 27/2026"
    assert alvo.cargo == "MÉDICO ESF (40 HORAS)"
    assert alvo.data_publicacao == date(2026, 9, 9)
    assert alvo.url_pdf == (
        "https://www.guiricema.mg.gov.br/wp-content/uploads/2026/09/"
        "EDITAL-DE-PROCESSO-SELETIVO-PUBLICO-CADASTRO-RESERVA-No-27-2026.pdf"
    )


def test_listar_processos_le_cargo_com_e_sem_prefixo():
    # achado real: o mesmo campo vem às vezes cru ("MÉDICO ESF...") e às
    # vezes com prefixo "Cargo: " ("Cargo: Nutricionista") — os dois
    # formatos precisam virar o mesmo texto sem prefixo.
    html = _ler_fixture("listagem_processos_seletivos.html")
    itens = guiricema_mg.listar_processos(html)

    cargos = {item.processo_id: item.cargo for item in itens}
    assert "MÉDICO ESF (40 HORAS)" in cargos.values()
    assert "Nutricionista" in cargos.values()
    assert not any(c.lower().startswith("cargo:") for c in cargos.values())


def test_eh_cargo_saude_casa_titulo_ou_cargo():
    assert guiricema_mg.eh_cargo_saude("EDITAL 1/2026", "MÉDICO ESF (40 HORAS)")
    assert guiricema_mg.eh_cargo_saude("EDITAL PARA SECRETARIA DE SAÚDE", "")
    assert not guiricema_mg.eh_cargo_saude("EDITAL 1/2026", "AUXILIAR DE SERVIÇOS GERAIS")


def test_eh_edital_abertura_descarta_convocacao_ata_e_prorrogacao():
    assert guiricema_mg.eh_edital_abertura("EDITAL DE PROCESSO SELETIVO PUBLICO CADASTRO RESERVA Nº 27/2026")
    assert not guiricema_mg.eh_edital_abertura("EDITAL DE CONVOCAÇÃO Nº 98/2026")
    assert not guiricema_mg.eh_edital_abertura("Edital de Convocação Especial")
    assert not guiricema_mg.eh_edital_abertura("Ata de Reunião Referente ao Processo Seletivo Público nº 024/2026")
    assert not guiricema_mg.eh_edital_abertura("Prorrogação do prazo de inscrição")


def test_filtrar_processos_saude_abertura_acha_so_o_edital_27_2026():
    # achado real que motivou este parser: das 15 publicações da
    # listagem (convocações, atas, prorrogação, cargos não-saúde), só 1
    # é edital de abertura com indício de cargo de saúde — o alvo real
    # (Médico ESF 40h, que no PDF real também cobre Médico Clínico 20h,
    # não capturado aqui porque o campo `cargo` da listagem é só
    # triagem — ver docstring do módulo e `test_rodar_guiricema.py`).
    html = _ler_fixture("listagem_processos_seletivos.html")
    itens = guiricema_mg.listar_processos(html)

    saude = guiricema_mg.filtrar_processos_saude_abertura(itens)

    assert len(saude) == 1
    assert saude[0].processo_id == 80642


def test_extrair_ids_processados_decodifica_prefixo():
    identificadores = {
        "guiricema-mg-80642-medico-esf-40-horas",
        "guiricema-mg-80642-medico-clinico-20-horas",
        "outra-fonte-99-medico",
    }
    assert guiricema_mg.extrair_ids_processados(identificadores) == {80642}
