from unittest.mock import Mock

import rodar_descoberta_google_news as script
from notifica_vagas_scraper.fontes import google_news


def _item(titulo="Prefeitura de Paracatu abre concurso para médico", link="https://news.google.com/rss/articles/x"):
    return google_news.ItemNoticia(titulo=titulo, link=link, publicado_em=None, fonte_nome="Jornal Local")


def test_dominio_ja_coberto_registra_sinal_mas_nao_chama_gemini(monkeypatch):
    conn_falso = Mock()
    monkeypatch.setattr(script, "resolver_url_final", lambda link: "https://www.ibgpconcursos.com.br/concurso/1")

    sinais = []
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: sinais.append(kw) or True)

    chamou_gemini = False

    def _fake_extrair(*a, **k):
        nonlocal chamou_gemini
        chamou_gemini = True
        return {"vagas": []}

    monkeypatch.setattr(script.gemini_texto, "extrair_vagas_de_texto", _fake_extrair)

    total = script.processar_item(
        conn_falso, _item(), "Paracatu", "MG", 3106200, dominios_conhecidos={"www.ibgpconcursos.com.br"}
    )

    assert total == 0
    assert not chamou_gemini
    assert len(sinais) == 1
    assert sinais[0]["coberto_por_fonte_oficial"] is True
    assert sinais[0]["municipio_id"] == 3106200


def test_dominio_desconhecido_extrai_vaga_via_gemini_e_grava(monkeypatch):
    conn_falso = Mock()
    monkeypatch.setattr(script, "resolver_url_final", lambda link: "https://jornallocal.com.br/noticia/123")
    monkeypatch.setattr(script, "buscar_texto_pagina", lambda url: "texto do artigo com detalhes do concurso")

    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(
        script.db,
        "upsert_fonte",
        lambda conn, *, nome, url, tipo, uf: "fonte-google-news-mg",
    )

    monkeypatch.setattr(
        script.gemini_texto,
        "extrair_vagas_de_texto",
        lambda titulo, texto: {
            "orgao": None,
            "numero_edital": "01/2026",
            "tipo_oportunidade": "concurso_efetivo",
            "data_publicacao": None,
            "inscricoes_inicio": None,
            "inscricoes_fim": None,
            "vagas": [{"cargo": "Médico Clínico Geral", "salario": 12000, "salario_tipo": "mensal"}],
        },
    )

    inserido = {}
    monkeypatch.setattr(
        script.db,
        "inserir_vaga_com_evidencia",
        lambda conn, **kw: inserido.update(kw) or {"vaga_id": "v1", "evidencia_id": "e1"},
    )

    total = script.processar_item(conn_falso, _item(), "Paracatu", "MG", 3106200, dominios_conhecidos=set())

    assert total == 1
    assert inserido["cargo"] == "Médico Clínico Geral"
    assert inserido["fonte_id"] == "fonte-google-news-mg"
    assert inserido["municipio_id"] == 3106200
    assert inserido["orgao"] == "Prefeitura Municipal de Paracatu/MG"


def test_gemini_sem_vaga_extraivel_nao_grava_nada(monkeypatch):
    conn_falso = Mock()
    monkeypatch.setattr(script, "resolver_url_final", lambda link: "https://jornallocal.com.br/noticia/123")
    monkeypatch.setattr(script, "buscar_texto_pagina", lambda url: "notícia sem detalhe de vaga nenhum")
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(script.gemini_texto, "extrair_vagas_de_texto", lambda titulo, texto: {"vagas": []})

    chamou_insercao = False

    def _fake_inserir(*a, **k):
        nonlocal chamou_insercao
        chamou_insercao = True

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_item(conn_falso, _item(), "Paracatu", "MG", 3106200, dominios_conhecidos=set())

    assert total == 0
    assert not chamou_insercao


def test_buscar_itens_recentes_deduplica_por_link(monkeypatch):
    xml_com_item = """<rss><channel><item>
        <title>Concurso médico em Paracatu</title>
        <link>https://news.google.com/rss/articles/dup</link>
        <pubDate>{data}</pubDate>
    </item></channel></rss>"""
    from datetime import datetime, timezone

    agora_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    respostas = []

    class _Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def _fake_get(url, headers=None, timeout=None):
        respostas.append(url)
        return _Resp(xml_com_item.format(data=agora_str))

    monkeypatch.setattr(script.requests, "get", _fake_get)

    itens = script.buscar_itens_recentes()

    # mesmo link retornado por várias queries diferentes -> só 1 item único
    assert len(itens) == 1
    assert len(respostas) == len(script.google_news.QUERIES)
