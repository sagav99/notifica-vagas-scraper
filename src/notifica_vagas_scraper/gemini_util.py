"""Utilitário compartilhado por gemini_pdf.py, gemini_texto.py e
revisao_ia.py para extrair o objeto JSON de uma resposta em texto do
Gemini.

Achado analisando vagas rejeitadas em produção (2026-09-01): algumas
respostas do Gemini vinham com barra invertida solta dentro de um valor
de string (ex: unidade de medida ou trecho copiado do texto original com
"\\" que não é escape JSON válido) — `json.loads` explode com "Invalid
\\uXXXX escape" e a chamada inteira virava erro tratado como "rejeitada
por padrão", descartando uma vaga real por causa de um problema de
escaping trivial, não de conteúdo genuinamente ruim.
"""

from __future__ import annotations

import json
import re

_CERCA_MARKDOWN = re.compile(r"^```(?:json)?\s*|\s*```$")
_ESCAPE_INVALIDO = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def parsear_json_resposta(texto: str) -> dict:
    """Remove cerca de markdown (```json ... ```), se presente, escapa
    qualquer backslash que não inicie um escape JSON válido e faz o
    parse. Levanta json.JSONDecodeError se o resultado ainda assim não
    for JSON válido (deixa o chamador decidir o que fazer)."""
    limpo = _CERCA_MARKDOWN.sub("", texto.strip())
    sanitizado = _ESCAPE_INVALIDO.sub(r"\\\\", limpo)
    return json.loads(sanitizado)
