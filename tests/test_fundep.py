from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import fundep

FIXTURES = Path(__file__).parent / "fixtures" / "fundep"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_listar_processos_abertos_filtra_vestibular_mas_mantem_processo_seletivo():
    # 5 cards reais na fixture: 2 "Vestibular" (entrada em curso de
    # graduação, fora do escopo do produto) devem ser filtrados; os outros
    # 3 (2 "Concurso Público" + 1 "Processo Seletivo") ficam — mesmo o
    # "Processo Seletivo de Especialização Lato Sensu" (id 68, academicamente
    # suspeito pelo TÍTULO) não é filtrado por decisão de projeto: o rótulo
    # estruturado "Processo Seletivo" é o mesmo usado por seleção de
    # emprego legítima em outras bancas da mesma plataforma, então sem
    # lista de exclusão agressiva ele passa e a revisão do Gemini decide.
    itens = fundep.listar_processos_abertos(_ler_fixture("abertos.html"))

    ids = {i.processo_id for i in itens}
    assert ids == {68, 65, 63}
    assert 74 not in ids  # Vestibular FAME/FUNJOB
    assert 69 not in ids  # Vestibular EMESCAM


def test_listar_processos_abertos_extrai_camara_municipal_do_titulo_livre():
    # achado real: o título da FUNDEP é frase livre ("Concurso Público da
    # Câmara Municipal de Passos - 01/2026"), não "Tipo - Órgão" limpo
    # como na Avança SP — extrair_municipio_de_titulo tem que buscar o
    # prefixo em qualquer ponto da frase, não só no início.
    itens = fundep.listar_processos_abertos(_ler_fixture("abertos.html"))
    passos = next(i for i in itens if i.processo_id == 65)

    assert passos.tipo_processo == "Concurso Público"
    assert passos.numero_edital == "01/2026"
    assert passos.municipio == "Passos"


def test_listar_processos_abertos_municipio_none_quando_titulo_nao_cita_cidade():
    # achado de peso pra esta fonte: o card do DMAE/Uberlândia (o achado
    # que motivou reabrir a investigação) NÃO cita "Uberlândia" em lugar
    # nenhum do título, só a sigla "DMAE" — extrair_municipio_de_titulo não
    # pode adivinhar, tem que devolver None (resolvido depois via
    # candidatos_municipio_por_sufixo no script, não aqui).
    itens = fundep.listar_processos_abertos(_ler_fixture("abertos.html"))
    dmae = next(i for i in itens if i.processo_id == 63)

    assert dmae.tipo_processo == "Concurso Público"
    assert dmae.numero_edital == "01/2026"
    assert dmae.municipio is None
    assert "DMAE" in dmae.titulo


def test_extrair_municipio_de_titulo_reconhece_variacoes_de_prefixo():
    assert fundep.extrair_municipio_de_titulo("Concurso Público da Prefeitura Municipal de Ouro Preto - 02/2026") == (
        "Ouro Preto"
    )
    assert fundep.extrair_municipio_de_titulo("Concurso Público da Câmara de Itaúna - 01/2026") == "Itaúna"


def test_extrair_municipio_de_titulo_devolve_none_sem_prefixo_reconhecido():
    assert fundep.extrair_municipio_de_titulo("Concurso Público DMAE - 01/2026") is None
    assert fundep.extrair_municipio_de_titulo("Vestibular de Medicina - EMESCAM 01/2027") is None


def test_listar_documentos_e_escolher_edital_reaproveitados_de_proseleta_sem_alteracao():
    # confirma que o pesquisador-fonte estava certo: proseleta.py funciona
    # sem alteração nenhuma contra o HTML real do DMAE/Uberlândia, inclusive
    # o desempate por data (edital consolidado e retificação empatados em
    # 10/07/2026 — o consolidado vem primeiro na ordem do HTML e é o
    # escolhido, mesmo achado de `escolher_edital` documentado na JCM).
    documentos = fundep.listar_documentos(_ler_fixture("detalhe_dmae_uberlandia.html"))
    assert len(documentos) == 6

    edital = fundep.escolher_edital(documentos)
    assert edital is not None
    assert edital.titulo.startswith("EDITAL CONSOLIDADO DO CONCURSO PÚBLICO Nº 01/2026")
    assert edital.data == date(2026, 7, 10)
    assert edital.url_pdf.startswith("https://anexos-r2.selecao.net.br/")


def test_candidatos_municipio_por_sufixo_inclui_uberlandia_como_ultimo_candidato():
    # não valida contra o IBGE aqui (função pura, sem rede) — só prova que
    # o sufixo de 1 palavra "Uberlândia" está na lista de candidatos
    # gerada a partir do título do edital escolhido, pra quem chama
    # (rodar_fundep.py) conseguir validar depois.
    documentos = fundep.listar_documentos(_ler_fixture("detalhe_dmae_uberlandia.html"))
    edital = fundep.escolher_edital(documentos)

    candidatos = fundep.candidatos_municipio_por_sufixo(edital.titulo)
    assert candidatos[-1] == "Uberlândia"
    assert candidatos[-2] == "DMAE Uberlândia"


def test_candidatos_municipio_por_sufixo_respeita_max_palavras():
    candidatos = fundep.candidatos_municipio_por_sufixo("a b c d e f g h", max_palavras=3)
    assert candidatos == ["f g h", "g h", "h"]


def test_listar_vagas_html_nao_descarta_nenhum_dos_28_cargos_reais():
    # achado real: as 28 linhas reais do quadro de vagas do DMAE/Uberlândia
    # vêm em 2 <table> dentro do mesmo bloco "Vagas" (mesmo achado da JCM)
    # — o parser tem que juntar as duas, sem lista de cargos conhecida.
    vagas = fundep.listar_vagas_html(_ler_fixture("detalhe_dmae_uberlandia.html"))
    assert len(vagas) == 28

    cargos = {v.cargo for v in vagas}
    assert "101 - Auxiliar Técnico Operacional" in cargos
    assert "511 - Químico" in cargos
    assert "510 - Psicólogo" in cargos  # última especialidade da 2ª tabela


def test_listar_vagas_html_quantidade_com_sufixo_cadastro_de_reserva():
    # achado real que motivou a função própria (regex, não `.isdigit()`
    # estrito): a célula vem como "10\n + Cadastro de Reserva", não
    # dígito puro — `listar_vagas_html` de outra fonte (JCM/ACCESS, via
    # proseleta.py) devolveria 0 vagas nesse formato.
    vagas = fundep.listar_vagas_html(_ler_fixture("detalhe_dmae_uberlandia.html"))

    auxiliar = next(v for v in vagas if v.cargo == "101 - Auxiliar Técnico Operacional")
    assert auxiliar.quantidade == 10
    assert auxiliar.cadastro_reserva is True

    eletricista = next(v for v in vagas if v.cargo == "202 - Eletricista Industrial")
    assert eletricista.quantidade == 1
    assert eletricista.cadastro_reserva is True


def test_listar_vagas_html_sem_secao_vagas_devolve_lista_vazia():
    assert fundep.listar_vagas_html("<html><body>sem vagas aqui</body></html>") == []


def test_identificador_externo_usa_codigo_do_cargo_pra_evitar_colisao():
    vagas = fundep.listar_vagas_html(_ler_fixture("detalhe_dmae_uberlandia.html"))
    ids = {fundep.identificador_externo(63, v) for v in vagas}

    assert len(ids) == len(vagas)  # nenhum cargo colide
    assert "fundep-63-101-auxiliar-tecnico-operacional" in ids
