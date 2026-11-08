from notifica_vagas_scraper.fontes.convocacoes_gerais import detectar, municipio_da_entidade


def test_convocacao_de_medico_com_concurso():
    t = "EDITAL DE CONVOCAÇÃO Nº 12/2026. A Prefeitura convoca os candidatos aprovados no Concurso Público nº 001/2024 para o cargo de MÉDICO CLÍNICO GERAL a apresentarem documentos."
    r = detectar("Convocação", t)
    assert r and r["tipo"] == "convocacao" and r["concurso"] and "1/2024" in r["concurso"]


def test_sem_medico_nao_conta():
    assert detectar("x", "Convoca os candidatos aprovados no concurso para o cargo de professor de matemática.") is None


def test_medico_veterinario_nao_conta():
    assert detectar("x", "Convoca o candidato aprovado no concurso público para o cargo de médico veterinário.") is None


def test_convocacao_de_reuniao_nao_conta():
    assert detectar("x", "Convocação para reunião do conselho municipal de saúde com a presença do médico responsável.") is None


def test_nomeacao_sem_concurso_nao_conta():
    assert detectar("x", "Nomeia fulano para o cargo em comissão de diretor, médico de formação.") is None


def test_nomeacao_de_aprovado_em_concurso_conta():
    r = detectar("x", "Nomeia a candidata aprovada em concurso público, classificada em 3º lugar, para o cargo de Médico ESF.")
    assert r and r["tipo"] == "nomeacao"


def test_municipio_da_entidade():
    assert municipio_da_entidade("Prefeitura Municipal de Ponta Grossa") == "Ponta Grossa"
    assert municipio_da_entidade("Prefeitura de Luz") == "Luz"
    assert municipio_da_entidade("Câmara Municipal de X") == "Câmara Municipal de X"
