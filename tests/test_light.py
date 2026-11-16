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


def test_consultas_enfermagem_rodam_fixas_mais_estados_em_rodizio():
    from datetime import date

    c1 = light.consultas_da_area("enfermagem", date(2026, 11, 16))
    c2 = light.consultas_da_area("enfermagem", date(2026, 11, 18))
    assert len(c1) == 2 + light.UFS_ROTATIVAS_POR_EXECUCAO
    assert c1[:2] == light.AREAS["enfermagem"]["queries"]
    assert set(c1[2:]).isdisjoint(c2[2:])  # execução seguinte (2 dias depois) pega outros estados


def test_consultas_de_engenharia_nao_mudam():
    from datetime import date

    assert light.consultas_da_area("engenharia", date(2026, 11, 16)) == light.AREAS["engenharia"]["queries"]


def test_mesclar_com_edital_completa_sem_sobrescrever_nem_usar_plantao():
    pagina = {
        "orgao": "Pref. X", "municipio": None, "uf": "MG", "inscricoes_fim": None,
        "vagas": [{"cargo": "Enfermeiro", "vagas_qtd": None, "salario": None},
                  {"cargo": "Enfermeiro ESF", "vagas_qtd": 2, "salario": 3000}],
    }
    edital = {
        "orgao": "Outro", "municipio": "Xis", "uf": "SP", "inscricoes_fim": "2026-12-01",
        "vagas": [{"cargo": "ENFERMEIRO", "vagas_qtd": 3, "salario": 4500, "salario_tipo": "mensal"},
                  {"cargo": "Enfermeiro ESF", "vagas_qtd": 9, "salario": 99, "salario_tipo": "plantao"}],
    }
    r = light.mesclar_com_edital(pagina, edital)
    assert (r["orgao"], r["municipio"], r["uf"], r["inscricoes_fim"]) == ("Pref. X", "Xis", "MG", "2026-12-01")
    assert r["vagas"][0]["salario"] == 4500 and r["vagas"][0]["vagas_qtd"] == 3
    assert r["vagas"][1]["salario"] == 3000  # não sobrescreve


def test_faltam_dados():
    ok = {"salario": 1.0, "inscricoes_fim": date(2026, 12, 1)}
    assert light.faltam_dados([ok, {"salario": None, "inscricoes_fim": date(2026, 12, 1)}])
    assert not light.faltam_dados([ok])
    assert not light.faltam_dados([])
