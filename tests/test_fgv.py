from pathlib import Path

from notifica_vagas_scraper.fontes import fgv

FIXTURES = Path(__file__).parent / "fixtures" / "fgv"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_concursos_encontra_governador_valadares():
    itens = fgv.listar_concursos(_ler_fixture("listagem_page0.html"))

    assert len(itens) == 20
    valadares = next(i for i in itens if "Governador Valadares" in i.titulo)
    assert valadares.url == "https://conhecimento.fgv.br/govvaladares26"


def test_encontrar_pdf_edital_principal_ignora_retificacoes_e_comunicados():
    html = _ler_fixture("govvaladares26.html")
    url = fgv.encontrar_pdf_edital_principal(html)

    assert url is not None
    assert url.endswith(
        "edital-01-2026-abertura-do-concurso-publico-para-a-camara-municipal-de-governador-valadares_retificado_30_07_2026.pdf"
    )


def test_encontrar_municipio_casa_titulo_real():
    municipios = [("Governador Valadares", "MG"), ("Belo Horizonte", "MG")]
    match = fgv.encontrar_municipio(
        "Concurso Público para a Câmara Municipal de Governador Valadares", municipios
    )
    assert match == ("Governador Valadares", "MG")


def test_encontrar_municipio_ignora_nome_curto_generico():
    # achado real na triagem manual: "Tocantins" e "Chácara" batiam por
    # coincidência em títulos sem relação com o município — nomes com menos
    # de 6 caracteres (sem espaço) são ignorados.
    municipios = [("Ubá", "MG")]
    match = fgv.encontrar_municipio("Concurso qualquer sem relação com Ubá no meio", municipios)
    assert match is None


def test_encontrar_municipio_rejeita_referencia_a_estado():
    # achado real rodando contra produção: existe município real "Tocantins"
    # em MG, mas "Secretaria de Estado de Saúde do Tocantins" é o ESTADO.
    municipios = [("Tocantins", "MG")]
    titulo = "Concurso Público para a Secretaria de Estado de Saúde do Tocantins"
    assert fgv.encontrar_municipio(titulo, municipios) is None


def test_encontrar_municipio_sem_match_retorna_none():
    municipios = [("Governador Valadares", "MG")]
    match = fgv.encontrar_municipio("Concurso Público para o Tribunal de Justiça de Pernambuco", municipios)
    assert match is None
