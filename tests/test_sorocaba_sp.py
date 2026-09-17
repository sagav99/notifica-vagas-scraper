import json
from io import BytesIO
from pathlib import Path

import pdfplumber

from notifica_vagas_scraper.fontes import sorocaba_sp

FIXTURES = Path(__file__).parent / "fixtures" / "sorocaba_sp"


def _ler_json(nome: str):
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def _abrir_pdf_fixture():
    conteudo = (FIXTURES / "edital_abertura_01_2026_saude.pdf").read_bytes()
    return pdfplumber.open(BytesIO(conteudo))


def test_listar_posts_recentes_le_todos_os_posts_da_listagem():
    dados = _ler_json("listagem_recente_wp_json.json")
    posts = sorocaba_sp.listar_posts_recentes(dados)

    assert len(posts) == len(dados)
    assert all(isinstance(p.id, int) for p in posts)


def test_eh_post_candidato_descarta_ruido_do_indice_de_busca():
    # achado real: /wp-json/wp/v2/search tem índice fraco pra esta fonte
    # (api_search_concurso.json) — retorna "Concurso elegerá Miss e
    # Mister..." e convocação de GCM, nenhum dos dois é edital de
    # abertura de concurso público real.
    dados = _ler_json("api_search_concurso.json")
    posts = [
        sorocaba_sp.Post(id=item["id"], titulo=item["title"], slug="", data=None, link=item["url"], conteudo_html="")
        for item in dados
    ]
    assert not any(sorocaba_sp.eh_post_candidato(p) for p in posts)


def test_eh_post_candidato_aceita_titulo_de_concurso_publico():
    post = sorocaba_sp.Post(
        id=1,
        titulo="Prefeitura de Sorocaba abre concurso público para área da saúde",
        slug="prefeitura-abre-concurso-publico-saude",
        data=None,
        link="",
        conteudo_html="",
    )
    assert sorocaba_sp.eh_post_candidato(post)


def test_eh_post_candidato_descarta_titulo_de_resultado_ou_convocacao():
    post = sorocaba_sp.Post(
        id=2,
        titulo="Prefeitura convoca mais aprovados em concurso público da GCM",
        slug="prefeitura-convoca-aprovados-gcm",
        data=None,
        link="",
        conteudo_html="",
    )
    assert not sorocaba_sp.eh_post_candidato(post)


def test_listar_posts_recentes_nao_tem_candidato_por_causa_do_bloqueio_eleitoral():
    # achado real: o post do Concurso 01/2026 está temporariamente fora da
    # listagem recente (bloqueio de "período eleitoral" do WordPress) — a
    # listagem de fevereiro/2026 (quando o post existia) vem vazia hoje, e
    # a listagem recente atual não tem nenhum candidato de concurso de
    # saúde. Não é bug do parser, é limitação temporal documentada.
    dados = _ler_json("listagem_recente_wp_json.json")
    posts = sorocaba_sp.listar_posts_recentes(dados)
    assert not any(sorocaba_sp.eh_post_candidato(p) for p in posts)

    dados_bloqueados = _ler_json("posts_fevereiro_2026_vazio_bloqueado.json")
    assert dados_bloqueados == []


def test_extrair_pdf_do_html_acha_link_direto():
    html = '<p>Edital em anexo: <a href="https://noticias.sorocaba.sp.gov.br/wp-content/uploads/edital.pdf">baixar</a></p>'
    assert sorocaba_sp.extrair_pdf_do_html(html) == "https://noticias.sorocaba.sp.gov.br/wp-content/uploads/edital.pdf"


def test_extrair_pdf_do_html_sem_pdf_devolve_none():
    assert sorocaba_sp.extrair_pdf_do_html("<p>sem anexo aqui</p>") is None


def test_escolher_pdf_de_media_usa_primeiro_anexo_pdf():
    dados = _ler_json("media_exemplo_post_com_pdf.json")
    url = sorocaba_sp.escolher_pdf_de_media(dados)
    assert url == "https://noticias.sorocaba.sp.gov.br/wp-content/uploads/2026/09/Tabela-2a-Semana_19-e-20-de-setembro.pdf"


