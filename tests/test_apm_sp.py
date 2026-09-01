from notifica_vagas_scraper.fontes import apm_sp


def test_lista_7_entidades_reais_confirmadas():
    entidades = apm_sp.listar_entidades_apm_sp()
    assert len(entidades) == 7
    assert all(e.uf == "SP" for e in entidades)
    nomes = {e.nome for e in entidades}
    assert nomes == {
        "Barra do Turvo",
        "Bocaina",
        "Irapuã",
        "Itu",
        "Itupeva",
        "Mococa",
        "Taquarituba",
    }


def test_devolve_copia_nao_a_lista_original():
    entidades = apm_sp.listar_entidades_apm_sp()
    entidades.pop()
    assert len(apm_sp.listar_entidades_apm_sp()) == 7
