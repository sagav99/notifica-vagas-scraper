from pathlib import Path

import pdfplumber

from notifica_vagas_scraper.fontes import instituto_mais

FIXTURES = Path(__file__).parent / "fixtures" / "instituto_mais"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


# --- listar_concursos (/Concursos/ConcursosAbertos) ------------------------


def test_listar_concursos_conta_itens_por_secao():
    itens = instituto_mais.listar_concursos(_ler_fixture("institutomais_concursos_abertos.html"))

    proximos = [i for i in itens if i.status == "proximo"]
    abertos = [i for i in itens if i.status == "aberta"]
    andamento = [i for i in itens if i.status == "andamento"]

    assert len(proximos) == 2
    assert len(abertos) == 4
    assert len(andamento) == 12


def test_listar_concursos_acha_itapeva_medico_psiquiatra_em_andamento():
    itens = instituto_mais.listar_concursos(_ler_fixture("institutomais_concursos_abertos.html"))
    itapeva = next(i for i in itens if i.concurso_id == 10637)

    assert itapeva.status == "andamento"
    assert itapeva.url == "https://institutomais.org.br/Concursos/Detalhe/10637"
    assert "Itapeva" in itapeva.titulo


def test_listar_concursos_acha_santa_casa_residencia_medica():
    itens = instituto_mais.listar_concursos(_ler_fixture("institutomais_concursos_abertos.html"))
    santa_casa = next(i for i in itens if i.concurso_id == 10643)

    assert "Resid" in santa_casa.titulo
    assert "Rio Preto" in santa_casa.titulo


def test_listar_concursos_nao_perde_nenhum_dos_18_itens_da_fixture():
    # achado de code review: 2 <a> por linha (um só com <img>, sem texto) —
    # confirma que nenhuma linha é contada 2x nem perdida.
    itens = instituto_mais.listar_concursos(_ler_fixture("institutomais_concursos_abertos.html"))
    ids = {i.concurso_id for i in itens}
    assert len(itens) == 18
    assert len(ids) == 18


def test_listar_concursos_pagina_sem_secoes_devolve_lista_vazia():
    assert instituto_mais.listar_concursos("<html><body>nada aqui</body></html>") == []


# --- listar_quadro_vagas (Tabela "Quadro de Vagas") -------------------------


def test_listar_quadro_vagas_itapeva_nao_descarta_o_medico_psiquiatra():
    # PRIORIDADE #1 do produto (ver CLAUDE.md): confirma que o cargo 302 —
    # Médico Psiquiatra aparece, junto de todos os outros 8 cargos do
    # Quadro de Vagas de Itapeva (nenhum filtro por nome).
    vagas = instituto_mais.listar_quadro_vagas(
        _ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")
    )
    assert len(vagas) == 9

    medico = next(v for v in vagas if v.codigo == "302")
    assert medico.cargo == "MÉDICO PSIQUIATRA"
    assert medico.vagas == 2
    assert medico.requisitos is not None
    assert "Medicina" in medico.requisitos
    assert "Especialidade" in medico.requisitos


def test_listar_quadro_vagas_itapeva_mantem_todos_os_codigos():
    vagas = instituto_mais.listar_quadro_vagas(
        _ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")
    )
    codigos = {v.codigo for v in vagas}
    assert codigos == {"101", "102", "103", "201", "202", "203", "301", "302", "303"}


def test_listar_quadro_vagas_santa_casa_residencia_medica_2_especialidades():
    vagas = instituto_mais.listar_quadro_vagas(
        _ler_fixture("institutomais_detalhe_santacasa_sjrp_residencia_medica_10643.html")
    )
    assert len(vagas) == 2

    cirurgia_geral = next(v for v in vagas if v.codigo == "309")
    assert cirurgia_geral.cargo == "Cirurgia Geral"
    assert cirurgia_geral.vagas == 3

    cirurgia_oncologica = next(v for v in vagas if v.codigo == "405")
    assert cirurgia_oncologica.cargo == "Cirurgia Oncológica"
    assert cirurgia_oncologica.vagas == 1
    assert "Cirurgia Geral" in cirurgia_oncologica.requisitos


