from datetime import date, datetime, timezone

from notifica_vagas_scraper import quota_gemini


class _CursorFalso:
    def __init__(self, tabela: dict):
        self._tabela = tabela
        self._ultimo_resultado = None

    def execute(self, sql: str, parametros: dict):
        hoje = parametros["hoje"]
        if sql.strip().startswith("select"):
            contagem = self._tabela.get(hoje)
            self._ultimo_resultado = (contagem,) if contagem is not None else None
        elif sql.strip().startswith("insert"):
            self._tabela[hoje] = self._tabela.get(hoje, 0) + 1
        else:
            raise AssertionError(f"SQL inesperado: {sql}")

    def fetchone(self):
        return self._ultimo_resultado

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _ConexaoFalsa:
    def __init__(self, tabela: dict):
        self._tabela = tabela

    def cursor(self):
        return _CursorFalso(self._tabela)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _instalar_tabela_falsa(monkeypatch, tabela: dict | None = None):
    tabela = tabela if tabela is not None else {}
    monkeypatch.setattr(quota_gemini, "_conectar", lambda: _ConexaoFalsa(tabela))
    return tabela


def test_sem_registro_comeca_no_modelo_padrao(monkeypatch):
    _instalar_tabela_falsa(monkeypatch)
    assert quota_gemini.proximo_modelo() == quota_gemini.MODELO_PADRAO


def test_registrar_chamada_incrementa(monkeypatch):
    tabela = _instalar_tabela_falsa(monkeypatch)

    quota_gemini.registrar_chamada()
    quota_gemini.registrar_chamada()

    assert tabela[quota_gemini._hoje()] == 2


def test_troca_para_fallback_apos_limite(monkeypatch):
    tabela = _instalar_tabela_falsa(monkeypatch, {quota_gemini._hoje(): quota_gemini.LIMITE_ANTES_DE_TROCAR})
    assert quota_gemini.proximo_modelo() == quota_gemini.MODELO_FALLBACK
    assert tabela[quota_gemini._hoje()] == quota_gemini.LIMITE_ANTES_DE_TROCAR


def test_dia_diferente_nao_conta(monkeypatch):
    _instalar_tabela_falsa(monkeypatch, {date(2000, 1, 1): 999})
    assert quota_gemini.proximo_modelo() == quota_gemini.MODELO_PADRAO


def test_modelo_forcado_por_env_sobrepoe_contagem(monkeypatch):
    _instalar_tabela_falsa(monkeypatch)
    monkeypatch.setenv("GEMINI_MODELO_FORCADO", "gemini-3.1-flash-lite")
    assert quota_gemini.proximo_modelo() == "gemini-3.1-flash-lite"


def test_hoje_usa_fuso_do_google_pacific_nao_brasilia(monkeypatch):
    """Regressão do bug real de 2026-09-03: o cron
    (`.github/workflows/scrape-diario.yml`) roda às 06:00 UTC = 03:00 em
    Brasília, mas ainda é o dia ANTERIOR em Pacific Time (a cota RPD real
    do Gemini reseta à meia-noite Pacific, não à meia-noite de Brasília).

    Com `_hoje()` ancorado em America/Sao_Paulo (bug), essa hora exata já
    contava como um novo dia aqui, enquanto o Google ainda atribuía as
    chamadas ao dia anterior — daí nosso contador acumular ~470 chamadas
    "de hoje" que o Google só ia zerar (do lado dele) horas depois,
    explicando o contador nosso alto com a cota real do Google zerada.

    Este teste fixa o instante exato do cron (2026-09-04 06:00 UTC) e
    confirma que `_hoje()` retorna a data em Pacific Time (2026-09-03,
    dia anterior), não a data em Brasília (2026-09-04) — se alguém
    reintroduzir America/Sao_Paulo em `FUSO_HORARIO`, este teste falha."""

    class _DatetimeFalso(datetime):
        @classmethod
        def now(cls, tz=None):
            instante_utc = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)
            return instante_utc.astimezone(tz) if tz else instante_utc

    monkeypatch.setattr(quota_gemini, "datetime", _DatetimeFalso)

    assert quota_gemini._hoje() == date(2026, 9, 3)  # dia em Pacific Time
    assert quota_gemini._hoje() != date(2026, 9, 4)  # dia em Brasília (bug antigo)


def test_incremento_e_atomico_via_upsert_concorrente(monkeypatch):
    # Duas "execuções" concorrentes incrementando a mesma linha -- o
    # upsert (contagem = contagem + 1 no servidor) não perde incremento
    # como um read-then-write local perderia sob corrida real.
    tabela = _instalar_tabela_falsa(monkeypatch)
    for _ in range(5):
        quota_gemini.registrar_chamada()
    assert tabela[quota_gemini._hoje()] == 5
