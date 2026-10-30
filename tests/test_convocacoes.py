from datetime import date
from types import SimpleNamespace

from notifica_vagas_scraper.fontes import convocacoes as conv


def test_nome_exato_ignora_acento_e_caixa():
    assert conv.nome_no_texto("José da Conceição Souza", "Convoca-se o candidato JOSE DA CONCEICAO SOUZA para posse") >= 0


def test_nome_precisa_de_duas_palavras():
    assert conv.nome_no_texto("Souza", "candidato Souza") == -1


def test_nome_dentro_de_nome_maior_nao_casa():
    texto = "Considerando que o servidor municipal Bianca Maria da Silva do Vale, ocupante do cargo"
    assert conv.nome_no_texto("Maria da Silva", texto) == -1


def test_nome_depois_de_candidato_casa():
    assert conv.nome_no_texto("Maria da Silva", "Convoca a candidata Maria da Silva, classificada em 3º") >= 0


def test_lista_em_caixa_alta_nao_perde_convocado():
    texto = "86 JOSIANE RIBEIRO DE ALMEIDA MARIA DA SILVA SANTOS 89 PEDRO LIMA"
    assert conv.nome_no_texto("Maria da Silva Santos", texto) >= 0


def test_nome_em_lista_numerada_casa():
    assert conv.nome_no_texto("Nivia Maria da Silva", "368 99044331/1 MICAELLE 369 99044144/1 Nivia Maria da Silva PAEIAIEF") >= 0


def test_contexto_titulo_de_convocacao():
    assert conv.contexto_de_convocacao("EDITAL DE CONVOCAÇÃO 03/2026", "Maria da Silva", "Maria da Silva")


def test_contexto_portaria_de_exoneracao_nao():
    assert not conv.contexto_de_convocacao("PORTARIA 12 EXONERAÇÃO", "exonera Maria da Silva", "Maria da Silva")


def test_contexto_sinal_perto_do_nome():
    texto = "Resultado final. Fica convocada para apresentar documentos a candidata Maria da Silva, 3ª colocada."
    assert conv.contexto_de_convocacao("Resultado final", texto, "Maria da Silva")


def test_numero_do_edital_ignora_zeros():
    assert conv.numero_edital_no_texto("Concurso 001/2023", "CONVOCAÇÃO do concurso público nº 1/2023")
    assert not conv.numero_edital_no_texto("001/2023", "concurso público nº 2/2023")
    assert conv.numero_edital_no_texto(None, "qualquer")


def test_cargo_ignora_exame_medico():
    assert not conv.cargo_no_texto("Médico", "apresentar exame médico admissional; cargo: Professor")
    assert conv.cargo_no_texto("Médico", "cargo: Médico Clínico Geral")


def test_entidade_do_municipio():
    assert conv.entidade_eh_do_municipio("Prefeitura Municipal de Divinópolis", "Divinopolis")


def test_achados_sc_por_nome_e_por_concurso():
    pub = SimpleNamespace(
        codigo="123", titulo="Edital de Convocação 5/2026", categoria="Concursos", municipio="Jaraguá do Sul",
        data="2026-10-28", link="https://x/1", url="https://x/1.pdf",
        texto="<p>Convoca o candidato Carlos Eduardo Lima para o cargo de Médico do concurso 001/2026</p>",
    )
    ac = conv.Acompanhamento("a1", "u1", "Carlos Eduardo Lima", "SC", "Jaraguá do Sul", "1/2026", "Médico")
    outro = conv.Acompanhamento("a2", "u2", "Ana Souza Pires", "SC", "Jaraguá do Sul", "1/2026", "Médico")
    achados = conv.achados_sc([pub], [ac, outro])
    assert {(a.acompanhamento_id, a.tipo) for a in achados} == {("a1", "nome"), ("a2", "concurso")}
    assert achados[0].data_publicacao == date(2026, 10, 28)


def test_avaliar_nome_sem_cadastro_de_concurso_detecta_concurso_e_cargo():
    ac = conv.Acompanhamento("a1", "u1", "Maria da Silva Santos", None, None)
    texto = "Edital de Convocação do Concurso Público nº 001/2023. Convoca para o cargo de Médico Clínico Geral a candidata Maria da Silva Santos, 3ª colocada."
    achado = conv.avaliar_publicacao(
        ac, fonte="sigpub-mg", uf="MG", chave="sigpub-mg:X", titulo="EDITAL DE CONVOCAÇÃO 5", entidade="Prefeitura de Luz",
        data_publicacao=date(2026, 10, 20), url="https://x", texto=texto,
    )
    assert achado.tipo == "nome" and achado.municipio == "Luz"
    assert achado.concurso == "Concurso público 001/2023"
    assert achado.cargo.startswith("Médico Clínico Geral")


def test_avaliar_nome_ausente_sem_concurso_cadastrado_nao_avisa():
    ac = conv.Acompanhamento("a1", "u1", "Maria da Silva Santos", None, None)
    assert conv.avaliar_publicacao(
        ac, fonte="s", uf="MG", chave="k", titulo="EDITAL DE CONVOCAÇÃO 5", entidade="Prefeitura de Luz",
        data_publicacao=None, url="u", texto="Convoca João Pereira Lima",
    ) is None


def test_avaliar_concurso_cadastrado_avisa_sem_o_nome():
    ac = conv.Acompanhamento("a1", "u1", "Maria da Silva Santos", "MG", "Luz", "001/2023", None)
    achado = conv.avaliar_publicacao(
        ac, fonte="s", uf="MG", chave="k", titulo="EDITAL DE CONVOCAÇÃO 5", entidade="Prefeitura de Luz",
        data_publicacao=None, url="u", texto="Convocação de aprovados no concurso 1/2023. Convoca João Pereira Lima",
    )
    assert achado.tipo == "concurso"


def test_municipio_da_entidade():
    assert conv.municipio_da_entidade("Prefeitura Municipal de Rolândia") == "Rolândia"
    assert conv.municipio_da_entidade("Prefeitura de Luz") == "Luz"


def test_assinatura_do_ato_nao_conta_como_candidato():
    texto = "Nomeia candidato aprovado. Publique-se. MARCEL VIEIRA RODRIGUES DA CUNHA Prefeito Municipal"
    assert conv.nome_no_texto("Marcel Vieira Rodrigues da Cunha", texto) == -1
    texto2 = texto + " Convocado: Marcel Vieira Rodrigues da Cunha, classificado em 3º"
    assert conv.nome_no_texto("Marcel Vieira Rodrigues da Cunha", texto2) > 0


def test_convocacao_para_assinatura_de_termo_e_ruido():
    assert not conv.contexto_de_convocacao("CONVOCAÇÃO PARA ASSINATURA SEGUNDO TERMO ADITIVO", "Maria da Silva Santos", "Maria da Silva Santos")


def test_cargo_detectado_nao_inclui_o_nome_do_candidato():
    _, cargo = conv.detectar_concurso("EDITAL 7", "Convoca para o cargo de Médico Clínico Geral o candidato Fulano de Tal, 2º colocado.", 80)
    assert cargo == "Médico Clínico Geral"