def test_listar_quadro_vagas_pagina_sem_a_secao_devolve_lista_vazia():
    assert instituto_mais.listar_quadro_vagas("<html><body>sem quadro aqui</body></html>") == []


# --- listar_documentos (EDITAIS E COMUNICADOS, plataforma antiga) ----------


def test_listar_documentos_itapeva_acha_todos_os_links_pdf():
    documentos = instituto_mais.listar_documentos(
        _ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")
    )
    # 28 parágrafos com link .pdf na seção "EDITAIS E COMUNICADOS" da fixture.
    assert len(documentos) == 28


def test_listar_documentos_itapeva_extrai_titulo_sem_clique_aqui():
    documentos = instituto_mais.listar_documentos(
        _ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")
    )
    edital = next(d for d in documentos if d.titulo.upper().startswith("EDITAL DO CONCURSO"))
    assert "Clique aqui" not in edital.titulo
    assert "01/2026" in edital.titulo
    assert edital.url_pdf == (
        "https://institutomais.org.br/ckfinder/userfiles/files/"
        "PM%20Itapeva_CP%2001-26-03-26_IMAIS_definitivo(5).pdf"
    )


def test_listar_documentos_pagina_sem_secao_devolve_lista_vazia():
    assert instituto_mais.listar_documentos("<html><body>sem editais aqui</body></html>") == []


# --- listar_documentos_novo (Arquivos, plataforma nova/Blazor) --------------


def test_listar_documentos_novo_acha_o_pdf_no_azure_blob():
    documentos = instituto_mais.listar_documentos_novo(
        _ler_fixture("institutomais_plataforma_nova_itapira_detalhe62_com_injecao_spam.html")
    )
    assert len(documentos) == 1
    assert "imais2023.blob.core.windows.net" in documentos[0].url_pdf
    assert documentos[0].titulo.upper().startswith("EDITAL")


def test_listar_documentos_novo_ignora_script_de_spam_injetado():
    # achado de segurança da investigação: script injetado cria 3 <a>
    # invisíveis via JS (não existem no HTML estático) — confirma que eles
    # não aparecem no resultado (não seguimos link nenhum criado por JS).
    documentos = instituto_mais.listar_documentos_novo(
        _ler_fixture("institutomais_plataforma_nova_itapira_detalhe62_com_injecao_spam.html")
    )
    assert not any("gozy" in d.url_pdf.lower() for d in documentos)


# --- escolher_edital (compartilhado pelas 2 plataformas) --------------------


def test_escolher_edital_itapeva_ignora_editais_de_convocacao_e_comunicados():
    documentos = instituto_mais.listar_documentos(
        _ler_fixture("institutomais_detalhe_itapeva_10637_medico_psiquiatra.html")
    )
    edital = instituto_mais.escolher_edital(documentos)
    assert edital is not None
    assert edital.titulo.upper().startswith("EDITAL DO CONCURSO")
    assert "CONVOCAÇÃO" not in edital.titulo.upper()


def test_escolher_edital_nao_confunde_mencao_secundaria_a_edital_no_meio_do_texto():
    # achado real: um comunicado menciona "...do Edital no 01/2026..." no
    # meio do texto sem ser o documento certo — checagem por PREFIXO (não
    # substring) evita escolher esse comunicado por engano.
    documentos = [
        instituto_mais.Documento(
            titulo="RESULTADO PROVISÓRIO DA PROVA PRÁTICA (conforme o Edital no 01/2026, ...)",
            url_pdf="https://x/resultado.pdf",
        ),
        instituto_mais.Documento(titulo="EDITAL DO CONCURSO PÚBLICO - Nº 01/2026", url_pdf="https://x/edital.pdf"),
    ]
    edital = instituto_mais.escolher_edital(documentos)
    assert edital is not None
    assert edital.url_pdf == "https://x/edital.pdf"


def test_escolher_edital_santa_casa_acha_o_edital_retificado_unico():
    # achado real: só o edital JÁ retificado foi publicado (sem original
    # avulso) — ainda tem que ser escolhido normalmente.
    documentos = instituto_mais.listar_documentos(
        _ler_fixture("institutomais_detalhe_santacasa_sjrp_residencia_medica_10643.html")
    )
    edital = instituto_mais.escolher_edital(documentos)
    assert edital is not None
    assert "Retificado" in edital.titulo


