import rodar_ibgp as script
from notifica_vagas_scraper.fontes import ibgp


def _item(concurso_id=1, nome="CONCURSO PÚBLICO DO MUNICÍPIO DE X/MG - EDITAL Nº 01/2026", empresa_nome="MUNICÍPIO DE X/MG"):
    return ibgp.ItemListagem(
        concurso_id=concurso_id,
        nome=nome,
        empresa_nome=empresa_nome,
        numero_edital="01/2026",
        tipo="CONCURSO PÚBLICO",
        total_vagas=10,
        total_cargos=3,
        inicio_inscricao=None,
        fim_inscricao=None,
    )


def test_resolver_municipio_uf_usa_empresa_nome_quando_disponivel(monkeypatch):
    chamadas = []

    def _fake_buscar(nome, uf):
        chamadas.append((nome, uf))
        return 3147105 if (nome, uf) == ("X", "MG") else None

    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", _fake_buscar)

    item = _item()
    resultado = script._resolver_municipio_uf(item)

    assert resultado == ("X", "MG", 3147105)
    assert chamadas  # tentou pelo menos 1 candidato


def test_resolver_municipio_uf_caso_real_itabira_cai_pro_titulo_do_concurso(monkeypatch):
    # achado de peso: empresa.nome do INSTITUTO DE PREVIDÊNCIA DE ITABIRA
    # não tem "/UF" nenhum — só o título completo do concurso cita "/MG".
    def _fake_buscar(nome, uf):
        return 3131307 if (nome, uf) == ("Itabira", "MG") else None

    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", _fake_buscar)

    item = _item(
        empresa_nome="INSTITUTO DE PREVIDÊNCIA DE ITABIRA - ITABIRAPREV",
        nome=" CONCURSO PÚBLICO DO INSTITUTO DE PREVIDÊNCIA DE ITABIRA/MG - ITABIRAPREV - EDITAL Nº 01/2026",
    )
    resultado = script._resolver_municipio_uf(item)

    assert resultado == ("Itabira", "MG", 3131307)


def test_resolver_municipio_uf_devolve_none_quando_nada_bate_ibge(monkeypatch):
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: None)

    item = _item(empresa_nome="SOBENFeE - SOCIEDADE BRASILEIRA DE ENFERMAGEM", nome="EXAME DE SUFICIÊNCIA 2026")
    assert script._resolver_municipio_uf(item) is None


def test_resolver_municipio_uf_ignora_uf_fora_do_projeto(monkeypatch):
    # candidato bate certinho no padrão "/UF", mas não é MG nem SP — não
    # pode nem tentar validar contra o IBGE (fora de escopo do produto).
    chamadas = []

    def _fake_buscar(nome, uf):
        chamadas.append((nome, uf))
        return 999

    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", _fake_buscar)

    item = _item(empresa_nome="MUNICÍPIO DE X/RJ", nome="CONCURSO PÚBLICO DO MUNICÍPIO DE X/RJ")
    assert script._resolver_municipio_uf(item) is None
    assert chamadas == []
