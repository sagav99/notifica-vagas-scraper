from pathlib import Path

import pytest

from notifica_vagas_scraper import evidencia_imagem

FIXTURE = Path(__file__).parent / "fixtures" / "itamonte_mg" / "ps_001_2026_edital_original.pdf"


def _pdf_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_renderizar_pagina_pdf_devolve_png_valido():
    imagem = evidencia_imagem.renderizar_pagina_pdf(_pdf_bytes(), 1)
    assert imagem[:8] == b"\x89PNG\r\n\x1a\n"


def test_renderizar_pagina_pdf_pagina_fora_do_intervalo_levanta_erro():
    with pytest.raises(evidencia_imagem.ErroImagemEvidencia):
        evidencia_imagem.renderizar_pagina_pdf(_pdf_bytes(), 9999)


def test_renderizar_pagina_pdf_zero_levanta_erro():
    with pytest.raises(evidencia_imagem.ErroImagemEvidencia):
        evidencia_imagem.renderizar_pagina_pdf(_pdf_bytes(), 0)


def test_renderizar_pagina_pdf_bytes_invalidos_levanta_erro():
    with pytest.raises(evidencia_imagem.ErroImagemEvidencia):
        evidencia_imagem.renderizar_pagina_pdf(b"isto nao e um pdf", 1)


class _RespostaFalsa:
    def __init__(self, status_code: int, texto: str = ""):
        self.status_code = status_code
        self.text = texto


def test_subir_print_pagina_sucesso_devolve_url_publica(monkeypatch):
    capturado = {}

    def _post_falso(url, headers, data, timeout):
        capturado["url"] = url
        capturado["headers"] = headers
        capturado["data"] = data
        return _RespostaFalsa(200)

    monkeypatch.setattr(evidencia_imagem.requests, "post", _post_falso)

    resultado = evidencia_imagem.subir_print_pagina(
        b"png-fake", caminho="arealva-pagina-3.png",
        supabase_url="https://exemplo.supabase.co", service_role_key="chave-servico",
    )

    assert resultado == "https://exemplo.supabase.co/storage/v1/object/public/evidencias-pdf/arealva-pagina-3.png"
    assert capturado["url"] == "https://exemplo.supabase.co/storage/v1/object/evidencias-pdf/arealva-pagina-3.png"
    assert capturado["headers"]["Authorization"] == "Bearer chave-servico"
    assert capturado["data"] == b"png-fake"


def test_subir_print_pagina_falha_http_levanta_erro(monkeypatch):
    monkeypatch.setattr(
        evidencia_imagem.requests, "post", lambda *a, **k: _RespostaFalsa(403, "sem permissao")
    )
    with pytest.raises(evidencia_imagem.ErroImagemEvidencia):
        evidencia_imagem.subir_print_pagina(
            b"png-fake", caminho="x.png",
            supabase_url="https://exemplo.supabase.co", service_role_key="chave-servico",
        )
