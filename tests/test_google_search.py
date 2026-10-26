from notifica_vagas_scraper.fontes import google_search


def test_montar_parametros_diario_restringe_recencia_e_backfill_nao():
    diario = google_search.montar_parametros("concurso médico MG")
    backfill = google_search.montar_parametros("concurso médico MG", backfill=True)

    assert diario["q"] == "concurso médico MG"
    assert diario["tbs"] == "qdr:d3"
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
    assert diario["tbs"] == "qdr:d3"


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


def test_orcamento_semanal_serper_nao_passa_de_30():
    semana = 3 * len(google_search.QUERIES[: google_search.QUERIES_POR_EXECUCAO]) + 2 * len(google_search.QUERIES_SUL)
    assert semana <= 30


def test_janela_sul_maior_que_a_base():
    assert google_search.montar_parametros("x", janela_dias=google_search.JANELA_DIAS_SUL)["tbs"] == "qdr:d4"
