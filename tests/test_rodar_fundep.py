import rodar_fundep as script
from notifica_vagas_scraper.fontes import fundep


def _item(processo_id=1, municipio=None, titulo="Concurso Público X - 01/2026"):
    return fundep.ItemListagem(
        processo_id=processo_id,
        url=f"https://fundep.selecao.net.br/informacoes/{processo_id}/",
        tipo_processo="Concurso Público",
        numero_edital="01/2026",
        titulo=titulo,
        municipio=municipio,
    )


def _edital(titulo):
    return fundep.Documento(titulo=titulo, data=None, url_pdf="https://anexos-r2.selecao.net.br/x.pdf")


def test_resolver_municipio_uf_usa_municipio_da_listagem_quando_disponivel(monkeypatch):
    # achado real: Câmara Municipal de Passos já vem com município
    # reconhecido na listagem — não precisa nem olhar o edital.
    chamadas = []

    def _fake_buscar(nome, uf):
        chamadas.append((nome, uf))
        return 3147808 if (nome, uf) == ("Passos", "MG") else None

    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", _fake_buscar)

    item = _item(municipio="Passos")
    resultado = script._resolver_municipio_uf(item, _edital("qualquer coisa"))

    assert resultado == ("Passos", "MG", 3147808)
    assert chamadas[0] == ("Passos", "MG")  # tenta o município da listagem antes de qualquer sufixo


def test_resolver_municipio_uf_cai_pro_sufixo_do_titulo_do_edital_quando_listagem_nao_tem(monkeypatch):
    # achado de peso: caso real do DMAE/Uberlândia — item.municipio é None
    # (título da listagem só cita a sigla "DMAE"), então precisa cair pro
    # sufixo do título do edital escolhido ("... DMAE Uberlândia").
    def _fake_buscar(nome, uf):
        return 3170206 if (nome, uf) == ("Uberlândia", "MG") else None

    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", _fake_buscar)

    item = _item(municipio=None, titulo="Concurso Público DMAE - 01/2026")
    edital = _edital(
        "EDITAL CONSOLIDADO DO CONCURSO PÚBLICO Nº 01/2026 do Departamento Municipal de Água e Esgoto  DMAE Uberlândia"
    )
    resultado = script._resolver_municipio_uf(item, edital)

    assert resultado == ("Uberlândia", "MG", 3170206)


def test_resolver_municipio_uf_devolve_none_quando_nada_bate_ibge(monkeypatch):
    # sem risco de gravar município errado: se nenhum candidato (listagem
    # nem sufixos do edital) bate contra o IBGE em MG/SP, devolve None —
    # quem chama tem que pular o processo, não chutar.
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: None)

    item = _item(municipio=None, titulo="Concurso Público DMAE - 01/2026")
    edital = _edital("Vestibular qualquer sem município nenhum reconhecível")
    assert script._resolver_municipio_uf(item, edital) is None


def test_resolver_municipio_uf_sem_edital_ainda_tenta_municipio_da_listagem(monkeypatch):
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: 123 if (nome, uf) == ("Passos", "MG") else None)

    item = _item(municipio="Passos")
    assert script._resolver_municipio_uf(item, None) == ("Passos", "MG", 123)
