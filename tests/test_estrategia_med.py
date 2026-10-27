from datetime import date
from pathlib import Path

from notifica_vagas_scraper.fontes import estrategia_med as em

FIX = Path(__file__).parent / "fixtures" / "estrategia_med"


def test_parsear_artigo_periodo_edital_e_uf():
    a = em.parsear_artigo((FIX / "artigo_cianorte.html").read_text(encoding="utf-8"))
    assert a.inscricoes_inicio == date(2026, 7, 27)
    assert a.inscricoes_fim == date(2026, 8, 25)
    assert a.edital_url.endswith("cianorte_edital2026.pdf")
    assert a.uf == "PR"


def test_periodo_variantes():
    h = '<p>Período de Inscrições 06/10 a 03/11/2026</p><a href="https://x/e.pdf">ACESSE AQUI O EDITAL!</a>'
    a = em.parsear_artigo(h)
    assert (a.inscricoes_inicio, a.inscricoes_fim) == (date(2026, 10, 6), date(2026, 11, 3))
    assert a.edital_url == "https://x/e.pdf"
    prosa = "<p>As inscrições estão previstas para acontecer entre os dias 25 de fevereiro e 26 de março de 2026.</p>"
    b = em.parsear_artigo(prosa)
    assert (b.inscricoes_inicio, b.inscricoes_fim) == (date(2026, 2, 25), date(2026, 3, 26))
    virada = em.parsear_artigo("<p>Período de Inscrição 20/12 a 15/01/2027</p>")
    assert virada.inscricoes_inicio == date(2026, 12, 20)


def test_feed_e_uf_do_titulo():
    itens = em.listar_feed((FIX / "feed.xml").read_text(encoding="utf-8"))
    assert len(itens) == 6 and all(i.url.startswith("https://") for i in itens)
    assert em.uf_do_titulo("Ivoti (RS) abre concurso") == "RS"
    assert em.uf_do_titulo("Prefeitura de Cambé/PR lança concurso") == "PR"
    assert em.uf_do_titulo("Foz do Iguaçu publica concurso") is None
