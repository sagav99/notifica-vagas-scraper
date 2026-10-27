from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import ses_sc

HTML = (Path(__file__).parent / "fixtures" / "ses_sc" / "listagem.html").read_text(encoding="utf-8")


def test_listar_pss_extrai_numero_cargos_local_e_datas():
    itens = ses_sc.listar_pss(HTML)
    assert len(itens) >= 10
    p042 = next(i for i in itens if i.numero == "042/2026")
    assert p042.cargos == "Médico"
    assert p042.tem_medico
    assert "Florianópolis" in p042.local
    assert p042.inscricoes_inicio == date(2026, 10, 21)
    assert p042.inscricoes_fim == date(2026, 11, 4)
    assert p042.url.startswith("https://www.saude.sc.gov.br/")
    assert "&amp;" not in p042.url
    p041 = next(i for i in itens if i.numero == "041/2026")
    assert not p041.tem_medico  # agente auxiliar / técnico em laboratório


def test_url_edital_pega_download_do_edital():
    html = (
        '<a href="/x/pss-ses-link-categoria/retificacao-edital-pss-042-2026/download">r</a>'
        '<a href="/x/pss-ses-link-categoria/edital-pss-042-2026-medicos/download">e</a>'
    )
    assert ses_sc.url_edital(html).endswith("/edital-pss-042-2026-medicos/download")
    assert ses_sc.url_edital("<a href='/outra'>x</a>") is None


def test_escolher_municipio():
    muns = {"Florianópolis": 1, "Mafra": 2, "Lages": 3}
    assert ses_sc.escolher_municipio("Mafra", muns) == "Mafra"
    assert ses_sc.escolher_municipio("Grande Florianópolis", muns) == "Florianópolis"
    assert ses_sc.escolher_municipio("Mafra e Lages", muns) == "Florianópolis"
    assert ses_sc.escolher_municipio("", muns) == "Florianópolis"
