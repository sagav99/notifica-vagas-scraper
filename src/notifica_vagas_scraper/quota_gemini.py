"""Rastreador de cota diária compartilhado entre os 3 módulos que chamam a
API do Gemini (gemini_pdf.py, gemini_texto.py, revisao_ia.py) — decisão do
usuário (2026-09-01): ao passar de ~470 chamadas no dia (a cota de
gemini-3.5-flash-lite é 500/dia), trocar pra gemini-3.1-flash-lite, que
tem cota diária própria e separada — ganha ~470 chamadas/dia extras sem
estourar limite de nenhum dos dois modelos.

Contador persiste em arquivo (`/tmp`, efêmero por natureza — cada
execução do cron do GitHub Actions já começa com runner limpo, então não
precisa de lógica extra pra "resetar à meia-noite" além de comparar a
data gravada com a data de hoje). Compartilhado entre processos
diferentes (cada `python scripts/rodar_*.py` é um processo Python novo)
porque os 3 módulos escrevem no mesmo arquivo — sem isso, cada processo
recomeçaria a contagem do zero e nunca trocaria de modelo de verdade.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

CAMINHO_CONTADOR = Path("/tmp/notifica_vagas_gemini_contagem.json")
LIMITE_ANTES_DE_TROCAR = 470

MODELO_PADRAO = "gemini-3.5-flash-lite"
MODELO_FALLBACK = "gemini-3.1-flash-lite"


def _ler_contagem_hoje() -> int:
    if not CAMINHO_CONTADOR.exists():
        return 0
    try:
        dados = json.loads(CAMINHO_CONTADOR.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    if dados.get("data") != date.today().isoformat():
        return 0
    return int(dados.get("contagem", 0))


def proximo_modelo() -> str:
    """Modelo a usar na PRÓXIMA chamada, considerando quantas já foram
    feitas hoje (somando todos os módulos que usam este rastreador)."""
    return MODELO_FALLBACK if _ler_contagem_hoje() >= LIMITE_ANTES_DE_TROCAR else MODELO_PADRAO


def registrar_chamada() -> None:
    """Chamar depois de CADA request de verdade feito à API do modelo
    padrão (sucesso ou erro — a cota é consumida pela tentativa, não só
    por resposta boa). Não conta chamadas ao modelo fallback: cada modelo
    tem cota própria, só rastreamos o consumo do padrão pra saber quando
    trocar."""
    CAMINHO_CONTADOR.write_text(
        json.dumps({"data": date.today().isoformat(), "contagem": _ler_contagem_hoje() + 1})
    )
