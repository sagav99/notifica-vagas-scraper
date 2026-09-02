from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import access

FIXTURES = Path(__file__).parent / "fixtures" / "access"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_extrair_municipio_uf_prefeitura_municipal():
    assert access.extrair_municipio_uf("Prefeitura Municipal de Birigui/SP") == (
        "Prefeitura Municipal",
        "Birigui",
        "SP",
    )


def test_extrair_municipio_uf_preserva_de_no_nome_do_municipio():
    # achado real: "Engenheiro Paulo de Frontin" tem " de " no meio do
    # próprio nome — só o prefixo de entidade pode ser removido, não
    # qualquer "de" na string.
    assert access.extrair_municipio_uf("Prefeitura Municipal de Engenheiro Paulo de Frontin/RJ") == (
        "Prefeitura Municipal",
        "Engenheiro Paulo de Frontin",
        "RJ",
    )


def test_extrair_municipio_uf_camara():
    assert access.extrair_municipio_uf("Câmara Municipal de Ipatinga/MG") == (
        "Câmara Municipal",
        "Ipatinga",
        "MG",
    )


def test_extrair_municipio_uf_devolve_none_pra_entidade_nao_mapeavel():
    assert access.extrair_municipio_uf("Universidade Federal do Pampa - Unipampa - Nível D") is None


def test_listar_processos_abertos_extrai_orgao_municipio_uf_tipo_e_edital():
    itens = access.listar_processos_abertos(_ler_fixture("abertos.html"))

    assert len(itens) == 3
    birigui = next(i for i in itens if i.processo_id == 183)
    assert birigui.url == "https://concursos.access.org.br/informacoes/183/"
    assert birigui.orgao == "Prefeitura Municipal"
    assert birigui.municipio == "Birigui"
    assert birigui.uf == "SP"
    assert birigui.tipo_processo == "Concurso Público"
    assert birigui.numero_edital == "179/2026"


def test_listar_processos_abertos_municipio_com_de_no_nome():
    itens = access.listar_processos_abertos(_ler_fixture("abertos.html"))
    frontin = next(i for i in itens if i.processo_id == 182)
    assert frontin.municipio == "Engenheiro Paulo de Frontin"
    assert frontin.uf == "RJ"


def test_listar_documentos_e_escolher_edital_reaproveitam_proseleta():
    documentos = access.listar_documentos(_ler_fixture("detalhe_birigui.html"))
    assert len(documentos) == 1
    assert documentos[0].titulo == "EDITAL DE ABERTURA"
    assert documentos[0].data == date(2026, 8, 24)

    edital = access.escolher_edital(documentos)
    assert edital is not None
    assert edital.titulo == "EDITAL DE ABERTURA"


def test_listar_vagas_html_reaproveita_proseleta():
    vagas = access.listar_vagas_html(_ler_fixture("detalhe_birigui.html"))
    assert len(vagas) == 1
    assert vagas[0].cargo == "Professor Auxiliar"
    assert vagas[0].quantidade == 10
