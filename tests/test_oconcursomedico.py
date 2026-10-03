from datetime import date

from notifica_vagas_scraper.fontes import oconcursomedico as ocm


def _item(**extra):
    base = {
        "id": 1,
        "titulo": "Concurso da Prefeitura de Conchal",
        "cidade": "Conchal",
        "uf": "SP",
        "status": "abertas",
        "inscricaoInicio": "04/09/2026",
        "inscricaoFim": "07/10/2026",
        "editais": [{"nome": "Edital", "link": "https://exemplo.org/edital.pdf"}],
    }
    base.update(extra)
    return base


def test_lista_so_mg_sp_abertos_ou_previstos():
    feed = {
        "concursos": [
            _item(id=1),
            _item(id=2, uf="PR"),
            _item(id=3, status="encerradas"),
            _item(id=4, status="previsto", uf="MG"),
        ]
    }
    ids = [c.id for c in ocm.listar_candidatos(feed)]
    assert ids == [1, 4]


def test_parseia_datas_e_link_do_edital():
    cand = ocm.listar_candidatos({"concursos": [_item()]})[0]
    assert cand.inscricoes_inicio == date(2026, 9, 4)
    assert cand.inscricoes_fim == date(2026, 10, 7)
    assert cand.edital_url == "https://exemplo.org/edital.pdf"


def test_edital_sem_link_ou_data_invalida_vira_none():
    cand = ocm.listar_candidatos(
        {"concursos": [_item(editais=[{"nome": "Edital", "link": ""}], inscricaoInicio=None, inscricaoFim="a definir")]}
    )[0]
    assert cand.edital_url is None
    assert cand.inscricoes_inicio is None
    assert cand.inscricoes_fim is None


def test_aceita_json_em_texto():
    assert len(ocm.listar_candidatos('{"concursos": []}')) == 0
