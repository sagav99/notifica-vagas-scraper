from datetime import date

from notifica_vagas_scraper import light

HOJE = date(2026, 10, 26)


def _extraido(**extra):
    base = {"orgao": "Prefeitura de X", "municipio": "X", "uf": "MG", "inscricoes_fim": "2026-11-10",
            "vagas": [{"cargo": "Enfermeiro ESF", "vagas_qtd": 3, "salario": 5000},
                      {"cargo": "Médico", "vagas_qtd": 1, "salario": 15000}]}
    base.update(extra)
    return base


def test_filtra_so_cargos_da_area():
    linhas = light.montar_linhas(_extraido(), "enfermagem", hoje=HOJE, url="u", titulo="t")
    assert [l["cargo"] for l in linhas] == ["Enfermeiro ESF"]
    assert linhas[0]["numero_vagas"] == 3 and linhas[0]["salario"] == 5000


def test_cargo_com_acento_e_tecnico():
    assert light.cargo_da_area("Técnico em Enfermagem", "enfermagem")
    assert light.cargo_da_area("ENGENHEIRO CIVIL", "engenharia")
    assert not light.cargo_da_area("Engenheiro", "enfermagem")


def test_inscricao_encerrada_nao_entra():
    assert light.montar_linhas(_extraido(inscricoes_fim="2026-10-27"), "enfermagem", hoje=HOJE, url="u", titulo="t") == []


def test_sem_data_fim_entra():
    assert len(light.montar_linhas(_extraido(inscricoes_fim=None), "enfermagem", hoje=HOJE, url="u", titulo="t")) == 1


def test_valores_invalidos_viram_none():
    ex = _extraido(vagas=[{"cargo": "Engenheiro Civil", "vagas_qtd": "dois", "salario": True}])
    l = light.montar_linhas(ex, "engenharia", hoje=HOJE, url="u", titulo="t")[0]
    assert l["numero_vagas"] is None and l["salario"] is None


def test_dedup_por_cargo():
    ex = _extraido(vagas=[{"cargo": "Enfermeiro"}, {"cargo": "enfermeiro"}])
    assert len(light.montar_linhas(ex, "enfermagem", hoje=HOJE, url="u", titulo="t")) == 1


def test_resposta_em_lista_nao_quebra():
    assert light.montar_linhas([{"cargo": "Enfermeiro"}], "enfermagem", hoje=HOJE, url="u", titulo="t")[0]["cargo"] == "Enfermeiro"
    assert light.montar_linhas([_extraido()], "enfermagem", hoje=HOJE, url="u", titulo="t")[0]["cargo"] == "Enfermeiro ESF"
    assert light.montar_linhas("lixo", "enfermagem", hoje=HOJE, url="u", titulo="t") == []


def test_prazo_minimo_de_5_dias():
    assert light.montar_linhas(_extraido(inscricoes_fim="2026-10-31"), "enfermagem", hoje=HOJE, url="u", titulo="t")
    assert not light.montar_linhas(_extraido(inscricoes_fim="2026-10-30"), "enfermagem", hoje=HOJE, url="u", titulo="t")


def test_ti_cargos_e_exclusoes():
    for c in ("Analista de Sistemas", "Desenvolvedor Full Stack", "Analista de TI - Redes", "Analista de Segurança da Informação", "Cientista de Dados", "Programador"):
        assert light.cargo_da_area(c, "ti"), c
    for c in ("Professor de Informática", "Estagiário de TI", "Enfermeiro", "Analista Administrativo"):
        assert not light.cargo_da_area(c, "ti"), c
    assert not light.cargo_da_area("Monitor de Engenharia", "engenharia")
    assert light.cargo_da_area("Engenheiro de Segurança do Trabalho", "engenharia")
