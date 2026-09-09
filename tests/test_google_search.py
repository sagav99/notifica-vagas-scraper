from notifica_vagas_scraper.fontes import google_search


def test_montar_parametros_diario_restringe_recencia_e_backfill_nao():
    diario = google_search.montar_parametros("concurso médico MG")
    backfill = google_search.montar_parametros("concurso médico MG", backfill=True)

    assert diario["q"] == "concurso médico MG"
    assert diario["tbs"] == "qdr:d2"
    assert "tbs" not in backfill


def test_montar_parametros_backfill_com_janela_em_estagios():
    """Achado 2026-09-08: backfill "tudo de uma vez" só tem ruído antigo —
    roda em estágios de recência crescente, começando pelo mais provável de
    ainda estar aberto."""
    semana = google_search.montar_parametros("concurso médico MG", backfill=True, janela="semana")
    mes = google_search.montar_parametros("concurso médico MG", backfill=True, janela="mes")
    trimestre = google_search.montar_parametros("concurso médico MG", backfill=True, janela="trimestre")
    tudo = google_search.montar_parametros("concurso médico MG", backfill=True, janela="tudo")

    assert semana["tbs"] == "qdr:d7"
    assert mes["tbs"] == "qdr:d30"
    assert trimestre["tbs"] == "qdr:d90"
    assert "tbs" not in tudo


def test_montar_parametros_janela_ignorada_fora_do_backfill():
    diario = google_search.montar_parametros("concurso médico MG", backfill=False, janela="semana")
    assert diario["tbs"] == "qdr:d2"


def test_listar_itens_extrai_resultado_valido_e_ignora_incompleto():
    itens = google_search.listar_itens(
        {
            "organic": [
                {
                    "title": "Prefeitura de Paracatu abre concurso para médico",
                    "link": "https://paracatu.mg.gov.br/edital/1",
                    "snippet": "Inscrições abertas",
                    "date": "6 days ago",
                },
                {"title": "sem link"},
            ]
        }
    )

    assert len(itens) == 1
    assert itens[0].resumo == "Inscrições abertas"
    assert itens[0].publicado_em is None


def test_listar_itens_sem_resultados_devolve_lista_vazia():
    assert google_search.listar_itens({}) == []
