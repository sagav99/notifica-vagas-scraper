from datetime import datetime, timezone

from notifica_vagas_scraper.fontes import fundatec


def test_tem_cargo_medico_pega_cargo_e_ignora_menções_soltas():
    assert fundatec.tem_cargo_medico("Cargos\nMédico Pediatra 20h\nEnfermeiro")
    assert fundatec.tem_cargo_medico("101 - MÉDICO CLÍNICO GERAL")
    assert not fundatec.tem_cargo_medico("Entrega do laudo\nMédico e/ou Atendimento Especial para recurso")
    assert not fundatec.tem_cargo_medico("médico de sua confiança. A Equipe")
    assert not fundatec.tem_cargo_medico("Médico Veterinário")


def test_municipio_e_uf_do_cabecalho():
    assert fundatec.municipio_e_uf("PREFEITURA MUNICIPAL DE SÃO MARCOS/RS - Edital de Abertura\nX") == ("SÃO MARCOS", "RS")
    assert fundatec.municipio_e_uf("ESTADO DO RIO GRANDE DO SUL\nPREFEITURA MUNICIPAL DE IJUÍ\nEDITAL") == ("IJUÍ", "RS")
    assert fundatec.municipio_e_uf("CÂMARA MUNICIPAL DE VEREADORES DE BAGÉ/RS\nEDITAL") is None


def test_casar_municipio_sem_acento_e_caixa():
    mapa = {("SAO MARCOS", "RS"): 4320800}
    assert fundatec.casar_municipio("São Marcos", "RS", mapa) == 4320800
    assert fundatec.casar_municipio("São Marcos", "SC", mapa) is None


def test_recentes_usa_ultima_modificacao():
    agora = datetime(2026, 10, 28, tzinfo=timezone.utc)
    antigo = fundatec.EditalFundatec(1, "1/a.pdf", datetime(2026, 8, 1, tzinfo=timezone.utc))
    novo = fundatec.EditalFundatec(1, "1/b.pdf", datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert not fundatec.recentes([antigo], dias=45, agora=agora)
    assert fundatec.recentes([antigo, novo], dias=45, agora=agora)
