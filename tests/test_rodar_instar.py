from pathlib import Path

import rodar_instar as script
from notifica_vagas_scraper.fontes import instar

FIXTURES = Path(__file__).parent / "fixtures" / "instar"


def test_tem_sinal_saude_detecta_medico_no_titulo_ou_descricao():
    assert script._tem_sinal_saude([{"titulo": "PROCESSO SELETIVO 05/2026 (MÉDICO)", "descricao": ""}])
    assert script._tem_sinal_saude(
        [{"titulo": "Processo Seletivo", "descricao": "contratação temporária de médico pediatra"}]
    )


def test_tem_sinal_saude_devolve_false_sem_palavra_de_saude():
    assert not script._tem_sinal_saude([{"titulo": "Motorista Categoria D", "descricao": "vaga de motorista"}])


def test_tem_sinal_saude_lista_vazia_devolve_false():
    assert not script._tem_sinal_saude([])


def _item(titulo: str, descricao: str = "", situacao: str | None = None, url: str = "https://x/portal/editais/0/3/1/") -> instar.ItemPortal:
    return instar.ItemPortal(titulo=titulo, descricao=descricao, url=url, situacao=situacao)


def test_tem_palavra_gatilho_detecta_concurso_pss_credenciamento():
    assert script._tem_palavra_gatilho(_item("PSS 003/2026"))
    assert script._tem_palavra_gatilho(_item("Aviso qualquer", descricao="abertura de credenciamento de médicos"))


def test_tem_palavra_gatilho_devolve_false_sem_termo_de_concurso():
    assert not script._tem_palavra_gatilho(_item("Campanha de vacinação", descricao="dia D contra o Aedes aegypti"))
    # Achado real 2026-09-10: "edital" sozinho não é gatilho (toda
    # listagem de /portal/editais tem "edital" no título, licitação
    # incluída) — sem isso a 2ª camada estoura cota do Gemini à toa.
    assert not script._tem_palavra_gatilho(
        _item("EDITAL PREGÃO ELETRÔNICO Nº 014/2026 - EXECUÇÃO DE SERVIÇOS ESF")
    )


def test_categoria_relevante_aceita_concurso_e_chamamento_rejeita_licitacao():
    assert script._categoria_relevante(instar.CategoriaEditais(id="3", nome="Editais de Concursos"))
    assert script._categoria_relevante(instar.CategoriaEditais(id="5", nome="Chamamento Público"))
    assert not script._categoria_relevante(instar.CategoriaEditais(id="1", nome="Editais de Licitação"))
    assert not script._categoria_relevante(instar.CategoriaEditais(id="4", nome="Compra Direta"))


def test_extrair_id_portal_pega_id_numerico_da_url():
    assert script._extrair_id_portal("https://x.gov.br/portal/editais/0/3/335/edital-agente-saude/") == "335"
    assert script._extrair_id_portal("https://x.gov.br/portal/noticias/0/3/467/processo-seletivo/") == "467"


def test_extrair_id_portal_sem_match_cai_pro_slug():
    assert script._extrair_id_portal("https://x.gov.br/algo/inesperado") == "https-x-gov-br-algo-inesperado"


_PAGINAS_INSTAR_FAKE = {
    "https://www.confins.mg.gov.br/portal/editais": "confins_mg_editais_base.html",
    "https://www.confins.mg.gov.br/portal/editais/1": "confins_mg_editais_base.html",  # categoria sem fixture própria, reaproveita a base (0 itens)
    "https://www.confins.mg.gov.br/portal/editais/3": "confins_mg_editais.html",
    "https://www.confins.mg.gov.br/portal/editais/4": "confins_mg_editais_base.html",
    "https://www.confins.mg.gov.br/portal/editais/5": "confins_mg_editais_chamamento.html",
    "https://www.confins.mg.gov.br/portal/noticias": "confins_mg_noticias.html",
}


def _buscar_html_fake(url: str) -> str:
    return (FIXTURES / _PAGINAS_INSTAR_FAKE[url]).read_text(encoding="utf-8")


def test_urls_listagem_editais_descobre_categorias_do_menu(monkeypatch):
    monkeypatch.setattr(script, "buscar_html", _buscar_html_fake)
    urls = script._urls_listagem_editais("https://www.confins.mg.gov.br")
    # Só as categorias relevantes pra concurso público (Concursos,
    # Chamamento Público) — Licitações e Compra Direta ficam de fora
    # (ver test_categoria_relevante_aceita_concurso_e_chamamento_rejeita_licitacao).
    assert urls == [
        "https://www.confins.mg.gov.br/portal/editais/3",
        "https://www.confins.mg.gov.br/portal/editais/5",
    ]


def test_buscar_itens_layer2_filtra_por_palavra_chave_e_dedup_contra_layer1(monkeypatch):
    monkeypatch.setattr(script, "buscar_html", _buscar_html_fake)

    # Sem sobreposição com layer1: acha os itens reais de concurso/PSS nas
    # listagens de editais (categoria "Concursos" e "Chamamento Público" —
    # achado real 2026-09-10, mesma classe de bug do Sapucaí-Mirim) e de
    # notícias (achado real do Confins que motivou a 2ª camada).
    itens = script.buscar_itens_layer2("https://www.confins.mg.gov.br", itens_layer1=[])
    assert any("AGENTE COMUNITÁRIO DE SAÚDE" in item.titulo.upper() for item in itens)
    assert any("CHAMAMENTO PÚBLICO Nº 006/2025" in item.titulo.upper() for item in itens)
    assert any("PROCESSO SELETIVO SIMPLIFICADO" in item.titulo.upper() for item in itens)
    assert not any("VACINAÇÃO" in item.titulo.upper() for item in itens)

    # Título já visto na 1ª camada não repete na 2ª (dedup).
    itens_com_dedup = script.buscar_itens_layer2(
        "https://www.confins.mg.gov.br",
        itens_layer1=[{"titulo": "EDITAL 03/2022 PROCESSO SELETIVO AGENTE COMUNITÁRIO DE SAÚDE", "descricao": ""}],
    )
    assert not any("AGENTE COMUNITÁRIO DE SAÚDE" in item.titulo.upper() for item in itens_com_dedup)
