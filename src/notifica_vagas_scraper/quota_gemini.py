"""Rastreador de cota diária compartilhado entre os 3 módulos que chamam a
API do Gemini (gemini_pdf.py, gemini_texto.py, revisao_ia.py).

Contador persiste em `public.gemini_quota_diaria` (migration 010, repo
principal) — não em arquivo `/tmp` como antes (achado real 2026-09-01,
TAREFAS.md: arquivo é local por processo, então mais de 1 execução do
cron no mesmo dia contava cada uma do zero e nunca cruzava o limiar de
troca de verdade). Incremento via `INSERT ... ON CONFLICT DO UPDATE`
(upsert atômico do Postgres) — seguro mesmo com processos concorrentes
escrevendo ao mesmo tempo, sem precisar de lock explícito.

**Despacho por menor uso do dia, não troca sequencial (migration 042,
2026-09-16)**: o desenho anterior usava só `gemini-3.5-flash-lite` até
~470 chamadas e só então trocava pra `gemini-3.1-flash-lite` — sub-usava
o fallback, que tem RPD (500), RPM (15) e TPM (250k) próprios e
independentes do padrão. `proximo_modelo()` agora escolhe, a cada
chamada, qual dos dois modelos tem MENOR contagem hoje (empate -> padrão)
— na prática intercala entre os dois desde o início, dobrando o
throughput combinado (30 RPM / 500k TPM / 1000 RPD) em vez de usar só 1
de cada vez. A tabela `gemini_quota_diaria` agora tem 1 linha por
`(data, modelo)`, não mais 1 por dia só (migration 042)."""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg

LIMITE_ANTES_DE_TROCAR = 470  # mantido só como referência histórica/testes; não usado no despacho

MODELO_PADRAO = "gemini-3.5-flash-lite"
MODELO_FALLBACK = "gemini-3.1-flash-lite"

FUSO_HORARIO = ZoneInfo("America/Los_Angeles")


def _hoje() -> date:
    """`date.today()` usa o fuso do processo — nos runners do GitHub
    Actions isso é UTC, então uma primeira versão deste código reiniciava
    a cota "diária" à meia-noite UTC (achado real, 2026-09-03: usuário
    conferiu e viu o fallback em uso mais cedo do que esperava). Esse fix
    ancorou em America/Sao_Paulo — mas isso era a causa raiz ERRADA:
    quem decide o dia da cota não é o fuso do usuário, é o fuso em que o
    Google reseta a cota real de `gemini-3.5-flash-lite` na conta, que é
    meia-noite em America/Los_Angeles (Pacific Time), não meia-noite de
    Brasília (confirmado: a cota RPD do Gemini API reseta à meia-noite
    Pacific Time, independente de onde o cliente roda — ver
    https://ai.google.dev/gemini-api/docs/rate-limits).

    Bug real encontrado 2026-09-03 (registrado em TAREFAS.md): nosso
    contador dizia 470/500 chamadas já feitas "hoje", mas o AI Studio do
    Google mostrava a cota de `gemini-3.5-flash-lite` zerada. Causa raiz:
    o cron (`.github/workflows/scrape-diario.yml`) roda às 06:00 UTC —
    03:00 em Brasília, mas ainda 22:00-23:00 do dia anterior em Pacific
    Time (UTC-7/UTC-8 conforme horário de verão americano). Com o fuso
    ancorado em America/Sao_Paulo, nosso `_hoje()` já tinha virado o dia
    (00:00 BRT já passou) quando o cron roda, então cada chamada real da
    execução caía no bucket "hoje" nosso — enquanto o Google ainda
    atribuía essas mesmas chamadas ao dia ANTERIOR (seu próprio "hoje"
    só vira quando bate meia-noite Pacific, horas depois). Resultado:
    nosso contador acumulava ~470 chamadas atribuídas a "hoje" que o
    Google só ia zerar (nesse mesmo bucket, do ponto de vista dele) horas
    mais tarde — daí o contador nosso alto e o real do Google zerado no
    mesmo instante. Ancorar em America/Los_Angeles alinha nosso corte de
    dia exatamente ao corte que o Google usa de verdade."""
    return datetime.now(FUSO_HORARIO).date()


def _conectar() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não definida.")
    return psycopg.connect(database_url)


def _ler_contagem_hoje(modelo: str) -> int:
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "select contagem from public.gemini_quota_diaria where data = %(hoje)s and modelo = %(modelo)s",
            {"hoje": _hoje(), "modelo": modelo},
        )
        linha = cur.fetchone()
        return linha[0] if linha else 0


def proximo_modelo() -> str:
    """Modelo a usar na PRÓXIMA chamada: o que tiver MENOR contagem hoje
    entre `MODELO_PADRAO`/`MODELO_FALLBACK` (empate -> padrão) — ver
    docstring do módulo. Considera todos os processos/módulos que usam
    este rastreador, via a tabela compartilhada.

    `GEMINI_MODELO_FORCADO` (env var) continua disponível como saída
    manual — útil quando se sabe, por um sinal fora deste contador (ex:
    429 confirmado), que a cota já estourou e não faz sentido esperar a
    contagem chegar no limiar."""
    forcado = os.environ.get("GEMINI_MODELO_FORCADO")
    if forcado:
        return forcado
    contagem_padrao = _ler_contagem_hoje(MODELO_PADRAO)
    contagem_fallback = _ler_contagem_hoje(MODELO_FALLBACK)
    return MODELO_FALLBACK if contagem_fallback < contagem_padrao else MODELO_PADRAO


def registrar_chamada(modelo: str) -> None:
    """Chamar depois de CADA request de verdade feito à API de um
    modelo (sucesso ou erro — a cota é consumida pela tentativa, não só
    por resposta boa). Cada modelo tem sua própria linha
    `(data, modelo)` — os dois são rastreados agora, não só o padrão
    (migration 042), porque `proximo_modelo()` intercala entre os dois."""
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into public.gemini_quota_diaria (data, modelo, contagem)
            values (%(hoje)s, %(modelo)s, 1)
            on conflict (data, modelo) do update
              set contagem = gemini_quota_diaria.contagem + 1
            """,
            {"hoje": _hoje(), "modelo": modelo},
        )
        conn.commit()
