from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import inepam

FIXTURES = Path(__file__).parent / "fixtures" / "inepam"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


# --- extrair_municipio_uf ----------------------------------------------


def test_extrair_municipio_uf_separador_espaco_hifen_espaco():
    assert inepam.extrair_municipio_uf(
        "Anhembi - SP - Prefeitura Municipal - Concurso Público Nº 001/2026 "
    ) == ("Anhembi", "SP", "Prefeitura Municipal")


def test_extrair_municipio_uf_hifen_colado_sem_espaco():
    # achado real: "Lambari-MG" sem espaço nenhum ao redor do hífen.
    assert inepam.extrair_municipio_uf(
        "Lambari-MG - Prefeitura Municipal - Processo Seletivo Nº 001/2025"
    ) == ("Lambari", "MG", "Prefeitura Municipal")


def test_extrair_municipio_uf_hifen_so_com_espaco_depois():
    # achado real: "Angatuba- SP" (espaço só depois do hífen).
    assert inepam.extrair_municipio_uf(
        "Angatuba- SP - Prefeitura Municipal - Concurso Público nº 001/2026"
    ) == ("Angatuba", "SP", "Prefeitura Municipal")


def test_extrair_municipio_uf_separador_barra():
    # achado real: "São Sebastião do Rio Verde/MG" usa "/" em vez de "-".
    assert inepam.extrair_municipio_uf(
        "São Sebastião do Rio Verde/MG - Prefeitura Municipal - Concurso Público nº 001/2026"
    ) == ("São Sebastião do Rio Verde", "MG", "Prefeitura Municipal")


def test_extrair_municipio_uf_camara_municipal():
    assert inepam.extrair_municipio_uf(
        "José Bonifácio - SP - Câmara Municipal - Concurso Público nº 001/2026"
    ) == ("José Bonifácio", "SP", "Câmara Municipal")


def test_extrair_municipio_uf_none_para_consorcio_intermunicipal():
    # não mapeia 1:1 pra um município — não adivinha.
    assert inepam.extrair_municipio_uf(
        "Água Boa - MT - Consórcio Intermunicipal de Saúde do Médio Araguaia/MT - CISMA - Concurso Público Nº 001/2025 "
    ) is None


def test_extrair_municipio_uf_none_para_conselho_regional_sem_municipio():
    # achado de peso: a regex de UF por si só acharia "MT" (código válido)
    # com um "município" bizarro ("Conselho Regional ... - CREA") se não
    # fosse a validação do órgão seguinte ("Concurso Público" não é
    # "Prefeitura"/"Câmara") — confirma que a validação de órgão pega
    # esse falso-positivo.
    assert inepam.extrair_municipio_uf(
        "Conselho Regional de Engenharia e Agronomia do Estado de Mato Grosso - CREA/MT - Concurso Público Nº 001/2025 "
    ) is None


def test_extrair_municipio_uf_none_para_conselho_municipal_nao_prefeitura_camara():
    assert inepam.extrair_municipio_uf(
        "Adamantina-SP - Conselho Municipal dos Direitos da Criança e do Adolescente - Processo Seletivo N° 0001/2023"
    ) is None


# --- extrair_tipo_numero_edital -----------------------------------------


def test_extrair_tipo_numero_edital_concurso_publico():
    assert inepam.extrair_tipo_numero_edital(
        "Anhembi - SP - Prefeitura Municipal - Concurso Público Nº 001/2026 "
    ) == ("Concurso Público", "001/2026")


def test_extrair_tipo_numero_edital_processo_seletivo_publico():
    assert inepam.extrair_tipo_numero_edital(
        "Jenipapo de Minas - MG -  Prefeitura Municipal - Processo Seletivo Público N° 001/2026"
    ) == ("Processo Seletivo Público", "001/2026")


def test_extrair_tipo_numero_edital_processo_seletivo_simplificado():
    assert inepam.extrair_tipo_numero_edital(
        "Pedra Dourada - MG -  Prefeitura Municipal - Processo Seletivo Simplificado Nº 001/2026  "
    ) == ("Processo Seletivo Simplificado", "001/2026")


def test_extrair_tipo_numero_edital_none_sem_padrao():
    assert inepam.extrair_tipo_numero_edital("texto qualquer sem tipo nem número") is None


# --- listar_processos_home (home.do) -------------------------------------


def test_listar_processos_home_conta_itens_por_secao():
    itens = inepam.listar_processos_home(_ler_fixture("home_listagem_concursos.html"))

    abertos = [i for i in itens if i.status == "aberta"]
    andamento = [i for i in itens if i.status == "andamento"]
    finalizados = [i for i in itens if i.status == "finalizado"]

    assert len(abertos) == 11
    assert len(andamento) == 10
    assert len(finalizados) == 10


def test_listar_processos_home_acha_embu_das_artes_processo_seletivo_006():
    itens = inepam.listar_processos_home(_ler_fixture("home_listagem_concursos.html"))
    embu_006 = next(
        i for i in itens if i.status == "aberta" and i.id_instituicao == 14 and i.id_concurso == 27
    )

    assert embu_006.municipio == "Embu das Artes"
    assert embu_006.uf == "SP"
    assert embu_006.orgao == "Prefeitura Municipal"
    assert embu_006.tipo_processo == "Processo Seletivo"
    assert embu_006.numero_edital == "006/2026"
    assert embu_006.url == (
        "https://app.inepam.org.br/concurso/concursoPaginaInterna.do?idInstituicao=14&idConcurso=27"
    )


