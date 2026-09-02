from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import kingpage

FIXTURES = Path(__file__).parent / "fixtures" / "kingpage"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_categoria_itapetininga():
    html = _ler_fixture("itapetininga_categoria24_processo-seletivo_listagem.html")
    itens = kingpage.listar_processos_categoria(html, "https://www.itapetininga.sp.gov.br")

    assert len(itens) == 12
    primeiro = itens[0]
    assert primeiro.processo_id == 46
    assert primeiro.numero_ano == "2/2026"
    assert primeiro.modalidade == "Processo Seletivo"
    assert primeiro.objeto == "PROCESSO SELETIVO SIMPLIFICADO Nº 02/2026"
    assert primeiro.url == "https://www.itapetininga.sp.gov.br/concurso/detalhe/46/edital/"


def test_listar_processos_categoria_slug_quebrado_nao_atrapalha_o_id():
    # achado real: o card do id 45 tem um <span style=...> sem escape
    # dentro do slug decorativo do link — reconstruir a URL só com o id
    # (em vez de reaproveitar o href bruto) evita herdar esse lixo.
    html = _ler_fixture("itapetininga_categoria24_processo-seletivo_listagem.html")
    itens = kingpage.listar_processos_categoria(html, "https://www.itapetininga.sp.gov.br")
    item_45 = next(i for i in itens if i.processo_id == 45)
    assert item_45.url == "https://www.itapetininga.sp.gov.br/concurso/detalhe/45/edital/"


def test_listar_processos_categoria_nao_descarta_processo_de_medico():
    # prioridade #1 do produto: nenhuma especialidade de saúde pode ser
    # descartada silenciosamente na listagem — Cajati lista cada cargo
    # (inclusive Médico 40h) como 1 processo seletivo próprio.
    html = _ler_fixture("cajati_categoria38_2026-processo-seletivo_listagem.html")
    itens = kingpage.listar_processos_categoria(html, "https://www.cajati.sp.gov.br")

    assert len(itens) == 11
    medico = next(i for i in itens if i.processo_id == 1766)
    assert "MÉDICO" in medico.objeto.upper()

    objetos = {i.objeto.upper() for i in itens}
    # outros cargos de saúde do mesmo lote também precisam estar presentes
    assert any("AGENTE COMUNITARIO DE SAÚDE" in o for o in objetos)
    assert any("FISIOTERAPEUTA" in o for o in objetos)
    assert any("TÉCNICO DE ENFERMAGEM" in o for o in objetos)


def test_listar_processos_categoria_sem_tabela_devolve_vazio():
    assert kingpage.listar_processos_categoria("<html>nada aqui</html>", "https://exemplo.sp.gov.br") == []


def test_listar_documentos_extrai_titulo_data_e_url_pdf():
    html = _ler_fixture("cajati_1766_processo-seletivo-001-medico-40h_detalhe.html")
    documentos = kingpage.listar_documentos(html, "https://www.cajati.sp.gov.br")

    assert len(documentos) == 4
    assert documentos[-1].titulo == "EDITAL DE ABERTURA"
    assert documentos[-1].data == date(2026, 2, 11)
    assert documentos[-1].url_pdf == "https://www.cajati.sp.gov.br/concurso/download/3048/"


def test_listar_documentos_sem_tabela_devolve_vazio():
    assert kingpage.listar_documentos("<html>nada aqui</html>", "https://exemplo.sp.gov.br") == []


def test_escolher_edital_medico_cajati_prefere_abertura_sobre_homologacao_e_convocacao():
    html = _ler_fixture("cajati_1766_processo-seletivo-001-medico-40h_detalhe.html")
    documentos = kingpage.listar_documentos(html, "https://www.cajati.sp.gov.br")
    edital = kingpage.escolher_edital(documentos)

    assert edital is not None
    assert edital.titulo == "EDITAL DE ABERTURA"
    assert edital.url_pdf == "https://www.cajati.sp.gov.br/concurso/download/3048/"


def test_escolher_edital_itapetininga_ignora_homologacao_dos_resultados():
    html = _ler_fixture("itapetininga_43_processo-seletivo-simplificado-07-2025_detalhe.html")
    documentos = kingpage.listar_documentos(html, "https://www.itapetininga.sp.gov.br")
    edital = kingpage.escolher_edital(documentos)

    assert edital is not None
    assert edital.titulo == "EDITAL DO PROCESSO SELETIVO SIMPLIFICADO Nº 07/2025"
    assert edital.url_pdf == "https://www.itapetininga.sp.gov.br/concurso/download/727/"


def test_escolher_edital_tupa_ignora_gabarito_homologacao_e_convocacao():
    # achado real: 18 documentos no total, boa parte com "Edital" no
    # título (gabarito preliminar, homologação de inscrição, convocação
    # pra prova) — só o de abertura de verdade tem que ser escolhido.
    html = _ler_fixture("tupa_20_processo-seletivo-002-2024-professores_detalhe.html")
    documentos = kingpage.listar_documentos(html, "https://www.tupa.sp.gov.br")
    assert len(documentos) == 18

    edital = kingpage.escolher_edital(documentos)
    assert edital is not None
    assert edital.titulo == "Edital - 02-2024 - Processo Seletivo para professores"
    assert edital.url_pdf == "https://www.tupa.sp.gov.br/concurso/download/275/"


def test_escolher_edital_lista_vazia_devolve_none():
    assert kingpage.escolher_edital([]) is None


def test_escolher_edital_sem_nenhum_titulo_com_edital_cai_pro_mais_antigo():
    documentos = [
        kingpage.Documento(titulo="Comunicado de Retificação", data=date(2026, 1, 10), url_pdf="https://x/2"),
        kingpage.Documento(titulo="Comunicado Original", data=date(2026, 1, 1), url_pdf="https://x/1"),
    ]
    assert kingpage.escolher_edital(documentos).url_pdf == "https://x/1"


def test_categorias_do_municipio_inclui_padrao_e_extras_de_cajati():
    assert kingpage.categorias_do_municipio("Itapetininga") == kingpage.CATEGORIAS_PADRAO
    categorias_cajati = kingpage.categorias_do_municipio("Cajati")
    assert (24, "processo-seletivo") in categorias_cajati
    assert (25, "concurso") in categorias_cajati
    assert (38, "2026-processo-seletivo") in categorias_cajati


def test_listar_municipios_kingpage_le_csv_real():
    municipios = kingpage.listar_municipios_kingpage()
    assert len(municipios) == 3
    assert all(m.uf == "SP" for m in municipios)
    nomes = {m.nome for m in municipios}
    assert nomes == {"Itapetininga", "Tupã", "Cajati"}