def test_escolher_pdf_de_media_sem_pdf_devolve_none():
    assert sorocaba_sp.escolher_pdf_de_media([{"mime_type": "image/jpeg", "source_url": "https://x/img.jpg"}]) is None
    assert sorocaba_sp.escolher_pdf_de_media([]) is None


def test_extrair_edital_le_numero_banca_prova_e_curriculo_do_pdf_real():
    with _abrir_pdf_fixture() as pdf:
        texto = "\n".join((p.extract_text() or "") for p in pdf.pages)
        tabelas = [t for p in pdf.pages for t in p.extract_tables()]

    edital = sorocaba_sp.extrair_edital(texto, tabelas)

    assert edital.numero_edital == "01/2026"
    assert edital.banca_organizadora == "Fundação VUNESP"
    assert edital.tem_prova is True
    # achado real: o Edital 01/2026 só cita "Prova Objetiva" (Capítulo
    # VII) pra todos os cargos, sem nenhuma fase de análise de
    # currículo/prova de títulos.
    assert edital.exige_curriculo is False


def test_extrair_cargos_do_pdf_nao_descarta_nenhuma_especialidade_medica():
    # caso mais denso disponível nas fixtures: 1 Técnico de Enfermagem +
    # 6 especialidades médicas (Médico do Trabalho, Nefrologista,
    # Neurologista Adulto, Neurologista Infantil, Psiquiatra Adulto,
    # Psiquiatra Infantil) no mesmo edital, tabela partida em 2 páginas.
    with _abrir_pdf_fixture() as pdf:
        tabelas = [t for p in pdf.pages for t in p.extract_tables()]

    cargos = sorocaba_sp.extrair_cargos_do_pdf(tabelas)
    nomes = {c.nome for c in cargos}

    assert len(cargos) == 7
    assert nomes == {
        "Técnico de Enfermagem",
        "Médico I – Médico do Trabalho",
        "Médico I – Nefrologista",
        "Médico I – Neurologista Adulto",
        "Médico I – Neurologista Infantil",
        "Médico I – Psiquiatra Adulto",
        "Médico I – Psiquiatra Infantil",
    }


def test_extrair_cargos_do_pdf_le_salario_por_hora_vagas_e_requisitos():
    with _abrir_pdf_fixture() as pdf:
        tabelas = [t for p in pdf.pages for t in p.extract_tables()]

    cargos = {c.nome: c for c in sorocaba_sp.extrair_cargos_do_pdf(tabelas)}

    neurologista = cargos["Médico I – Neurologista Adulto"]
    assert neurologista.numero_vagas == 1
    assert neurologista.vagas_ampla_concorrencia == 1
    assert neurologista.vagas_pcd == 0
    assert neurologista.valor_hora == 108.40
    assert neurologista.carga_horaria_semanal == 20
    assert "Curso Superior em Medicina" in neurologista.requisitos
    assert "Título de Especialista ou Residência Médica" in neurologista.requisitos

    tecnico = cargos["Técnico de Enfermagem"]
    assert tecnico.numero_vagas == 20
    assert tecnico.vagas_ampla_concorrencia == 18
    assert tecnico.vagas_pcd == 2
    assert tecnico.valor_hora == 23.49
    assert tecnico.carga_horaria_semanal == 30


def test_extrair_cargos_do_pdf_sem_tabela_devolve_lista_vazia():
    assert sorocaba_sp.extrair_cargos_do_pdf([]) == []
    assert sorocaba_sp.extrair_cargos_do_pdf([[]]) == []


def test_identificador_externo_usa_edital_e_slug_do_cargo():
    ident = sorocaba_sp.identificador_externo("01/2026", "Médico I – Neurologista Adulto")
    assert ident == "sorocaba-sp-01-2026-medico-i-neurologista-adulto"


def test_identificador_externo_sem_numero_edital_usa_fallback():
    ident = sorocaba_sp.identificador_externo(None, "Médico I – Nefrologista")
    assert ident.startswith("sorocaba-sp-sem-numero-")
