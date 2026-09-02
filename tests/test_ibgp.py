import json
from pathlib import Path

from notifica_vagas_scraper.fontes import ibgp

FIXTURES = Path(__file__).parent / "fixtures" / "ibgp"


def _ler_json(nome: str):
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def test_listar_concursos_inscricao_aberta_traz_os_15_itens_reais():
    itens = ibgp.listar_concursos(_ler_json("rest_concurso_inscricaoAberta.json"))
    assert len(itens) == 15

    paracatu = next(i for i in itens if i.concurso_id == 670)
    assert paracatu.empresa_nome == "MUNICÍPIO DE PARACATU/MG"
    assert paracatu.numero_edital == "02/2026"
    assert paracatu.tipo == "CONCURSO PÚBLICO"
    assert paracatu.total_vagas == 342
    assert paracatu.total_cargos == 69
    assert paracatu.inicio_inscricao is not None
    assert paracatu.fim_inscricao is not None
    assert paracatu.fim_inscricao.strftime("%d/%m/%Y %H:%M") == "22/09/2026 16:00"


def test_listar_concursos_funciona_igual_pra_proximas_inscricoes():
    # mesmo formato de item nos 2 endpoints, ver docstring do módulo.
    itens = ibgp.listar_concursos(_ler_json("rest_concurso_proximasInscricoes.json"))
    assert len(itens) == 23
    coromandel = next(i for i in itens if i.concurso_id == 684)
    assert coromandel.empresa_nome == "MUNICÍPIO DE COROMANDEL/MG"


def test_listar_cargos_nao_descarta_nenhum_dos_69_cargos_reais_de_paracatu():
    # prioridade #1 do produto: nenhuma especialidade médica pode sumir.
    dados = _ler_json("rest_concurso_cargos_670_paracatu.json")
    cargos = ibgp.listar_cargos(dados)
    assert len(cargos) == 69

    nomes = {c.nome for c in cargos}
    medicos = {n for n in nomes if n.startswith("MÉDICO")}
    assert len(medicos) == 17  # 17 especialidades médicas reais na fixture
    assert "MÉDICO - CIRURGIA GERAL" in nomes
    assert "MÉDICO - PEDIATRIA" in nomes
    assert "MÉDICO - PSIQUIATRIA" in nomes

    cirurgia_geral = next(c for c in cargos if c.nome == "MÉDICO - CIRURGIA GERAL")
    assert cirurgia_geral.codigo == "601"
    assert cirurgia_geral.total_vagas == 8


def test_listar_editais_traz_os_9_documentos_reais():
    documentos = ibgp.listar_editais(_ler_json("rest_concurso_editais_670_paracatu.json"))
    assert len(documentos) == 9
    assert {d.id for d in documentos} == {21893, 21894, 21916, 21917, 21918, 21919, 21920, 21921, 21922}


def test_escolher_edital_vencimento_acha_o_anexo_i_mesmo_com_sufixo_de_retificacao():
    documentos = ibgp.listar_editais(_ler_json("rest_concurso_editais_670_paracatu.json"))
    edital = ibgp.escolher_edital_vencimento(documentos)

    assert edital is not None
    assert edital.id == 21894
    assert "VENCIMENTO" in edital.nome.upper()
    assert edital.nome_real == "01 - ANEXO I - CARGOS ESC. JORNADAS VAGAS E VENCIMENTOS - RETIFICAÇÃO Nº 01.pdf"
    assert edital.data is not None


def test_escolher_edital_vencimento_ignora_outros_anexos_e_edital_principal():
    documentos = ibgp.listar_editais(_ler_json("rest_concurso_editais_670_paracatu.json"))
    edital = ibgp.escolher_edital_vencimento(documentos)
    # "ANEXO VI - MODELO DE DECLARAÇÃO E DE LAUDO MÉDICO PARA PCD" cita
    # "MÉDICO" mas não é "ANEXO I" nem tem "VENCIMENTO" — não pode ser
    # escolhido.
    assert edital.id != 21920


def test_escolher_edital_vencimento_lista_vazia_devolve_none():
    assert ibgp.escolher_edital_vencimento([]) is None


def test_montar_url_download_usa_nome_real_sem_duplicar_extensao_pdf():
    url = ibgp.montar_url_download(
        concurso_id=670,
        edital_id=21894,
        nome_real="01 - ANEXO I - CARGOS ESC. JORNADAS VAGAS E VENCIMENTOS - RETIFICAÇÃO Nº 01.pdf",
    )
    assert url.startswith(f"{ibgp.BASE_URL}/rest/concurso/download/edital/21894/?file=")
    assert url.count(".pdf") == 1
    assert "site/anexos/670/" in url
    # espaço e "º" ficam URL-encoded, não literais.
    assert " " not in url.split("file=", 1)[1]


def test_extrair_candidatos_municipio_uf_caso_simples_ultima_palavra():
    candidatos = ibgp.extrair_candidatos_municipio_uf(
        "MUNICÍPIO DE DELFINÓPOLIS/MG",
        "CONCURSO PÚBLICO DO MUNICÍPIO DE DELFINÓPOLIS/MG - EDITAL Nº 01/2026",
    )
    assert ("Delfinópolis", "MG") in candidatos


