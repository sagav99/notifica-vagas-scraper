from pathlib import Path

from notifica_vagas_scraper.fontes import pbh_ibfc

FIXTURES = Path(__file__).parent / "fixtures" / "pbh_ibfc"


def _ler(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_traz_os_16_itens_reais_da_listagem_filtrada():
    itens = pbh_ibfc.listar_processos(_ler("pbh_listagem_geral_oportunidades_filtro_concurso_publico.html"))
    assert len(itens) == 16

    edital_01_2025 = next(i for i in itens if i.numero_edital == "01/2025")
    assert edital_01_2025.area == "Planejamento/Gestão de Pessoas"
    assert edital_01_2025.modalidade == "Concurso Público"
    assert edital_01_2025.url == "https://prefeitura.pbh.gov.br/saude/oportunidades-de-trabalho/concurso-publico-01-2025"
    assert edital_01_2025.resumo is not None
    assert "Carreira dos Servidores da área da Saúde" in edital_01_2025.resumo


def test_listar_processos_item_sem_resumo_real_vira_none():
    # achado real: `.item_ar_objeto` de vários itens só traz "…"
    # (placeholder), sem texto de verdade nenhum.
    itens = pbh_ibfc.listar_processos(_ler("pbh_listagem_geral_oportunidades_filtro_concurso_publico.html"))
    hospital = next(i for i in itens if i.numero_edital == "01/2026" and "Odilon Behrens" in i.area)
    assert hospital.resumo is None


def test_listar_processos_todos_os_itens_reais_sao_processaveis():
    # a listagem real (filtrada por modalidade=Concurso Público) não tem
    # nenhum item "interno" — confirma que o filtro de origem já é
    # suficiente pra esta amostra, mas ver teste da 2ª camada de defesa
    # abaixo pro caso em que ele não for.
    itens = pbh_ibfc.listar_processos(_ler("pbh_listagem_geral_oportunidades_filtro_concurso_publico.html"))
    assert all(pbh_ibfc.eh_concurso_externo_processavel(i) for i in itens)


def test_eh_concurso_externo_processavel_rejeita_edital_155_2026_promocao_interna():
    # achado real: o Edital 155/2026 é "PROCEDIMENTO SELETIVO INTERNO...
    # para promoção dos servidores públicos" (ver
    # edital_155_2026_promocao_interna_NAO_e_concurso_externo.pdf), NÃO
    # concurso externo — mesmo que apareça um dia numa listagem mais
    # ampla (modalidade "Seleção Interna" ou rotulagem parecida), a 2ª
    # camada de defesa tem que barrar.
    item_interno = pbh_ibfc.ItemListagem(
        area="Saúde",
        numero_edital="155/2026",
        modalidade="Seleção Interna",
        url="https://prefeitura.pbh.gov.br/saude/oportunidades-de-trabalho/procedimento-seletivo-interno-155-2026",
        resumo="Procedimento Seletivo Interno para promoção dos servidores públicos...",
    )
    assert pbh_ibfc.eh_concurso_externo_processavel(item_interno) is False

    # variação "Interno" (masculino, sem "a") também tem que ser pega.
    item_interno_masc = pbh_ibfc.ItemListagem(
        area="Saúde", numero_edital="155/2026", modalidade="Procedimento Seletivo Interno", url="x", resumo=None
    )
    assert pbh_ibfc.eh_concurso_externo_processavel(item_interno_masc) is False


def test_eh_concurso_externo_processavel_aceita_concurso_publico_normal():
    item = pbh_ibfc.ItemListagem(
        area="Saúde", numero_edital="01/2025", modalidade="Concurso Público", url="x", resumo=None
    )
    assert pbh_ibfc.eh_concurso_externo_processavel(item) is True


def test_listar_documentos_traz_os_58_atos_e_anexos_reais():
    documentos = pbh_ibfc.listar_documentos(_ler("pbh_pagina_fixa_concurso_01_2025_582_vagas.html"))
    assert len(documentos) == 58
    assert all(d.url_pdf.lower().endswith(".pdf") for d in documentos)


def test_listar_documentos_acha_link_pdf_mesmo_quando_nao_e_a_2a_celula():
    # achado real: a posição do link .pdf na linha varia (ícone "link
    # externo" vs ícone "PDF" trocam de coluna dependendo da linha) —
    # "1º Ato de reclassificação" é um caso real onde o link .pdf vem
    # DEPOIS do link pro dom-web, não antes.
    documentos = pbh_ibfc.listar_documentos(_ler("pbh_pagina_fixa_concurso_01_2025_582_vagas.html"))
    reclassificacao = next(d for d in documentos if d.titulo == "1º Ato de reclassificação")
    assert reclassificacao.url_pdf.endswith("1.ato_.reclassificacao.smsa_.01.25.pdf")
    assert "dom-web" not in reclassificacao.url_pdf


def test_listar_documentos_le_a_data_da_ultima_coluna():
    documentos = pbh_ibfc.listar_documentos(_ler("pbh_pagina_fixa_concurso_01_2025_582_vagas.html"))
    homologacao = next(d for d in documentos if d.titulo == "Homologação")
    assert homologacao.data is not None
    assert homologacao.data.isoformat() == "2026-01-22"


def test_escolher_edital_com_anexo_prioriza_o_compilado_apos_retificacao():
    documentos = pbh_ibfc.listar_documentos(_ler("pbh_pagina_fixa_concurso_01_2025_582_vagas.html"))
    escolhido = pbh_ibfc.escolher_edital_com_anexo(documentos)

    assert escolhido is not None
    assert escolhido.titulo == "Edital 01/2025 - compilado após 3ª retificação"
    assert escolhido.url_pdf.endswith("editalretificadoeprorrogacao03.pdf")


def test_escolher_edital_com_anexo_ignora_retificacoes_isoladas_e_convocacoes():
    documentos = pbh_ibfc.listar_documentos(_ler("pbh_pagina_fixa_concurso_01_2025_582_vagas.html"))
    escolhido = pbh_ibfc.escolher_edital_com_anexo(documentos)
    # "3ª Retificação do Edital 01/2025 e Prorrogação das Inscrições" só
    # lista o que mudou, não é candidato (não começa com "Edital").
    assert escolhido.titulo != "3ª Retificação do Edital 01/2025 e Prorrogação das Inscrições"


def test_escolher_edital_com_anexo_cai_pro_edital_original_sem_compilado():
    documentos = [
        pbh_ibfc.Documento(titulo="Edital SMSA 155/2026", url_pdf="https://x/edital-155.pdf", data=None),
        pbh_ibfc.Documento(titulo="Convocação - Prova Objetiva", url_pdf="https://x/convocacao.pdf", data=None),
    ]
    escolhido = pbh_ibfc.escolher_edital_com_anexo(documentos)
    assert escolhido is not None
    assert escolhido.titulo == "Edital SMSA 155/2026"


def test_escolher_edital_com_anexo_lista_vazia_devolve_none():
    assert pbh_ibfc.escolher_edital_com_anexo([]) is None


def test_escolher_edital_com_anexo_sem_edital_publicado_devolve_none():
    documentos = [pbh_ibfc.Documento(titulo="Comunicado Geral", url_pdf="https://x/y.pdf", data=None)]
    assert pbh_ibfc.escolher_edital_com_anexo(documentos) is None


def test_montar_cargo_com_jornada_diferencia_mesma_especialidade_em_jornadas_diferentes():
    # achado real: "Médico - Pediatria" tem 3 linhas no ANEXO I (12h/20h/
    # 24h), cada uma com vagas e salário próprios.
    cargo_12h = pbh_ibfc.montar_cargo_com_jornada("Médico - Pediatria", "12 Horas")
    cargo_20h = pbh_ibfc.montar_cargo_com_jornada("Médico - Pediatria", "20 Horas")
    cargo_24h = pbh_ibfc.montar_cargo_com_jornada("Médico - Pediatria", "24 Horas")

    assert len({cargo_12h, cargo_20h, cargo_24h}) == 3
    assert "12 Horas" in cargo_12h
    assert "20 Horas" in cargo_20h
    assert "24 Horas" in cargo_24h


def test_montar_cargo_com_jornada_sem_jornada_devolve_so_o_cargo():
    assert pbh_ibfc.montar_cargo_com_jornada("Médico - Cardiologia", None) == "Médico - Cardiologia"
    assert pbh_ibfc.montar_cargo_com_jornada("Médico - Cardiologia", "") == "Médico - Cardiologia"


def test_montar_cargo_com_jornada_nao_duplica_se_ja_estiver_no_texto():
    resultado = pbh_ibfc.montar_cargo_com_jornada("Médico - Pediatria (20 Horas)", "20 Horas")
    assert resultado == "Médico - Pediatria (20 Horas)"


def test_identificador_externo_diferencia_jornadas_da_mesma_especialidade():
    cargo_12h = pbh_ibfc.montar_cargo_com_jornada("Médico - Pediatria", "12 Horas")
    cargo_20h = pbh_ibfc.montar_cargo_com_jornada("Médico - Pediatria", "20 Horas")

    id_12h = pbh_ibfc.identificador_externo("01/2025", cargo_12h)
    id_20h = pbh_ibfc.identificador_externo("01/2025", cargo_20h)

    assert id_12h != id_20h
    assert id_12h.startswith("pbh-ibfc-01-2025-")
