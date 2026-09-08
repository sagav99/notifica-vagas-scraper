from notifica_vagas_scraper.fontes import google_search


def test_montar_parametros_diario_restringe_recencia_e_backfill_nao():
    diario = google_search.montar_parametros("concurso médico MG", api_key="chave", engine_id="cx")
    backfill = google_search.montar_parametros(
        "concurso médico MG", api_key="chave", engine_id="cx", backfill=True
    )

    assert diario["q"] == "concurso médico MG"
    assert diario["dateRestrict"] == "d2"
    assert "dateRestrict" not in backfill


def test_montar_parametros_backfill_com_janela_em_estagios():
    """Achado 2026-09-08: backfill "tudo de uma vez" só tem ruído antigo —
    roda em estágios de recência crescente, começando pelo mais provável de
    ainda estar aberto."""
    semana = google_search.montar_parametros(
        "concurso médico MG", api_key="chave", engine_id="cx", backfill=True, janela="semana"
    )
    mes = google_search.montar_parametros(
        "concurso médico MG", api_key="chave", engine_id="cx", backfill=True, janela="mes"
    )
    trimestre = google_search.montar_parametros(
        "concurso médico MG", api_key="chave", engine_id="cx", backfill=True, janela="trimestre"
    )
    tudo = google_search.montar_parametros(
        "concurso médico MG", api_key="chave", engine_id="cx", backfill=True, janela="tudo"
    )

    assert semana["dateRestrict"] == "d7"
    assert mes["dateRestrict"] == "m1"
    assert trimestre["dateRestrict"] == "m3"
    assert "dateRestrict" not in tudo


def test_montar_parametros_janela_ignorada_fora_do_backfill():
    diario = google_search.montar_parametros(
        "concurso médico MG", api_key="chave", engine_id="cx", backfill=False, janela="semana"
    )
    assert diario["dateRestrict"] == "d2"


def test_listar_itens_extrai_resultado_valido_e_ignora_incompleto():
    itens = google_search.listar_itens(
        {
            "items": [
                {
                    "title": "Prefeitura de Paracatu abre concurso para médico",
                    "link": "https://paracatu.mg.gov.br/edital/1",
                    "snippet": "Inscrições abertas",
                    "pagemap": {"metatags": [{"article:published_time": "2026-09-05T10:00:00Z"}]},
                },
                {"title": "sem link"},
            ]
        }
    )

    assert len(itens) == 1
    assert itens[0].resumo == "Inscrições abertas"
    assert itens[0].publicado_em is not None


def test_listar_itens_sem_resultados_devolve_lista_vazia():
    assert google_search.listar_itens({}) == []
