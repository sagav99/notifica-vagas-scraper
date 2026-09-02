from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import ache_concursos as ache

FIXTURES = Path(__file__).parent / "fixtures" / "ache_concursos"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_concursos_extrai_titulo_url_data_e_vagas():
    itens = ache.listar_concursos(_ler_fixture("listagem_mg.html"))
    assert len(itens) == 10

    capinopolis = itens[0]
    assert capinopolis.titulo == "Edital Capinópolis-MG 2026: Câmara Municipal abre cinco vagas de até R$ 5.800"
    assert capinopolis.url.startswith("https://www.acheconcursos.com.br/concursos-minas-gerais/")
    assert capinopolis.inscricoes_fim == date(2026, 10, 16)
    assert capinopolis.quantidade_vagas == 5


def test_listar_concursos_lista_vazia_sem_tabela():
    assert ache.listar_concursos("<html><body>sem tabela aqui</body></html>") == []


def test_extrair_url_pagina_edital_acha_link_anexos():
    url = ache.extrair_url_pagina_edital(_ler_fixture("artigo_anexos.html"))
    assert url == (
        "https://www.acheconcursos.com.br/edital-concurso/"
        "edital-concurso-prefeitura-de-lagoa-da-prata-mg-01-2026"
    )


def test_extrair_url_pagina_edital_sem_anexo_devolve_none():
    assert ache.extrair_url_pagina_edital("<html><body>sem anexo</body></html>") is None


def test_extrair_url_pdf_acha_iframe():
    url = ache.extrair_url_pdf(_ler_fixture("edital_pagina.html"))
    assert url == (
        "https://www.acheconcursos.com.br/imagens/anexo/64179/"
        "edital-concurso-prefeitura-de-lagoa-da-prata-mg-01-2026.pdf"
    )


def test_extrair_url_pdf_sem_iframe_devolve_none():
    assert ache.extrair_url_pdf("<html><body>sem iframe</body></html>") is None
