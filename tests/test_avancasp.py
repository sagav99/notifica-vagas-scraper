from decimal import Decimal
from pathlib import Path

from notifica_vagas_scraper.fontes import avancasp

FIXTURES = Path(__file__).parent / "fixtures" / "avanca_sp"


def _ler_fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_extrair_municipio_reconhece_prefeitura_municipal():
    assert avancasp.extrair_municipio("PREFEITURA MUNICIPAL DE LIMEIRA") == (
        "Prefeitura Municipal",
        "Limeira",
    )


def test_extrair_municipio_reconhece_autarquia_municipal_de_saude():
    # prioridade #1 do produto: autarquia de saúde não pode ficar de fora
    # do reconhecimento de município, é exatamente o caso do concurso 266.
    assert avancasp.extrair_municipio("AUTARQUIA MUNICIPAL DE SAÚDE - ITAPECERICA DA SERRA") == (
        "Autarquia Municipal de Saúde",
        "Itapecerica da Serra",
    )


def test_extrair_municipio_devolve_none_para_orgao_fora_do_escopo():
    # CRECI/SP não é prefeitura/câmara/autarquia municipal — não mapeia
    # pra município nenhum, tem que ser descartado, não "chutado".
    assert (
        avancasp.extrair_municipio(
            "CONSELHO REGIONAL DE CORRETORES DE IMÓVEIS DO ESTADO DE SÃO PAULO - CRECI/SP"
        )
        is None
    )
    assert (
        avancasp.extrair_municipio(
            "INSTITUTO DE EDUCAÇÃO SUPERIOR E PESQUISA UNIREGISTRAL"
        )
        is None
    )


def test_listar_processos_abertos_descarta_orgaos_sem_municipio_reconhecido():
    itens = avancasp.listar_processos_abertos(_ler_fixture("listagem_abertos_cards.html"))

    # 8 cards no HTML, 3 não batem nenhum prefixo conhecido de
    # prefeitura/câmara/autarquia municipal de saúde — só 5 devem sobrar.
    assert len(itens) == 5
    ids = {i.processo_id for i in itens}
    assert 267 not in ids  # vestibular do instituto, sem município
    assert 251 not in ids  # CRECI/SP, sem município
    # "AGÊNCIA DE INOVAÇÃO E DESENVOLVIMENTO TECNOLÓGICO DE OSASCO S.A." —
    # cita o município mas não bate nenhum prefixo da whitelist (não é
    # prefeitura/câmara/autarquia de saúde); descartado por segurança em
    # vez de tentar adivinhar. Ver docstring do módulo e resumo da tarefa.
    assert 268 not in ids


def test_listar_processos_abertos_extrai_prefeitura_municipal():
    itens = avancasp.listar_processos_abertos(_ler_fixture("listagem_abertos_cards.html"))
    limeira_01 = next(i for i in itens if i.processo_id == 21)

    assert limeira_01.url == "https://www.avancasp.org.br/informacoes/21/"
    assert limeira_01.tipo_processo == "Concurso Público"
    assert limeira_01.numero_edital == "01/2026"
    assert limeira_01.orgao == "Prefeitura Municipal"
    assert limeira_01.municipio == "Limeira"
    assert limeira_01.uf == "SP"

    # 2 concursos distintos da mesma prefeitura (01/2026 e 02/2026) não
    # podem colidir/se sobrescrever na listagem.
    limeira_02 = next(i for i in itens if i.processo_id == 210)
    assert limeira_02.numero_edital == "02/2026"
    assert limeira_02.municipio == "Limeira"


def test_listar_processos_abertos_extrai_autarquia_de_saude_itapecerica():
    itens = avancasp.listar_processos_abertos(_ler_fixture("listagem_abertos_cards.html"))

    concurso = next(i for i in itens if i.processo_id == 266)
    assert concurso.tipo_processo == "Concurso Público"
    assert concurso.numero_edital == "01/2026"
    assert concurso.orgao == "Autarquia Municipal de Saúde"
    assert concurso.municipio == "Itapecerica da Serra"
    assert concurso.uf == "SP"

    # processo seletivo (ACS, id 269) é um processo DIFERENTE do concurso
    # (id 266), mesmo órgão/município — os dois têm que aparecer.
    processo_seletivo = next(i for i in itens if i.processo_id == 269)
    assert processo_seletivo.tipo_processo == "Processo de Seleção Pública"
    assert processo_seletivo.municipio == "Itapecerica da Serra"


