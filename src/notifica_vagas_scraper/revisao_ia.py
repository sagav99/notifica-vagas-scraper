"""Revisão administrativa automática via Gemini — decide aprovar/rejeitar
uma vaga, 1 chamada por vaga, sem humano no loop (decisão do usuário,
2026-09-01 — cancelou o fluxo anterior de aprovar/rejeitar manual no
painel /admin do repo principal). Ver
docs/revisao_automatica_gemini.md no repo principal para o desenho
completo e como auditar uma decisão.

Limitação aceita nesta v1: nenhuma fonte grava `texto_extraido` hoje (ver
scripts/rodar*.py, sempre `None`) — o Gemini avalia só os campos
estruturados já extraídos (cargo/salário/edital/datas/município/resumo),
não lê o documento original de novo. Reavaliar se aprovação/rejeição
errada virar problema real na prática.

Em caso de qualquer erro ou resposta ambígua do Gemini, a decisão cai
para "rejeitada" (decisão explícita do usuário: nunca aprovar no escuro).
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

MODELO_PADRAO = "gemini-3.5-flash-lite"
URL_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
INTERVALO_MINIMO_ENTRE_CHAMADAS_S = 4.5  # 15 RPM = 1 a cada 4s; margem de segurança

_ultima_chamada: float = 0.0


def _esperar_rate_limit() -> None:
    global _ultima_chamada
    agora = time.monotonic()
    espera = INTERVALO_MINIMO_ENTRE_CHAMADAS_S - (agora - _ultima_chamada)
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada = time.monotonic()


PROMPT_TEMPLATE = """Você audita dados extraídos automaticamente sobre uma vaga de concurso \
público brasileiro, antes dela ficar visível para o usuário final de um site de \
notificação de vagas.

Você NÃO tem acesso ao documento original — avalie só a qualidade e \
consistência interna dos dados abaixo: campos essenciais vazios (cargo \
ausente ou genérico demais, tipo "vaga" sem nome real de cargo), valores \
implausíveis (salário absurdo para cargo público, datas inconsistentes \
como inscrição terminando antes de começar), ou cargo sem nenhuma relação \
plausível com concurso público (indício de erro de extração). Não afirme \
ter consultado a fonte original — você não tem esse acesso.

Dados extraídos:
{dados_json}

Responda APENAS com um objeto JSON, sem markdown, sem texto adicional:
{{"decisao": "aprovada" ou "rejeitada", "motivo": "uma frase curta em \
português explicando a decisão"}}.

Se tiver qualquer dúvida real sobre a qualidade dos dados, responda \
"rejeitada" — nunca aprove no escuro."""


class ErroRevisaoGemini(Exception):
    pass


def decidir_revisao(
    dados: dict, *, api_key: str | None = None, modelo: str = MODELO_PADRAO
) -> dict:
    """dados: campos estruturados da vaga (ver
    scripts/revisar_vagas.py:montar_dados_para_revisao).

    Retorna {"decisao": "aprovada"|"rejeitada", "motivo": str}. Nunca
    levanta por resposta ambígua/erro de rede — nesses casos a decisão é
    "rejeitada" com o motivo explicando a falha, para nunca aprovar uma
    vaga sem uma decisão real do Gemini por trás.
    """
    chave = api_key or os.environ.get("GEMINI_API_KEY")
    if not chave:
        return {
            "decisao": "rejeitada",
            "motivo": "[revisão automática] GEMINI_API_KEY não configurada.",
        }

    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": PROMPT_TEMPLATE.format(
                            dados_json=json.dumps(dados, ensure_ascii=False, default=str)
                        )
                    }
                ]
            }
        ],
        "generationConfig": {"temperature": 0},
    }

    _esperar_rate_limit()
    try:
        resposta = requests.post(
            URL_API.format(modelo=modelo), params={"key": chave}, json=body, timeout=60
        )
        resposta.raise_for_status()
        corpo = resposta.json()
        texto = corpo["candidates"][0]["content"]["parts"][0]["text"]
        texto_limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
        resultado = json.loads(texto_limpo)
    except Exception as exc:
        # Captura ampla e intencional: qualquer falha (rede, HTTP, formato
        # de resposta, JSON inválido) tem que virar rejeição automática,
        # nunca uma exceção que aborta o lote inteiro de revisão.
        return {
            "decisao": "rejeitada",
            "motivo": f"[revisão automática] Erro ao consultar o Gemini, rejeitada por padrão: {exc}",
        }

    decisao = resultado.get("decisao")
    motivo = resultado.get("motivo") or "Sem motivo informado pelo Gemini."
    if decisao not in ("aprovada", "rejeitada"):
        return {
            "decisao": "rejeitada",
            "motivo": (
                "[revisão automática] Resposta do Gemini fora do formato esperado, "
                f"rejeitada por padrão: {resultado!r}"
            ),
        }

    return {"decisao": decisao, "motivo": motivo}