def test_extrair_candidatos_municipio_uf_caso_camara_municipal_duas_palavras():
    candidatos = ibgp.extrair_candidatos_municipio_uf("CÂMARA MUNICIPAL DE BOM DESPACHO/MG")
    assert ("Bom Despacho", "MG") in candidatos


def test_extrair_candidatos_municipio_uf_caso_municipio_composto_quatro_palavras():
    candidatos = ibgp.extrair_candidatos_municipio_uf(
        "PROCESSO SELETIVO PÚBLICO DO MUNICÍPIO DE SANTO ANTÔNIO DO ITAMBÉ/MG - EDITAL Nº 01/2026"
    )
    assert ("Santo Antônio Do Itambé", "MG") in candidatos


def test_extrair_candidatos_municipio_uf_caso_itabira_autarquia_uf_no_meio_do_titulo():
    # achado de peso: "/MG" não está no fim de empresa.nome (que nem tem
    # barra nenhuma), só no MEIO do título completo do concurso.
    candidatos = ibgp.extrair_candidatos_municipio_uf(
        "INSTITUTO DE PREVIDÊNCIA DE ITABIRA - ITABIRAPREV",  # empresa.nome, sem "/UF"
        " CONCURSO PÚBLICO DO INSTITUTO DE PREVIDÊNCIA DE ITABIRA/MG - ITABIRAPREV - EDITAL Nº 01/2026",
    )
    assert ("Itabira", "MG") in candidatos


def test_extrair_candidatos_municipio_uf_nao_confunde_numero_de_edital_com_uf():
    # "Nº 01/2026" não pode virar candidato de UF "20" ou parecido.
    candidatos = ibgp.extrair_candidatos_municipio_uf(
        "CONCURSO PÚBLICO DA CÂMARA MUNICIPAL DE BOM DESPACHO/MG - EDITAL Nº 01/2026"
    )
    assert all(uf.isalpha() for _municipio, uf in candidatos)
    assert all(len(uf) == 2 for _municipio, uf in candidatos)


def test_extrair_candidatos_municipio_uf_sem_padrao_devolve_lista_vazia():
    assert ibgp.extrair_candidatos_municipio_uf("SOBENFeE - SOCIEDADE BRASILEIRA DE ENFERMAGEM") == []


def test_parear_salario_por_codigo_casa_todos_os_medicos_por_codigo():
    dados = _ler_json("rest_concurso_cargos_670_paracatu.json")
    cargos = ibgp.listar_cargos(dados)

    # simula o que o Gemini devolveria lendo o Anexo I real de Paracatu —
    # cada linha da tabela começa com "<código> - <cargo>", confirmado no
    # texto extraído do PDF (`paracatu_anexo1_cargos_vencimentos.pdf`).
    vagas_gemini = [
        {"cargo": "601 - MÉDICO - CIRURGIA GERAL", "salario": 9600.0},
        {"cargo": "602 – MÉDICO - MEDICINA INTENSIVA", "salario": 9600.0},  # travessão, não hífen
        {"cargo": "101 - AUXILIAR DE SERVIÇOS DE EDUCAÇÃO", "salario": 1721.44},
    ]

    resultado = ibgp.parear_salario_por_codigo(cargos, vagas_gemini)

    assert resultado["601"] == 9600.0
    assert resultado["602"] == 9600.0
    assert resultado["101"] == 1721.44


def test_parear_salario_por_codigo_nao_descarta_cargo_sem_casamento_no_pdf():
    # achado de segurança: um cargo que o Gemini não conseguiu ler (ou leu
    # com nome bem diferente) ainda entra no resultado, só com salario=None
    # — a fonte de verdade de QUAIS cargos existem é sempre `listar_cargos`.
    dados = _ler_json("rest_concurso_cargos_670_paracatu.json")
    cargos = ibgp.listar_cargos(dados)

    resultado = ibgp.parear_salario_por_codigo(cargos, vagas_gemini=[])

    assert len(resultado) == len(cargos) == 69
    assert resultado["601"] is None
    assert all(codigo in resultado for codigo in (c.codigo for c in cargos))


def test_parear_salario_por_codigo_cai_pro_nome_quando_gemini_nao_traz_codigo():
    dados = _ler_json("rest_concurso_cargos_670_paracatu.json")
    cargos = ibgp.listar_cargos(dados)

    resultado = ibgp.parear_salario_por_codigo(
        cargos, vagas_gemini=[{"cargo": "Médico - Cirurgia Geral", "salario": 9600.0}]
    )

    assert resultado["601"] == 9600.0


def test_identificador_externo_usa_codigo_do_cargo():
    dados = _ler_json("rest_concurso_cargos_670_paracatu.json")
    cargos = ibgp.listar_cargos(dados)
    ids = {ibgp.identificador_externo(670, c) for c in cargos}

    assert len(ids) == len(cargos)  # nenhum cargo colide
    assert "ibgp-670-601" in ids
