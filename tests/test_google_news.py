from datetime import datetime, timedelta, timezone

from notifica_vagas_scraper.fontes import google_news

RSS_AMOSTRA = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>"concurso médico MG" - Google News</title>
  <item>
    <title>Prefeitura de Paracatu abre concurso para médico - Jornal Local</title>
    <link>https://news.google.com/rss/articles/CBMi123?oc=5</link>
    <guid isPermaLink="false">abc123</guid>
    <pubDate>{recente}</pubDate>
    <source url="https://jornallocal.com.br">Jornal Local</source>
  </item>
  <item>
    <title>Notícia antiga sobre concurso de médico</title>
    <link>https://news.google.com/rss/articles/CBMi999?oc=5</link>
    <guid isPermaLink="false">def456</guid>
    <pubDate>{antiga}</pubDate>
    <source url="https://outrojornal.com.br">Outro Jornal</source>
  </item>
  <item>
    <title>Item sem data de publicação</title>
    <link>https://news.google.com/rss/articles/CBMi000?oc=5</link>
  </item>
</channel>
</rss>"""


def _rfc822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def test_montar_url_busca_codifica_espacos_e_acentos():
    url = google_news.montar_url_busca("concurso médico MG")
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "hl=pt-BR" in url
    assert "gl=BR" in url
    assert "%20" in url or "+" in url  # espaço codificado de alguma forma


def test_listar_itens_extrai_titulo_link_data_e_fonte():
    agora = datetime.now(timezone.utc)
    xml = RSS_AMOSTRA.format(recente=_rfc822(agora), antiga=_rfc822(agora - timedelta(days=30)))
    itens = google_news.listar_itens(xml)
    assert len(itens) == 3
    primeiro = itens[0]
    assert primeiro.titulo == "Prefeitura de Paracatu abre concurso para médico - Jornal Local"
    assert primeiro.link == "https://news.google.com/rss/articles/CBMi123?oc=5"
    assert primeiro.fonte_nome == "Jornal Local"
    assert primeiro.publicado_em is not None
    # item sem <pubDate> não quebra o parse, só fica com publicado_em=None
    assert itens[2].publicado_em is None


def test_listar_itens_xml_malformado_devolve_lista_vazia():
    assert google_news.listar_itens("<rss><item><title>sem fechar") == []


def test_filtrar_recentes_exclui_item_fora_da_janela_e_sem_data():
    agora = datetime.now(timezone.utc)
    xml = RSS_AMOSTRA.format(recente=_rfc822(agora), antiga=_rfc822(agora - timedelta(days=30)))
    itens = google_news.listar_itens(xml)
    recentes = google_news.filtrar_recentes(itens, dentro_de_dias=2, agora=agora)
    assert len(recentes) == 1
    assert recentes[0].link == "https://news.google.com/rss/articles/CBMi123?oc=5"
