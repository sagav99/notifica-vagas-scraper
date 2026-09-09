"""Print de 1 página de PDF, salvo como imagem permanente no Supabase
Storage — resolve achado real de 2026-09-09 (Vigia capturou Médico
Psiquiatra em Arealva/SP corretamente, mas a única forma de conferir era
abrir o Diário Oficial inteiro do município, 821 páginas). O Gemini já lê
o PDF inteiro pra extrair cargo/salário (``gemini_pdf.py``) e agora também
devolve em que página achou cada cargo — esse módulo renderiza só essa
página como PNG (via ``pdfplumber``, que já é dependência direta do
projeto e já traz ``pypdfium2``/Pillow como dependência transitiva, sem
precisar adicionar nada novo em ``pyproject.toml``) e sobe pro bucket
público ``evidencias-pdf`` (migration 017, repo principal).

Bucket público por decisão de produto: documento de concurso público já é
informação pública, então servir a imagem direto sem URL assinada
simplifica o catálogo/painel admin. Só escreve quem tiver a service role
key (ignora RLS) — nenhuma policy de insert/update/delete existe pra esse
bucket de propósito.
"""

from __future__ import annotations

import io

import pdfplumber
import requests

RESOLUCAO_PADRAO = 150


class ErroImagemEvidencia(Exception):
    pass


def renderizar_pagina_pdf(pdf_bytes: bytes, pagina: int, *, resolucao: int = RESOLUCAO_PADRAO) -> bytes:
    """Renderiza a página ``pagina`` (1-indexada, igual o Gemini devolve)
    de um PDF como PNG. Levanta ``ErroImagemEvidencia`` se a página não
    existir (PDF menor do que o Gemini indicou) ou o PDF for ilegível."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if not (1 <= pagina <= len(pdf.pages)):
                raise ErroImagemEvidencia(
                    f"página {pagina} fora do intervalo (PDF tem {len(pdf.pages)} página(s))"
                )
            imagem = pdf.pages[pagina - 1].to_image(resolution=resolucao)
            buffer = io.BytesIO()
            imagem.save(buffer, format="PNG")
            return buffer.getvalue()
    except ErroImagemEvidencia:
        raise
    except Exception as exc:  # pdfplumber/pypdfium2 levantam tipos variados p/ PDF corrompido
        raise ErroImagemEvidencia(f"falha renderizando página {pagina}: {exc}") from exc


def subir_print_pagina(
    imagem_png: bytes, *, caminho: str, supabase_url: str, service_role_key: str
) -> str:
    """Sobe a imagem pro bucket ``evidencias-pdf`` e devolve a URL pública
    (o bucket é ``public=true``, então a URL funciona sem autenticação).
    ``x-upsert`` evita erro de conflito se o mesmo caminho já existir
    (reprocessamento do mesmo documento/página)."""
    resposta = requests.post(
        f"{supabase_url}/storage/v1/object/evidencias-pdf/{caminho}",
        headers={
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
            "Content-Type": "image/png",
            "x-upsert": "true",
        },
        data=imagem_png,
        timeout=30,
    )
    if resposta.status_code >= 400:
        raise ErroImagemEvidencia(f"falha no upload (HTTP {resposta.status_code}): {resposta.text[:300]}")
    return f"{supabase_url}/storage/v1/object/public/evidencias-pdf/{caminho}"
