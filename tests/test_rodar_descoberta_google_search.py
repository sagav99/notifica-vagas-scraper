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


def test_dominio_conhecido_marca_sinal_coberto_mas_ainda_extrai(monkeypatch):
    """`coberto=True` só descreve o sinal — não pula mais a extração
    (mudou em 2026-09-08: pular fazia essa função de auditoria se anular
    quando a fonte oficial daquele domínio está quebrada)."""
    conn = Mock()
    sinais = []
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: sinais.append(kw) or True)
    monkeypatch.setattr(script, "buscar_pagina_html", lambda url: "<html><body>sem PDF aqui</body></html>")

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
    assert chamou_gemini
    assert sinais[0]["coberto_por_fonte_oficial"] is True


def test_dominio_com_www_eh_reconhecido_como_fonte_conhecida(monkeypatch):
    conn = Mock()
    sinal = {}
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: sinal.update(kw) or True)
    monkeypatch.setattr(script, "buscar_pagina_html", lambda url: "<html><body>sem PDF aqui</body></html>")
    monkeypatch.setattr(script.gemini_texto, "extrair_vagas_de_texto", lambda *a, **kw: {"vagas": []})

    resultado = script.processar_item(
        conn, _item(link="https://www.banca.test/edital"), "Paracatu", "MG", 3106200,
        {"banca.test"}, "chave-gemini-dedicada"
    )

    assert resultado == (1, 0)
    assert sinal["coberto_por_fonte_oficial"] is True


def test_dominio_coberto_com_vaga_nova_gera_alerta_de_cobertura(monkeypatch, capsys):
    """Achado 2026-09-08: se o domínio já tem fonte oficial mas o Vigia
    acha uma vaga que gera uma linha NOVA em `vagas` (não é dedup de algo
    que a fonte oficial já tinha capturado), isso é sinal de falha
    silenciosa na coleta oficial — precisa ficar visível (resumo + log)."""
    conn = Mock()
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(script, "buscar_pagina_html", lambda url: "<html><body>sem PDF aqui</body></html>")
    monkeypatch.setattr(script.db, "upsert_fonte", lambda conn, **kw: "fonte-google-search")
    monkeypatch.setattr(
        script.gemini_texto, "extrair_vagas_de_texto",
        lambda titulo, texto, *, api_key: {
            "vagas": [{"cargo": "Médico Clínico Geral", "salario": 12000, "salario_tipo": "mensal"}],
        },
    )
    inserido = {}
    monkeypatch.setattr(
        script.db, "inserir_vaga_com_evidencia",
        lambda conn, **kw: inserido.update(kw) or {"vaga_id": "v1", "evidencia_id": "e1", "vaga_criada": True},
    )

    resultado = script.processar_item(
        conn, _item(link="https://banca.test/edital"), "Paracatu", "MG", 3106200,
        {"banca.test"}, "chave-gemini-dedicada"
    )

    assert resultado == (1, 1)
    assert "ALERTA cobertura" in inserido["resumo"]
    assert "banca.test" in inserido["resumo"]
    assert "ALERTA cobertura" in capsys.readouterr().out


def test_dominio_coberto_mas_vaga_ja_existente_nao_loga_alerta(monkeypatch, capsys):
    """`resumo` só é gravado quando a vaga é criada agora (dedup reaproveita
    a linha existente sem tocar o resumo) — o log não deve alertar nesse
    caso, mesmo com `coberto=True`."""
    conn = Mock()
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(script, "buscar_pagina_html", lambda url: "<html><body>sem PDF aqui</body></html>")
    monkeypatch.setattr(script.db, "upsert_fonte", lambda conn, **kw: "fonte-google-search")
    monkeypatch.setattr(
        script.gemini_texto, "extrair_vagas_de_texto",
        lambda titulo, texto, *, api_key: {
            "vagas": [{"cargo": "Médico Clínico Geral", "salario": 12000, "salario_tipo": "mensal"}],
        },
    )
    monkeypatch.setattr(
        script.db, "inserir_vaga_com_evidencia",
        lambda conn, **kw: {"vaga_id": "v1", "evidencia_id": "e1", "vaga_criada": False},
    )

    resultado = script.processar_item(
        conn, _item(link="https://banca.test/edital"), "Paracatu", "MG", 3106200,
        {"banca.test"}, "chave-gemini-dedicada"
    )

    assert resultado == (1, 1)
    assert "ALERTA cobertura" not in capsys.readouterr().out


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
    monkeypatch.setattr(script, "buscar_pagina_html", lambda url: "<html><body>texto do edital, sem PDF</body></html>")
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


def test_encontrar_pdf_edital_prioriza_link_com_edital_no_texto():
    html = """
    <a href="/anexos/resultado-2024.pdf">Resultado final</a>
    <a href="/anexos/edital-01-2026.pdf">Edital de Abertura</a>
    """
    assert script.encontrar_pdf_edital(html, "https://prefeitura.test/noticias/1") == (
        "https://prefeitura.test/anexos/edital-01-2026.pdf"
    )


def test_encontrar_pdf_edital_cai_pro_primeiro_pdf_sem_sinal_no_texto():
    html = '<a href="/docs/anexo1.pdf">Anexo I</a><a href="/docs/anexo2.pdf">Anexo II</a>'
    assert script.encontrar_pdf_edital(html, "https://prefeitura.test/") == "https://prefeitura.test/docs/anexo1.pdf"


