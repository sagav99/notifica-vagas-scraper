from unittest.mock import Mock

import requests

import rodar_descoberta_google_search as script
from notifica_vagas_scraper.fontes import google_search


def _item(titulo="Prefeitura de Paracatu abre concurso para médico", link="https://jornallocal.com.br/noticia/1"):
    return google_search.ItemBusca(titulo=titulo, link=link, resumo="resumo", publicado_em=None)


def test_buscar_itens_erro_isolado_nao_impede_demais_queries(monkeypatch):
    monkeypatch.setattr(script.google_search, "QUERIES", ("erro", "ok"))

    class Resposta:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"title": "Concurso médico em Paracatu", "link": "https://exemplo.test/1"}]}

    def fake_get(url, *, params, headers, timeout):
        if params["q"] == "erro":
            raise requests.RequestException("falha simulada")
        return Resposta()

    monkeypatch.setattr(script.requests, "get", fake_get)
    itens = script.buscar_itens(api_key="chave", engine_id="cx")

    assert len(itens) == 1
    assert itens[0].link == "https://exemplo.test/1"


def test_buscar_itens_para_apos_tres_falhas_consecutivas_sem_vazar_chave(monkeypatch, capsys):
    monkeypatch.setattr(script.google_search, "QUERIES", ("a", "b", "c", "nao-deve-rodar"))
    chamadas = []

    def fake_get(url, *, params, headers, timeout):
        chamadas.append(params["q"])
        resposta = Mock(status_code=403, url=f"{url}?key={params['key']}")
        erro = requests.HTTPError("403 Client Error", response=resposta)
        raise erro

    monkeypatch.setattr(script.requests, "get", fake_get)

    assert script.buscar_itens(api_key="segredo-nao-vaza", engine_id="cx") == []
    saida = capsys.readouterr().err
    assert chamadas == ["a", "b", "c"]
    assert "segredo-nao-vaza" not in saida
    assert "HTTP 403" in saida


def test_dominio_conhecido_para_no_sinal_sem_chamar_gemini(monkeypatch):
    conn = Mock()
    sinais = []
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: sinais.append(kw) or True)

    chamou_gemini = False

    def fake_extrair(*args, **kwargs):
        nonlocal chamou_gemini
        chamou_gemini = True
        return {"vagas": []}

    monkeypatch.setattr(script.gemini_texto, "extrair_vagas_de_texto", fake_extrair)
    resultado = script.processar_item(
        conn, _item(link="https://banca.test/edital"), "Paracatu", "MG", 3106200,
        {"banca.test"}, "chave-gemini-dedicada"
    )

    assert resultado == (1, 0)
    assert not chamou_gemini
    assert sinais[0]["coberto_por_fonte_oficial"] is True


def test_dominio_com_www_eh_reconhecido_como_fonte_conhecida(monkeypatch):
    conn = Mock()
    sinal = {}
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: sinal.update(kw) or True)
    monkeypatch.setattr(script.gemini_texto, "extrair_vagas_de_texto", Mock())

    resultado = script.processar_item(
        conn, _item(link="https://www.banca.test/edital"), "Paracatu", "MG", 3106200,
        {"banca.test"}, "chave-gemini-dedicada"
    )

    assert resultado == (1, 0)
    assert sinal["coberto_por_fonte_oficial"] is True
    script.gemini_texto.extrair_vagas_de_texto.assert_not_called()


def test_sinal_ja_existente_nao_reextrai_com_gemini(monkeypatch):
    conn = Mock()
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: False)
    extrair = Mock()
    monkeypatch.setattr(script.gemini_texto, "extrair_vagas_de_texto", extrair)

    resultado = script.processar_item(conn, _item(), "Paracatu", "MG", 3106200, set(), "chave-dedicada")

    assert resultado == (0, 0)
    extrair.assert_not_called()


def test_dominio_novo_extrai_com_chave_gemini_dedicada_e_grava(monkeypatch):
    conn = Mock()
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(script, "buscar_texto_pagina", lambda url: "texto do edital")
    monkeypatch.setattr(script.db, "upsert_fonte", lambda conn, **kw: "fonte-google-search")
    chamadas = []
    monkeypatch.setattr(
        script.gemini_texto,
        "extrair_vagas_de_texto",
        lambda titulo, texto, *, api_key: chamadas.append(api_key) or {
            "orgao": None, "numero_edital": "01/2026", "tipo_oportunidade": "concurso_efetivo",
            "data_publicacao": None, "inscricoes_inicio": None, "inscricoes_fim": None,
            "vagas": [{"cargo": "Médico Clínico Geral", "salario": 12000, "salario_tipo": "mensal"}],
        },
    )
    inserido = {}
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda conn, **kw: inserido.update(kw) or {"vaga_id": "v1", "evidencia_id": "e1"})

    resultado = script.processar_item(conn, _item(), "Paracatu", "MG", 3106200, set(), "chave-dedicada")

    assert resultado == (1, 1)
    assert chamadas == ["chave-dedicada"]
    assert inserido["cargo"] == "Médico Clínico Geral"
    assert inserido["orgao"] == "Prefeitura Municipal de Paracatu/MG"


def test_backfill_nao_envia_filtro_de_recencia(monkeypatch):
    monkeypatch.setattr(script.google_search, "QUERIES", ("concurso médico",))
    parametros = []

    class Resposta:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": []}

    def fake_get(url, *, params, headers, timeout):
        parametros.append(params)
        return Resposta()

    monkeypatch.setattr(script.requests, "get", fake_get)
    script.buscar_itens(api_key="chave", engine_id="cx", backfill=True)

    assert "dateRestrict" not in parametros[0]