def test_listar_processos_home_nao_descarta_linha_sem_municipio_reconhecido():
    # a linha ainda entra na lista (com municipio=None) — quem decide
    # pular é o script, não o parser, pra não perder rastro silenciosamente.
    itens = inepam.listar_processos_home(_ler_fixture("home_listagem_concursos.html"))
    sem_municipio = [i for i in itens if i.municipio is None]
    assert len(sem_municipio) >= 1


def test_listar_processos_home_todos_os_6_processos_de_embu_das_artes_sao_lidos():
    # achado real: Embu das Artes tem 5 processos simultâneos em
    # "Inscrições Abertas" (idConcurso 23, 25, 26, 27, 28) — confirma que
    # nenhum é perdido.
    itens = inepam.listar_processos_home(_ler_fixture("home_listagem_concursos.html"))
    embu = [i for i in itens if i.status == "aberta" and i.municipio == "Embu das Artes"]
    ids_concurso = {i.id_concurso for i in embu}
    assert ids_concurso == {23, 25, 26, 27, 28}


# --- listar_processos_pagina (concursosEmAndamento.do) --------------------


def test_listar_processos_pagina_em_andamento_conta_26_itens_sem_perder_nenhum():
    itens = inepam.listar_processos_pagina(_ler_fixture("concursos_em_andamento.html"), status="andamento")
    assert len(itens) == 26


def test_listar_processos_pagina_em_andamento_resolve_municipio_mesmo_sem_data_descricao():
    # esta página NÃO tem o atributo data-descricao (diferente da home) —
    # confirma que o parser lê o <span> de texto, não o atributo.
    itens = inepam.listar_processos_pagina(_ler_fixture("concursos_em_andamento.html"), status="andamento")
    baependi = next(i for i in itens if i.id_instituicao == 43 and i.id_concurso == 3)
    assert baependi.municipio == "Baependi"
    assert baependi.uf == "MG"


def test_listar_processos_pagina_em_andamento_pedra_dourada_3_processos():
    # Pedra Dourada/MG já é fixture de DOM/AMM-MG — overlap conhecido, só
    # confirma aqui que os 3 processos de Pedra Dourada nesta fonte não
    # colidem entre si (ids de concurso distintos).
    itens = inepam.listar_processos_pagina(_ler_fixture("concursos_em_andamento.html"), status="andamento")
    pedra_dourada = [i for i in itens if i.municipio == "Pedra Dourada"]
    assert {i.id_concurso for i in pedra_dourada} == {1, 2, 3}


# --- página do concurso (Embu das Artes, Processo Seletivo 006/2026) ------


def test_listar_documentos_acha_o_edital():
    documentos = inepam.listar_documentos(
        _ler_fixture("embu_das_artes_processo_seletivo_006_2026_pagina.html")
    )
    assert len(documentos) == 1
    assert "Edital" in documentos[0].titulo
    assert documentos[0].url_pdf == "https://app.inepam.org.br/concurso/downloadAnexo.do?idAnexo=2711"


def test_escolher_edital_devolve_o_unico_documento_disponivel():
    documentos = inepam.listar_documentos(
        _ler_fixture("embu_das_artes_processo_seletivo_006_2026_pagina.html")
    )
    edital = inepam.escolher_edital(documentos)
    assert edital is not None
    assert edital.url_pdf == "https://app.inepam.org.br/concurso/downloadAnexo.do?idAnexo=2711"


def test_escolher_edital_lista_vazia_devolve_none():
    assert inepam.escolher_edital([]) is None


def test_listar_funcoes_nao_descarta_nenhuma_das_7_especialidades_medicas():
    # PRIORIDADE #1 do produto (ver CLAUDE.md): confirma que as 7
    # especialidades médicas reais de Embu das Artes (Processo Seletivo
    # 006/2026, 10 vagas) aparecem todas, sem filtro por nome de cargo.
    funcoes = inepam.listar_funcoes(
        _ler_fixture("embu_das_artes_processo_seletivo_006_2026_pagina.html")
    )
    assert funcoes == [
        "Médico Fisiatra",
        "Médico Ginecologista - Obstetra",
        "Médico Neurologista Infantil",
        "Médico Ortopedista",
        "Médico Pneumologista Infantil",
        "Médico Psiquiatra Infantil",
        "Médico Ultrassonografista",
    ]
    assert all("Médico" in f for f in funcoes)


def test_extrair_periodo_inscricao():
    inicio, fim = inepam.extrair_periodo_inscricao(
        _ler_fixture("embu_das_artes_processo_seletivo_006_2026_pagina.html")
    )
    assert inicio == date(2026, 8, 24)
    assert fim == date(2026, 9, 8)


def test_extrair_periodo_inscricao_none_sem_paragrafo():
    assert inepam.extrair_periodo_inscricao("<html><body>sem período aqui</body></html>") == (None, None)
