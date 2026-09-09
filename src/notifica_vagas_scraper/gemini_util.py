"""Utilitário compartilhado por gemini_pdf.py, gemini_texto.py e
revisao_ia.py para extrair o objeto JSON de uma resposta em texto do
Gemini, e pelo rate limiter comum às chamadas de API dos 3 módulos.

Achado analisando vagas rejeitadas em produção (2026-09-01): algumas
respostas do Gemini vinham com barra invertida solta dentro de um valor
de string (ex: unidade de medida ou trecho copiado do texto original com
"\\" que não é escape JSON válido) — `json.loads` explode com "Invalid
\\uXXXX escape" e a chamada inteira virava erro tratado como "rejeitada
por padrão", descartando uma vaga real por causa de um problema de
escaping trivial, não de conteúdo genuinamente ruim.

Achado real 2026-09-09 (verificação do Vigia/Serper em produção,
`scripts/rodar_descoberta_google_search.py`): até esta data, cada um dos
3 módulos mantinha seu próprio `_ultima_chamada`/`_esperar_rate_limit`
independente. Isso é inofensivo quando só um módulo é usado por processo
(caso normal do pipeline principal), mas `rodar_descoberta_google_search.py`
chama `gemini_pdf` OU `gemini_texto` conforme cada item tem PDF ou não,
dentro do mesmo processo — como os 2 relógios não se falam, itens
alternados furavam o limite de 15 RPM (gaps reais de <1s entre chamadas
no log de produção, mesmo com os 4.5s respeitados dentro de cada módulo
isoladamente), causando 429 em cascata. Rate limiter centralizado aqui
resolve isso pra qualquer combinação de módulos chamados no mesmo processo.
"""

from __future__ import annotations

import json
import re
import time

_CERCA_MARKDOWN = re.compile(r"^```(?:json)?\s*|\s*```$")
_ESCAPE_INVALIDO = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')

INTERVALO_MINIMO_ENTRE_CHAMADAS_S = 4.5  # 15 RPM = 1 a cada 4s; margem de segurança

_ultima_chamada: float = 0.0


def esperar_rate_limit() -> None:
    """Garante >=4.5s desde a última chamada real ao Gemini feita por
    QUALQUER um dos 3 módulos (gemini_pdf, gemini_texto, revisao_ia) no
    mesmo processo — estado compartilhado de propósito, ver docstring
    do módulo."""
    global _ultima_chamada
    agora = time.monotonic()
    espera = INTERVALO_MINIMO_ENTRE_CHAMADAS_S - (agora - _ultima_chamada)
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada = time.monotonic()


def parsear_json_resposta(texto: str) -> dict:
    """Remove cerca de markdown (```json ... ```), se presente, escapa
    qualquer backslash que não inicie um escape JSON válido e faz o
    parse. Levanta json.JSONDecodeError se o resultado ainda assim não
    for JSON válido (deixa o chamador decidir o que fazer)."""
    limpo = _CERCA_MARKDOWN.sub("", texto.strip())
    sanitizado = _ESCAPE_INVALIDO.sub(r"\\\\", limpo)
    return json.loads(sanitizado)
