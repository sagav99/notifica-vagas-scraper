"""Rastreador de cota diária compartilhado entre os 3 módulos que chamam a
API do Gemini (gemini_pdf.py, gemini_texto.py, revisao_ia.py) — decisão do
usuário (2026-09-01): ao passar de ~470 chamadas no dia (a cota de
gemini-3.5-flash-lite é 500/dia), trocar pra gemini-3.1-flash-lite, que
tem cota diária própria e separada — ganha ~470 chamadas/dia extras sem
estourar limite de nenhum dos dois modelos.

Contador persiste em `public.gemini_quota_diaria` (migration 010, repo
principal) — não em arquivo `/tmp` como antes (achado real 2026-09-01,
TAREFAS.md: arquivo é local por processo, então mais de 1 execução do
cron no mesmo dia contava cada uma do zero e nunca cruzava o limiar de
troca de verdade). Incremento via `INSERT ... ON CONFLICT DO UPDATE`
(upsert atômico do Postgres) — seguro mesmo com processos concorrentes
escrevendo ao mesmo tempo, sem precisar de lock explícito.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg

LIMITE_ANTES_DE_TROCAR = 470

MODELO_PADRAO = "gemini-3.5-flash-lite"
MODELO_FALLBACK = "gemini-3.1-flash-lite"

FUSO_HORARIO = ZoneInfo("America/Sao_Paulo")


def _hoje() -> date:
    """`date.today()` usa o fuso do processo — nos runners do GitHub
    Actions isso é UTC, então a cota "diária" reiniciaria às 21h de
    Brasília (meia-noite UTC), não à meia-noite local (achado real,
    2026-09-03: usuário conferiu e viu o fallback em uso mais cedo do que
    esperava). Ancora explicitamente em America/Sao_Paulo, mesmo fuso já
    usado em toda formatação de data do site (`timeZone:
    "America/Sao_Paulo"` em `lib/visual`/painéis admin)."""
    return datetime.now(FUSO_HORARIO).date()


def _conectar() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não definida.")
    return psycopg.connect(database_url)


def _ler_contagem_hoje() -> int:
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "select contagem from public.gemini_quota_diaria where data = %(hoje)s",
            {"hoje": _hoje()},
        )
        linha = cur.fetchone()
        return linha[0] if linha else 0


def proximo_modelo() -> str:
    """Modelo a usar na PRÓXIMA chamada, considerando quantas já foram
    feitas hoje (somando todos os processos/módulos que usam este
    rastreador, via a tabela compartilhada).

    `GEMINI_MODELO_FORCADO` (env var) continua disponível como saída
    manual — útil quando se sabe, por um sinal fora deste contador (ex:
    429 confirmado), que a cota já estourou e não faz sentido esperar a
    contagem chegar no limiar."""
    forcado = os.environ.get("GEMINI_MODELO_FORCADO")
    if forcado:
        return forcado
    return MODELO_FALLBACK if _ler_contagem_hoje() >= LIMITE_ANTES_DE_TROCAR else MODELO_PADRAO


def registrar_chamada() -> None:
    """Chamar depois de CADA request de verdade feito à API do modelo
    padrão (sucesso ou erro — a cota é consumida pela tentativa, não só
    por resposta boa). Não conta chamadas ao modelo fallback: cada modelo
    tem cota própria, só rastreamos o consumo do padrão pra saber quando
    trocar."""
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into public.gemini_quota_diaria (data, contagem)
            values (%(hoje)s, 1)
            on conflict (data) do update
              set contagem = gemini_quota_diaria.contagem + 1
            """,
            {"hoje": _hoje()},
        )
        conn.commit()
