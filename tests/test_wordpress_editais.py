from datetime import date

from notifica_vagas_scraper.fontes import wordpress_editais as wp


class _RespostaFalsa:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise wp.requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _post_bruto(id_=1, titulo="Edital 01/2026", data_iso="2026-07-10T15:42:02", conteudo=""):
    return {
        "id": id_,
        "title": {"rendered": titulo},
        "date": data_iso,
        "link": f"https://exemplo.mg.gov.br/post-{id_}/",
        "content": {"rendered": conteudo},
    }


def test_buscar_posts_parseia_campos_reais(monkeypatch):
    monkeypatch.setattr(
        wp.requests, "get", lambda *a, **k: _RespostaFalsa([_post_bruto()])
    )
    posts = wp.buscar_posts("https://exemplo.mg.gov.br", "processo seletivo")
    assert len(posts) == 1
    assert posts[0].titulo == "Edital 01/2026"
    assert posts[0].data == date(2026, 7, 10)


def test_buscar_posts_erro_de_rede_devolve_lista_vazia(monkeypatch):
    def _levanta(*a, **k):
        raise wp.requests.exceptions.ConnectionError("falha de rede")

    monkeypatch.setattr(wp.requests, "get", _levanta)
    assert wp.buscar_posts("https://exemplo.mg.gov.br", "edital") == []


def test_buscar_posts_resposta_nao_e_lista_devolve_vazio(monkeypatch):
    monkeypatch.setattr(
        wp.requests, "get", lambda *a, **k: _RespostaFalsa({"code": "rest_no_route"}, status_code=404)
    )
    assert wp.buscar_posts("https://exemplo.mg.gov.br", "edital") == []


def test_extrair_pdfs_deduplicado_preservando_ordem():
    html = (
        '<a href="https://x.gov.br/a.pdf">A</a>'
        '<a href="https://x.gov.br/b.pdf">B (baixar)</a>'
        '<a href="https://x.gov.br/a.pdf">A de novo</a>'
    )
    assert wp.extrair_pdfs(html) == [
        "https://x.gov.br/a.pdf",
        "https://x.gov.br/b.pdf",
    ]


def test_extrair_pdfs_sem_pdf_devolve_vazio():
    assert wp.extrair_pdfs("<p>sem anexo nenhum</p>") == []


def test_escolher_pdf_edital_prioriza_nome_com_edital():
    urls = [
        "https://x.gov.br/portaria.pdf",
        "https://x.gov.br/Edital-12-2026.pdf",
        "https://x.gov.br/resultado.pdf",
    ]
    assert wp.escolher_pdf_edital(urls) == "https://x.gov.br/Edital-12-2026.pdf"


def test_escolher_pdf_edital_sem_nome_edital_usa_primeiro():
    urls = ["https://x.gov.br/portaria.pdf", "https://x.gov.br/resultado.pdf"]
    assert wp.escolher_pdf_edital(urls) == "https://x.gov.br/portaria.pdf"


def test_escolher_pdf_edital_lista_vazia_devolve_none():
    assert wp.escolher_pdf_edital([]) is None


def test_listar_municipios_wordpress_le_csv_real():
    municipios = wp.listar_municipios_wordpress()
    assert len(municipios) == 11
    nomes = {m.nome for m in municipios}
    assert "Estiva" in nomes
    assert "Ferraz de Vasconcelos" in nomes