def test_encontrar_pdf_edital_retorna_none_sem_pdf_na_pagina():
    assert script.encontrar_pdf_edital("<a href='/sobre'>Sobre</a>", "https://prefeitura.test/") is None


def test_item_que_ja_eh_pdf_extrai_direto_sem_buscar_html(monkeypatch):
    conn = Mock()
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(script.db, "upsert_fonte", lambda conn, **kw: "fonte-google-search")

    def fail_buscar_pagina_html(url):
        raise AssertionError("não deveria buscar HTML quando o link já é PDF")

    monkeypatch.setattr(script, "buscar_pagina_html", fail_buscar_pagina_html)
    monkeypatch.setattr(script, "baixar_pdf", lambda url: b"%PDF-bytes")
    chamadas = []
    monkeypatch.setattr(
        script.gemini_pdf,
        "extrair_vagas_de_pdf",
        lambda pdf_bytes, *, api_key: chamadas.append((pdf_bytes, api_key)) or {
            "orgao": "Prefeitura de Paracatu", "numero_edital": "02/2026", "tipo_oportunidade": "concurso_efetivo",
            "vagas": [{"cargo": "Médico ESF", "salario": 15000, "salario_tipo": "mensal"}],
        },
    )
    inserido = {}
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda conn, **kw: inserido.update(kw) or {"vaga_id": "v1", "evidencia_id": "e1"})

    resultado = script.processar_item(
        conn, _item(link="https://prefeitura.test/edital-02-2026.pdf"), "Paracatu", "MG", 3106200, set(), "chave-dedicada"
    )

    assert resultado == (1, 1)
    assert chamadas == [(b"%PDF-bytes", "chave-dedicada")]
    assert inserido["cargo"] == "Médico ESF"
    assert inserido["tipo_documento"] == "pdf"
    assert inserido["url_evidencia"] == "https://prefeitura.test/edital-02-2026.pdf"


def test_html_com_link_de_edital_segue_pro_pdf(monkeypatch):
    conn = Mock()
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(script.db, "upsert_fonte", lambda conn, **kw: "fonte-google-search")
    monkeypatch.setattr(
        script, "buscar_pagina_html",
        lambda url: '<a href="/anexos/edital-01-2026.pdf">Edital de Abertura</a>',
    )
    monkeypatch.setattr(script, "baixar_pdf", lambda url: b"%PDF-bytes" if url.endswith("edital-01-2026.pdf") else None)
    monkeypatch.setattr(
        script.gemini_pdf, "extrair_vagas_de_pdf",
        lambda pdf_bytes, *, api_key: {
            "vagas": [{"cargo": "Médico Plantonista", "salario": 8000, "salario_tipo": "plantao"}],
        },
    )
    extrair_texto = Mock()
    monkeypatch.setattr(script.gemini_texto, "extrair_vagas_de_texto", extrair_texto)
    inserido = {}
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda conn, **kw: inserido.update(kw) or {"vaga_id": "v1", "evidencia_id": "e1"})

    resultado = script.processar_item(
        conn, _item(link="https://prefeitura.test/noticias/concurso"), "Paracatu", "MG", 3106200, set(), "chave-dedicada"
    )

    assert resultado == (1, 1)
    extrair_texto.assert_not_called()
    assert inserido["cargo"] == "Médico Plantonista"
    assert inserido["tipo_documento"] == "pdf"
    assert inserido["url_evidencia"] == "https://prefeitura.test/anexos/edital-01-2026.pdf"


def test_falha_na_extracao_do_pdf_cai_pro_texto_da_pagina(monkeypatch):
    conn = Mock()
    monkeypatch.setattr(script.db, "registrar_sinal_descoberta", lambda conn, **kw: True)
    monkeypatch.setattr(script.db, "upsert_fonte", lambda conn, **kw: "fonte-google-search")
    monkeypatch.setattr(
        script, "buscar_pagina_html",
        lambda url: '<a href="/anexos/edital-01-2026.pdf">Edital de Abertura</a><p>texto de apoio na página</p>',
    )
    monkeypatch.setattr(script, "baixar_pdf", lambda url: b"%PDF-bytes")

    def fake_extrair_pdf(pdf_bytes, *, api_key):
        raise script.gemini_pdf.ErroExtracaoGemini("PDF corrompido")

    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", fake_extrair_pdf)
    monkeypatch.setattr(
        script.gemini_texto, "extrair_vagas_de_texto",
        lambda titulo, texto, *, api_key: {
            "vagas": [{"cargo": "Médico Clínico Geral", "salario": 10000, "salario_tipo": "mensal"}],
        },
    )
    inserido = {}
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda conn, **kw: inserido.update(kw) or {"vaga_id": "v1", "evidencia_id": "e1"})

    resultado = script.processar_item(
        conn, _item(link="https://prefeitura.test/noticias/concurso"), "Paracatu", "MG", 3106200, set(), "chave-dedicada"
    )

    assert resultado == (1, 1)
    assert inserido["tipo_documento"] == "pagina_html"
    assert inserido["url_evidencia"] == "https://prefeitura.test/noticias/concurso"


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
