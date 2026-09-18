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


def test_encontrar_municipio_rejeita_referencia_a_estado_com_de():
    # achado real rodando contra produção: "Secretaria da Educação do
    # Estado de São Paulo" é o ESTADO, não o município capital — a
    # preposição antes do nome do estado é "de" aqui, não "do/da".
    municipios = [("São Paulo", "SP")]
    titulo = "Processo Seletivo Simplificado para a Secretaria da Educação do Estado de São Paulo"
    assert fgv.encontrar_municipio(titulo, municipios) is None


def test_encontrar_municipio_rejeita_prefixo_de_nome_maior():
    # achado real rodando contra produção: "São Lourenço" (MG) batia dentro
    # de "São Lourenço da Mata" (PE, fora da nossa lista de MG/SP).
    municipios = [("São Lourenço", "MG")]
    titulo = "Concurso Público para o Município de São Lourenço da Mata"
    assert fgv.encontrar_municipio(titulo, municipios) is None


def test_encontrar_municipio_sem_match_retorna_none():
    municipios = [("Governador Valadares", "MG")]
    match = fgv.encontrar_municipio("Concurso Público para o Tribunal de Justiça de Pernambuco", municipios)
    assert match is None


def test_encontrar_municipio_rejeita_nome_de_instituicao_organizadora():
    # achado real do Vigia/Serper em produção (2026-09-11, TAREFAS.md):
    # banca "Fundação Carlos Chagas" (Barueri/SP) casou por engano com o
    # município "Carlos Chagas/MG" — 49 vagas gravadas com município errado.
    municipios = [("Carlos Chagas", "MG")]
    titulo = "Prefeitura de Barueri abre concurso via Fundação Carlos Chagas"
    assert fgv.encontrar_municipio(titulo, municipios) is None


def test_encontrar_municipio_ainda_casa_titulo_legitimo_com_banca_no_meio():
    # a guarda não pode virar falso negativo generalizado: título real de
    # concurso legítimo, com nome de banca em outra parte do texto, sem
    # relação direta com o nome do município casado, continua batendo.
    municipios = [("Governador Valadares", "MG")]
    titulo = "Instituto AOCP organiza concurso da Prefeitura de Governador Valadares"
    assert fgv.encontrar_municipio(titulo, municipios) == ("Governador Valadares", "MG")


def test_encontrar_municipio_rejeita_match_no_meio_de_palavra_sem_borda():
    # achado real 2026-09-18 (auditoria de revisão Gemini): "Arandu" (SP)
    # batia dentro de "Massaranduba" (SC) sem espaço nem qualquer borda de
    # palavra entre os dois — 4 vagas médicas reais de Massaranduba/SC
    # gravadas com município errado (Arandu/SP).
    municipios = [("Arandu", "SP")]
    titulo = "Prefeitura de Massaranduba abre concurso para médicos"
    assert fgv.encontrar_municipio(titulo, municipios) is None


def test_extrair_uf_do_link_site_oficial_prefeitura():
    assert fgv.extrair_uf_do_link("https://www.cruzmachado.pr.gov.br/editais") == "PR"
    assert fgv.extrair_uf_do_link("https://qconcursos.com/algum/pdf") is None


def test_casar_municipio_com_guarda_de_uf_rejeita_quando_link_diverge():
    # achado real 2026-09-18: título sem marcador "- UF" casava "Machado"
    # (MG) num edital de Cruz Machado/PR de verdade — o link oficial já
    # denunciava o UF certo. 5 vagas médicas reais gravadas com município
    # errado antes desta guarda existir.
    municipios = [("Machado", "MG")]
    titulo = "Prefeitura Municipal de Cruz Machado abre concurso para médicos"
    match = fgv.casar_municipio_com_guarda_de_uf(
        titulo, "", municipios, link="https://www.cruzmachado.pr.gov.br/editais"
    )
    assert match is None


def test_casar_municipio_com_guarda_de_uf_aceita_quando_link_confirma_uf():
    municipios = [("Governador Valadares", "MG")]
    titulo = "Prefeitura de Governador Valadares abre concurso para médicos"
    match = fgv.casar_municipio_com_guarda_de_uf(
        titulo, "", municipios, link="https://www.valadares.mg.gov.br/editais"
    )
    assert match == ("Governador Valadares", "MG")