def test_listar_vagas_html_nao_descarta_nenhuma_especialidade_do_concurso_266():
    # achado real: a fixture salva é um TRECHO da tabela completa (6 de 53
    # linhas reais), escolhido pra cobrir a variação de formato — ver
    # docstring do módulo. O parser não tem lista de cargos conhecida, lê
    # toda <tr> de 7 colunas igual, então o teste prova que NENHUMA linha
    # do trecho é descartada silenciosamente (proxy pro comportamento nas
    # 53 linhas reais, já que não há filtragem por nome de cargo).
    vagas = avancasp.listar_vagas_html(_ler_fixture("itapecerica_saude_concurso_266_vagas.html"))

    assert len(vagas) == 6
    cargos = {v.cargo for v in vagas}
    assert cargos == {
        "Auxiliar Administrativo",
        "Enfermeiro",
        "Médico Clínico Geral",
        "Médico Pediatra",
        "Médico Psiquiatra",
        "Procurador Autárquico",
    }

    # as 3 especialidades médicas do trecho, especificamente:
    clinico_geral = next(v for v in vagas if v.cargo == "Médico Clínico Geral")
    pediatra = next(v for v in vagas if v.cargo == "Médico Pediatra")
    psiquiatra = next(v for v in vagas if v.cargo == "Médico Psiquiatra")
    assert (clinico_geral.quantidade, pediatra.quantidade, psiquiatra.quantidade) == (1, 1, 3)


def test_listar_vagas_html_salario_mensal_fixo():
    vagas = avancasp.listar_vagas_html(_ler_fixture("itapecerica_saude_concurso_266_vagas.html"))
    auxiliar = next(v for v in vagas if v.cargo == "Auxiliar Administrativo")

    assert auxiliar.escolaridade == "Médio"
    assert auxiliar.salario == Decimal("3184.64")
    assert auxiliar.salario_texto == "R$ 3.184,64"
    assert auxiliar.carga_horaria == "40 h"
    assert auxiliar.quantidade == 10
    assert auxiliar.cadastro_reserva is True
    assert auxiliar.taxa_inscricao == Decimal("64.00")


def test_listar_vagas_html_salario_por_hora_nao_e_convertido_pra_mensal():
    # achado real: cargo médico costuma vir "R$ 48,31 por hora" — mesma
    # regra dos prompts do Gemini, nunca inventar valor mensal a partir de
    # taxa horária. `salario` fica None, mas o texto não se perde.
    vagas = avancasp.listar_vagas_html(_ler_fixture("itapecerica_saude_concurso_266_vagas.html"))
    medico = next(v for v in vagas if v.cargo == "Médico Clínico Geral")

    assert medico.salario is None
    assert medico.salario_texto == "R$ 48,31 por hora"
    assert medico.escolaridade == "Superior"
    assert medico.carga_horaria == "Até 220 h mensais"
    assert medico.cadastro_reserva is True

    enfermeiro = next(v for v in vagas if v.cargo == "Enfermeiro")
    assert enfermeiro.salario is None
    assert enfermeiro.salario_texto == "R$ 27,30 por hora"


def test_listar_vagas_html_processo_269_todas_as_37_microareas_de_acs():
    # a fixture do processo 269 tem só 3 das 37 linhas reais (comentário
    # no HTML confirma que as outras 34 seguem o mesmo padrão) — o que dá
    # pra provar aqui é que nenhuma das 3 presentes é descartada e que
    # cada microárea vira um cargo distinto (chave de dedup depende disso).
    vagas = avancasp.listar_vagas_html(_ler_fixture("itapecerica_processo_seletivo_269_acs.html"))

    assert len(vagas) == 3
    assert all(v.cargo.startswith("Agente Comunitário de Saúde - UBS JACIRA - Microárea") for v in vagas)
    microareas = {v.cargo.rsplit(" ", 1)[-1] for v in vagas}
    assert microareas == {"27", "28", "29"}
    assert all(v.salario == Decimal("3242.00") for v in vagas)
    assert all(v.quantidade == 1 and v.cadastro_reserva for v in vagas)


def test_identificador_externo_microarea_gera_chave_unica_por_cargo():
    vagas = avancasp.listar_vagas_html(_ler_fixture("itapecerica_processo_seletivo_269_acs.html"))
    ids = {avancasp.identificador_externo(269, v) for v in vagas}

    assert len(ids) == len(vagas)  # nenhuma microárea colide com outra
    assert "avancasp-269-agente-comunitario-de-saude-ubs-jacira-microarea-27" in ids


def test_listar_documentos_e_escolher_edital_reaproveitados_de_proseleta():
    # Documento/escolher_edital vêm prontos de proseleta.py (mesma
    # plataforma da JCM/ACCESS) — só confere que a integração funciona
    # com o HTML real da Avança SP, não reimplementa a lógica.
    documentos = avancasp.listar_documentos(_ler_fixture("itapecerica_saude_concurso_266_vagas.html"))
    assert len(documentos) == 6

    edital = avancasp.escolher_edital(documentos)
    assert edital is not None
    assert edital.titulo == "RETIFICAÇÃO I DO EDITAL COMPLETO"
    assert edital.data.isoformat() == "2026-08-18"
    assert edital.url_pdf.startswith("https://anexos-r2.selecao.net.br/")
