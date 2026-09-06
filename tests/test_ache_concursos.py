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


def test_listar_concursos_agrega_as_duas_tabelas_mg():
    """Regressão do bug 2026-09-06: a página de listagem por UF tem DUAS
    `<table class="tbl-conc">` — "Concursos abertos em MG" (1ª) e
    "Concursos em andamento em MG" (2ª). Um `find` singular pegava só a
    1ª e perdia item real da 2ª silenciosamente (Congonhal/MG, com
    Médico ESF + Médico CAPS + Médico Psiquiatra no edital 001/2026)."""
    itens = ache.listar_concursos(_ler_fixture("listagem_mg_duas_tabelas.html"))

    # 91 itens na 1ª tabela + 184 na 2ª (contagem manual na fixture real).
    assert len(itens) == 91 + 184

    titulos = [item.titulo for item in itens]

    congonhal = [t for t in titulos if "Congonhal" in t]
    assert congonhal, "item de Congonhal/MG (só existe na 2ª tabela) não pode sumir"

    # Outros itens da 2ª tabela cujo próprio título já denuncia vaga
    # médica — confirma que nenhuma vaga de médico da 2ª tabela é
    # descartada pelo agrupamento (a extração de cargo em si é feita
    # depois pelo Gemini, mas o ITEM/edital tem que chegar até lá).
    assert any("Médico" in t for t in titulos), "nenhum item com 'Médico' no título sobrou — 2ª tabela não foi lida"
    assert any("IFSULDEMINAS" in t and "Médicos" in t for t in titulos)


def test_listar_concursos_agrega_as_duas_tabelas_sp():
    """Mesmo bug, fonte SP: Itanhaém-SP tem 2 editais na 1ª tabela e o de
    Agente Comunitário de Saúde (ACS) só na 2ª — os três precisam
    aparecer juntos no retorno."""
    itens = ache.listar_concursos(_ler_fixture("listagem_sp_duas_tabelas.html"))

    assert len(itens) == 95 + 318

    titulos_itanhaem = [item.titulo for item in itens if "Itanha" in item.titulo]
    assert len(titulos_itanhaem) == 5

    acs = [t for t in titulos_itanhaem if "Agente de Saúde" in t or "Agente Comunit" in t]
    assert acs, "edital de ACS de Itanhaém/SP (só existe na 2ª tabela) não pode sumir"


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
