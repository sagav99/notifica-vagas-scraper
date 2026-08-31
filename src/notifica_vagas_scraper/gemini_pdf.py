"""Extração de cargo/salário/vagas de PDF de edital via Gemini.

Camada de auditoria/leitura sobre o que o scraper coletou, não o scraper em
si (decisão em CLAUDE.md do repo principal) — usada só para fontes onde a
informação básica não existe em HTML (ex: FGV, diferente da IMESO que já
expõe cargo/salário estruturado).

Modelo: Gemini 3.5 Flash-Lite por decisão explícita do usuário — cota da
chave usada é 500 requisições/dia, 15 RPM, 250k TPM (bem mais apertada que
o Flash normal). `_esperar_rate_limit()` garante >=4.5s entre chamadas
nesse processo pra não estourar as 15 RPM quando o script processa vários
concursos numa única execução.
"""

from __future__ import annotations

import base64
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

PROMPT = """Você está lendo um edital de concurso público brasileiro em PDF.
Extraia um objeto JSON com:
- numero_edital (string, ex: "01/2026", ou null)
- orgao (nome do órgão/entidade que abriu o concurso, string ou null)
- inscricoes_inicio (data "AAAA-MM-DD" de início das inscrições, ou null)
- inscricoes_fim (data "AAAA-MM-DD" de fim das inscrições, ou null)
- vagas: lista de vagas, cada uma com:
  - cargo (string)
  - vagas_qtd (int ou null)
  - salario (number, só o valor numérico em reais, sem "R$", ou null)
  - requisitos (string curta ou null)
  - carga_horaria (string ou null)
Responda APENAS com o objeto JSON, sem markdown, sem texto adicional."""


class ErroExtracaoGemini(Exception):
    pass


def extrair_vagas_de_pdf(
    pdf_bytes: bytes, *, api_key: str | None = None, modelo: str = MODELO_PADRAO
) -> dict:
    """Retorna {"numero_edital", "orgao", "inscricoes_inicio",
    "inscricoes_fim", "vagas": [{"cargo", "vagas_qtd", "salario",
    "requisitos", "carga_horaria"}, ...]} — ver PROMPT."""
    chave = api_key or os.environ.get("GEMINI_API_KEY")
    if not chave:
        raise ErroExtracaoGemini("GEMINI_API_KEY não definida.")

    body = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "application/pdf",
                            "data": base64.b64encode(pdf_bytes).decode(),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0},
    }

    _esperar_rate_limit()
    resposta = requests.post(
        URL_API.format(modelo=modelo), params={"key": chave}, json=body, timeout=90
    )
    resposta.raise_for_status()
    dados = resposta.json()

    try:
        texto = dados["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ErroExtracaoGemini(f"Resposta inesperada do Gemini: {dados}") from exc

    texto_limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    try:
        return json.loads(texto_limpo)
    except json.JSONDecodeError as exc:
        raise ErroExtracaoGemini(f"JSON inválido do Gemini: {texto[:500]}") from exc
