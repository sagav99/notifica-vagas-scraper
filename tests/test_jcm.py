from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import jcm

FIXTURES = Path(__file__).parent / "fixtures" / "jcm"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_abertos_extrai_municipio_uf_tipo_e_orgao():
    itens = jcm.listar_processos_abertos(_ler_fixture("abertos.html"))

    assert len(itens) == 3
    concurso = next(i for i in itens if i.processo_id == 261)
    assert concurso.url == "https://concursosjcm.com.br/informacoes/261/"
    assert concurso.municipio == "Conceição da Barra de Minas"
    assert concurso.uf == "MG"
    assert concurso.tipo_processo == "Concurso Público"
    assert concurso.numero_edital == "001/2026"
    assert concurso.orgao == "Prefeitura Municipal"


def test_listar_processos_abertos_reconhece_camara_municipal():
    itens = jcm.listar_processos_abertos(_ler_fixture("abertos.html"))
    ingai = next(i for i in itens if i.processo_id == 260)
    assert ingai.municipio == "Ingaí"
    assert ingai.orgao == "Câmara Municipal"


def test_listar_documentos_extrai_titulo_data_e_pdf():
    documentos = jcm.listar_documentos(_ler_fixture("detalhe_conceicao.html"))
    assert len(documentos) == 6
    primeiro = documentos[0]
    assert primeiro.titulo == "Edital - Retificado"
    assert primeiro.data == date(2026, 6, 19)
    assert primeiro.url_pdf.startswith("https://anexos-r2.selecao.net.br/")


def test_escolher_edital_prefere_retificacao_mais_recente_por_data():
    # achado real: a ordem no HTML não é cronológica (leis municipais com
    # a mesma data do edital original aparecem entre o edital e a
    # retificação) — escolher_edital tem que comparar datas, não posição.
    documentos = jcm.listar_documentos(_ler_fixture("detalhe_conceicao.html"))
    edital = jcm.escolher_edital(documentos)
    assert edital is not None
    assert edital.titulo == "Retificação 01 ao Edital"
    assert edital.data == date(2026, 8, 17)


def test_escolher_edital_lista_vazia_devolve_none():
    assert jcm.escolher_edital([]) is None


def test_listar_vagas_html_junta_paginas_de_mais_de_uma_tabela():
    # achado real: 19 cargos vieram em 2 <table> dentro do mesmo container
    vagas = jcm.listar_vagas_html(_ler_fixture("detalhe_conceicao.html"))
    assert len(vagas) == 19
    motorista = next(v for v in vagas if v.cargo == "Motorista")
    assert motorista.quantidade == 10
    medico = next(v for v in vagas if v.cargo == "Médico do PSF")
    assert medico.quantidade == 2