def test_escolher_edital_lista_vazia_devolve_none():
    assert instituto_mais.escolher_edital([]) is None


def test_escolher_edital_sem_candidato_edital_cai_pro_primeiro_documento():
    documentos = [instituto_mais.Documento(titulo="COMUNICADO Nº 01", url_pdf="https://x/1.pdf")]
    assert instituto_mais.escolher_edital(documentos) == documentos[0]


# --- extrair_numero_edital ---------------------------------------------


def test_extrair_numero_edital_com_a_palavra_edital():
    assert (
        instituto_mais.extrair_numero_edital(
            "Prefeitura Municipal de Santa Gertrudes / SP - Processo Seletivo Simplificado - Edital nº 01/2026"
        )
        == "01/2026"
    )


def test_extrair_numero_edital_sem_a_palavra_edital_programa_residencia():
    assert (
        instituto_mais.extrair_numero_edital(
            "Irmandade da Santa Casa de Misericórdia de São José do Rio Preto / SP - "
            "Programa de Residência Médica - P. S. nº 02/2026"
        )
        == "02/2026"
    )


def test_extrair_numero_edital_sem_a_palavra_edital_francisco_morato():
    assert (
        instituto_mais.extrair_numero_edital("Prefeitura de Francisco Morato - Concurso Público 04/2025 - Procurador Jurídico")
        == "04/2025"
    )


def test_extrair_numero_edital_none_sem_padrao():
    assert instituto_mais.extrair_numero_edital("texto qualquer sem número nenhum") is None


# --- normalizar_cargo / identificador_externo -------------------------


def test_normalizar_cargo_ignora_acento_e_maiuscula():
    assert instituto_mais.normalizar_cargo("MÉDICO PSIQUIATRA") == instituto_mais.normalizar_cargo("Médico Psiquiatra")


def test_identificador_externo_estavel_e_com_slug_sem_acento():
    ident = instituto_mais.identificador_externo(10637, "MÉDICO PSIQUIATRA")
    assert ident == "instituto-mais-10637-medico-psiquiatra"


# --- edital mais denso das fixtures: Jarinu CP 02/2025 "Saúde" ------------
#
# Não há fixture de página de detalhe (HTML "Quadro de Vagas") pra Jarinu —
# só o PDF real do edital foi capturado na investigação (ver TAREFAS.md
# pendência ao final desta tarefa). Testamos aqui, direto contra o PDF real
# (sem chamar o Gemini real), que as 16 especialidades médicas distintas
# documentadas na investigação (`docs/investigacao_fonte_instituto_mais_
# 2026-09-05.md`) realmente aparecem no texto extraído do PDF — confirma
# que a camada que o Gemini lê (texto real, não escaneado) não está
# truncada/corrompida antes mesmo de chegar no Gemini. PRIORIDADE #1 do
# produto (CLAUDE.md): nenhuma especialidade médica pode ser perdida.


def test_pdf_jarinu_edital_mais_denso_no_descarta_nenhuma_das_16_especialidades_medicas():
    caminho_pdf = FIXTURES / "edital_jarinu_cp02_2025_saude_multiplos_medicos.pdf"
    with pdfplumber.open(caminho_pdf) as pdf:
        texto = "\n".join((pagina.extract_text() or "") for pagina in pdf.pages).upper()

    especialidades_medicas = [
        "MÉDICO AUDITOR",
        "CARDIOLOGISTA",
        "CLÍNICO GERAL",
        "DERMATOLOGISTA",
        "ESPECIALISTA",
        "GENERALISTA",
        "GINECOLOGISTA",
        "HEBIATRA",
        "INFECTOLOGISTA",
        "NEUROLOGISTA",
        "OFTALMOLOGISTA",
        "ORTOPEDISTA",
        "PEDIATRA",
        "PSIQUIATRA",
    ]
    faltando = [nome for nome in especialidades_medicas if nome not in texto]
    assert faltando == []
