import json
from pathlib import Path

from notifica_vagas_scraper.fontes import instar

FIXTURES = Path(__file__).parent / "fixtures" / "instar"


def _carregar_fixture(nome: str) -> dict:
    with (FIXTURES / nome).open(encoding="utf-8") as f:
        return json.load(f)


def test_listar_municipios_instar_le_csv_real():
    municipios = instar.listar_municipios_instar()
    assert len(municipios) > 0
    assert all(m.uf in ("MG", "SP") for m in municipios)
    assert all(m.url_prefeitura.startswith("http") for m in municipios)


def test_url_dados_abertos():
    assert (
        instar.url_dados_abertos("https://www.araujos.mg.gov.br/", 2026)
        == "https://www.araujos.mg.gov.br/portal/dados-abertos/concursos/2026"
    )
    assert (
        instar.url_dados_abertos("https://www.araujos.mg.gov.br", 2026)
        == "https://www.araujos.mg.gov.br/portal/dados-abertos/concursos/2026"
    )


def test_listar_itens_abertos_filtra_situacao():
    payload = _carregar_fixture("araujos_mg_2026.json")
    abertos = instar.listar_itens_abertos(payload)
    assert len(abertos) > 0
    assert all(item["situacao"].lower() == "aberto" for item in abertos)
    # Achado real: item "Aberto" mas que não é vaga de verdade (eleição de
    # conselho tutelar) — a filtragem por situacao não resolve isso sozinha,
    # fica pro Gemini (gemini_texto.py) devolver vagas: [] pra esse caso.
    titulos = [item["titulo"] for item in abertos]
    assert any("CONSELHO TUTELAR" in t.upper() for t in titulos)


def test_listar_itens_abertos_sem_registro_nao_quebra():
    payload = _carregar_fixture("barbacena_mg_2026_sem_registro.json")
    assert instar.listar_itens_abertos(payload) == []


def test_listar_itens_abertos_ignora_concluido():
    payload = _carregar_fixture("buritis_mg_2026.json")
    abertos = instar.listar_itens_abertos(payload)
    assert all(item["situacao"].lower() != "concluído" for item in abertos)


def _carregar_html(nome: str) -> str:
    with (FIXTURES / nome).open(encoding="utf-8") as f:
        return f.read()


def test_url_editais_e_noticias():
    assert instar.url_editais("https://www.confins.mg.gov.br/") == "https://www.confins.mg.gov.br/portal/editais"
    assert instar.url_noticias("https://www.confins.mg.gov.br") == "https://www.confins.mg.gov.br/portal/noticias"


def test_listar_categorias_editais_extrai_ids_e_nomes_do_menu():
    # Achado real 2026-09-10: `/portal/editais` sozinho sempre devolve 0
    # itens — precisa escolher uma categoria do menu (aqui: Editais de
    # Licitação, Editais de Concursos, Compra Direta, Chamamento Público).
    html = _carregar_html("confins_mg_editais_base.html")
    categorias = instar.listar_categorias_editais(html)
    assert [(c.id, c.nome) for c in categorias] == [
        ("1", "Editais de Licitação"),
        ("3", "Editais de Concursos"),
        ("4", "Compra Direta"),
        ("5", "Chamamento Público"),
    ]


def test_listar_itens_portal_editais_categoria_chamamento_publico():
    # Mesma classe de bug que fez o endpoint de dados abertos perder a
    # vaga de Sapucaí-Mirim (categoria "Chamamento Público" separada de
    # "Concursos") — confirma que a categoria 5 (chamamento) também é
    # legível pelo mesmo parser de listagem.
    html = _carregar_html("confins_mg_editais_chamamento.html")
    itens = instar.listar_itens_portal(html, "https://www.confins.mg.gov.br")
    assert any("CHAMAMENTO PÚBLICO" in item.titulo.upper() and item.situacao == "Aberto" for item in itens)


def test_listar_itens_portal_editais_extrai_titulo_descricao_e_situacao():
    html = _carregar_html("confins_mg_editais.html")
    itens = instar.listar_itens_portal(html, "https://www.confins.mg.gov.br")
    assert len(itens) > 0
    assert all(item.url.startswith("https://www.confins.mg.gov.br/portal/editais/") for item in itens)

    # Achado real 2026-09-05 que motivou a 2ª camada: edital de PSS de
    # Agente Comunitário de Saúde, visível só na listagem HTML (o endpoint
    # de dados abertos de Confins nunca teve nenhum registro).
    saude = next(item for item in itens if "AGENTE COMUNITÁRIO DE SAÚDE" in item.titulo.upper())
    assert saude.situacao is not None and saude.situacao.strip().lower() == "aberto"


def test_listar_itens_portal_noticias_extrai_titulo_descricao_sem_situacao():
    html = _carregar_html("confins_mg_noticias.html")
    itens = instar.listar_itens_portal(html, "https://www.confins.mg.gov.br")
    assert len(itens) > 0

    pss = next(item for item in itens if "PROCESSO SELETIVO SIMPLIFICADO" in item.titulo.upper())
    assert pss.situacao is None
    assert "saúde" in pss.descricao.lower()
