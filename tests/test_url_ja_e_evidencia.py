from notifica_vagas_scraper import db


class _Cur:
    def __init__(self, achou):
        self.achou, self.args = achou, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.args = params

    def fetchone(self):
        return (1,) if self.achou else None


class _Conn:
    def __init__(self, achou):
        self.cur = _Cur(achou)

    def cursor(self):
        return self.cur


def test_sem_urls_validas_nao_consulta():
    conn = _Conn(True)
    assert db.url_ja_e_evidencia(conn, [None, ""]) is False
    assert conn.cur.args is None


def test_url_existente_e_inexistente():
    assert db.url_ja_e_evidencia(_Conn(True), ["https://x/a.pdf"]) is True
    assert db.url_ja_e_evidencia(_Conn(False), ["https://x/a.pdf"]) is False
