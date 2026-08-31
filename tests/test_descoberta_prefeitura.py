from notifica_vagas_scraper.descoberta_prefeitura import (
    _pagina_parece_da_prefeitura,
    candidatos_url,
    gerar_slugs,
)


def test_gerar_slugs_nome_simples():
    assert gerar_slugs("Alfenas") == ["alfenas"]


def test_gerar_slugs_com_preposicao_gera_variacoes():
    slugs = gerar_slugs("Conceição da Barra de Minas")
    assert "conceicaodabarrademinas" in slugs  # sem hífen, com preposição (achado real)
    assert "conceicao-da-barra-de-minas" in slugs
    assert "conceicao-barra-minas" in slugs  # sem preposição
    assert "conceicaobarraminas" in slugs


def test_candidatos_url_combina_slug_www_e_uf():
    candidatos = candidatos_url("Alfenas", "MG")
    assert "https://www.alfenas.mg.gov.br" in candidatos
    assert "https://alfenas.mg.gov.br" in candidatos
    assert all(c.endswith(".mg.gov.br") for c in candidatos)


def test_pagina_valida_confirma_prefeitura_e_nome_cidade():
    html = "<html><body>Prefeitura Municipal de Alfenas</body></html>"
    assert _pagina_parece_da_prefeitura(html, "Alfenas") is True


def test_pagina_sem_nome_da_cidade_nao_confirma():
    html = "<html><body>Prefeitura Municipal de Outra Cidade</body></html>"
    assert _pagina_parece_da_prefeitura(html, "Alfenas") is False


def test_pagina_site_nao_configurado_nao_confirma():
    # Achado real: domínio .gov.br registrado mas sem site publicado —
    # não pode contar como URL de prefeitura válida.
    html = "<H3>Site não configurado</H3><P>O domínio está reservado.</P>"
    assert _pagina_parece_da_prefeitura(html, "Conceição da Barra de Minas") is False
