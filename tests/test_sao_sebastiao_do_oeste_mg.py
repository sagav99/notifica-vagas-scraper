from io import BytesIO
from pathlib import Path

import pdfplumber

from notifica_vagas_scraper.fontes import sao_sebastiao_do_oeste_mg as fonte

FIXTURES = Path(__file__).parent / "fixtures" / "sao_sebastiao_do_oeste_mg"


def _ler_html(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def _abrir_anexos_pdf():
    conteudo = (FIXTURES / "edital_simplificado_01_2026_anexos_cargos_salarios.pdf").read_bytes()
    return pdfplumber.open(BytesIO(conteudo))


def _texto_e_tabelas_anexos():
    with _abrir_anexos_pdf() as pdf:
        texto = "\n".join((p.extract_text() or "") for p in pdf.pages)
        tabelas = [t for p in pdf.pages for t in p.extract_tables()]
    return texto, tabelas


def test_listar_processos_le_os_12_itens_da_listagem_real():
    html = _ler_html("listagem_processos_seletivos_pagina1.html")
    itens = fonte.listar_processos(html)

    assert len(itens) == 12
    assert all(i.titulo and i.url for i in itens)
    # data em português ("Terça, 22 Setembro 2026") parseada corretamente
    primeiro = itens[0]
    assert primeiro.titulo.startswith("Gabarito Oficial")
    from datetime import date

    assert primeiro.data_publicacao == date(2026, 9, 22)


def test_eh_edital_abertura_descarta_todo_ruido_da_listagem_real():
    # achado real: nenhum dos 12 itens da página 1 é edital de abertura —
    # todos são gabarito/resultado/retificação/despacho/convocação/
    # extrato, ou (1 caso) um edital de programa não-relacionado
    # (Minha Casa Minha Vida, mas com "retificação" no título também).
    html = _ler_html("listagem_processos_seletivos_pagina1.html")
    itens = fonte.listar_processos(html)
    assert not any(fonte.eh_edital_abertura(i.titulo) for i in itens)


def test_eh_edital_abertura_aceita_titulo_do_edital_real():
    assert fonte.eh_edital_abertura("Edital de Processo Seletivo Simplificado nº 01/2026")


def test_eh_edital_abertura_descarta_retificacao_de_programa_habitacional():
    titulo = (
        "Retificação nº 003/2026 (Nova prorrogação e retificação do edital de inscrição para "
        "o processo de seleção de candidatos para o Programa Habitacional Minha Casa Minha Vida)"
    )
    assert not fonte.eh_edital_abertura(titulo)


def test_listar_documentos_acha_edital_e_anexos_do_item_real():
    html = _ler_html("item_4769_edital_simplificado_01_2026.html")
    documentos = fonte.listar_documentos(html)

    titulos = {d.titulo for d in documentos}
    assert titulos == {"Edital", "Anexos"}


def test_escolher_pdf_anexo_prioriza_o_link_anexos():
    documentos = [
        fonte.Documento(titulo="Edital", url="https://x/editalprocessoseletivosim012026.pdf"),
        fonte.Documento(titulo="Anexos", url="https://x/editalprocessoseletivosim012026anexos.pdf"),
    ]
    escolhido = fonte.escolher_pdf_anexo(documentos)
    assert escolhido.titulo == "Anexos"


def test_escolher_pdf_anexo_sem_anexos_cai_pro_primeiro_pdf():
    documentos = [fonte.Documento(titulo="Edital", url="https://x/edital.pdf")]
    escolhido = fonte.escolher_pdf_anexo(documentos)
    assert escolhido.titulo == "Edital"


def test_escolher_pdf_anexo_sem_nenhum_pdf_devolve_none():
    assert fonte.escolher_pdf_anexo([]) is None
    assert fonte.escolher_pdf_anexo([fonte.Documento(titulo="Link externo", url="https://x/pagina")]) is None


def test_extrair_edital_le_numero_banca_prova_curriculo_e_inscricoes_do_pdf_real():
    texto, tabelas = _texto_e_tabelas_anexos()
    edital = fonte.extrair_edital(texto, tabelas)

    from datetime import date

    assert edital.numero_edital == "01/2026"
    assert edital.banca_organizadora == (
        "IDEAP – Instituto de Desenvolvimento Social, Empresarial e de Administração Pública"
    )
    assert edital.tem_prova is True
    # achado real: o ANEXO II cita "Prova de Títulos" como fase avaliativa
    assert edital.exige_curriculo is True
    assert edital.inscricoes_inicio == date(2026, 8, 31)
    assert edital.inscricoes_fim == date(2026, 9, 29)


def test_extrair_cargos_do_pdf_nao_descarta_nenhuma_especialidade_de_saude():
    # caso real mais denso disponível nas fixtures: 5 cargos no mesmo
    # ANEXO I, incluindo Médico — nenhum descartado, mesmo os não-médicos
    # (a decisão de escopo "só médico entra em catálogo" é de
    # `categorizarCargoSaude`, fora deste parser).
    _, tabelas = _texto_e_tabelas_anexos()
    cargos = fonte.extrair_cargos_do_pdf(tabelas)
    nomes = {c.nome for c in cargos}

    assert len(cargos) == 5
    assert nomes == {
        "Auxiliar de Saúde Bucal – ESB",
        "Enfermeiro – ESF",
        "Médico – ESF",
        "Odontólogo – ESB",
        "Técnico em Enfermagem – ESF",
    }


def test_extrair_cargos_do_pdf_le_vagas_salario_carga_e_requisitos_do_medico():
    _, tabelas = _texto_e_tabelas_anexos()
    cargos = {c.nome: c for c in fonte.extrair_cargos_do_pdf(tabelas)}

    medico = cargos["Médico – ESF"]
    assert medico.codigo == "3"
    assert medico.numero_vagas == 3
    assert medico.vagas_pcd == 0
    assert medico.vencimento_inicial == 19846.52
    assert medico.carga_horaria == "40H"
    assert "Superior em Medicina" in medico.requisitos
    assert "Registro CRM" in medico.requisitos
    assert medico.taxa_inscricao == 100.0

    tecnico = cargos["Técnico em Enfermagem – ESF"]
    assert tecnico.numero_vagas == 5
    assert tecnico.vagas_pcd == 1
    assert tecnico.vencimento_inicial == 4414.42


def test_extrair_cargos_do_pdf_pula_cabecalho_subtotal_e_total():
    _, tabelas = _texto_e_tabelas_anexos()
    cargos = fonte.extrair_cargos_do_pdf(tabelas)
    nomes = {c.nome for c in cargos}
    assert "SUBTOTAL" not in nomes
    assert "TOTAL DE VAGAS" not in nomes


def test_extrair_cargos_do_pdf_sem_tabela_devolve_lista_vazia():
    assert fonte.extrair_cargos_do_pdf([]) == []
    assert fonte.extrair_cargos_do_pdf([[]]) == []


def test_identificador_externo_usa_url_completa_nao_so_o_numero_do_edital():
    # achado real (ver docstring do módulo): a numeração do edital
    # reinicia entre secretarias/grupos de cargos — dois editais "01/2026"
    # reais e distintos (URLs diferentes) não podem colidir no dedup.
    url_a = "https://www.saosebastiaodooeste.mg.gov.br/processos-seletivos/item/4769-edital-de-processo-seletivo-simplificado-n-01-2026"
    url_b = "https://www.saosebastiaodooeste.mg.gov.br/processos-seletivos/item/5001-outro-edital-de-processo-seletivo-n-01-2026"

    ident_a = fonte.identificador_externo(url_a, "Médico – ESF")
    ident_b = fonte.identificador_externo(url_b, "Médico – ESF")

    assert ident_a != ident_b
    assert ident_a.startswith("sao-sebastiao-do-oeste-mg-")
