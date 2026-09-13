from notifica_vagas_scraper import deteccao_bloqueio as db_


def test_detecta_marcador_cloudflare_conhecido():
    corpo = "<html><head><title>Just a moment...</title></head></html>"
    motivo = db_.detectar_bloqueio_html(status_code=403, corpo=corpo)
    assert motivo is not None
    assert "just a moment" in motivo.lower()


def test_detecta_status_suspeito_com_corpo_curto():
    motivo = db_.detectar_bloqueio_html(status_code=403, corpo="forbidden")
    assert motivo is not None
    assert "403" in motivo


def test_pagina_normal_nao_e_marcada_como_bloqueio():
    corpo = "<html><body>" + ("Edital de concurso público. " * 200) + "</body></html>"
    assert db_.detectar_bloqueio_html(status_code=200, corpo=corpo) is None


def test_erro_generico_404_com_corpo_grande_nao_e_bloqueio():
    corpo = "<html><body>Página não encontrada. " + ("conteúdo irrelevante " * 200) + "</body></html>"
    assert db_.detectar_bloqueio_html(status_code=404, corpo=corpo) is None


def test_parece_pdf_valido():
    assert db_.parece_pdf_valido(b"%PDF-1.7\n...") is True
    assert db_.parece_pdf_valido(b"<html>nao e pdf</html>") is False


def test_detectar_bloqueio_pdf_aceita_pdf_valido():
    assert db_.detectar_bloqueio_pdf(status_code=200, conteudo=b"%PDF-1.7\nresto do arquivo") is None


def test_detectar_bloqueio_pdf_marca_html_de_desafio_como_bloqueio():
    conteudo = "Just a moment... verificando seu navegador".encode()
    motivo = db_.detectar_bloqueio_pdf(status_code=403, conteudo=conteudo)
    assert motivo is not None
    assert "just a moment" in motivo.lower()


def test_detectar_bloqueio_pdf_marca_conteudo_generico_nao_pdf():
    motivo = db_.detectar_bloqueio_pdf(status_code=200, conteudo=b"nao e um pdf nem tem marcador conhecido")
    assert motivo is not None
    assert "não é um pdf válido" in motivo.lower()
