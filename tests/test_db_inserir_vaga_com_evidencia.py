"""Cobre o achado real de 2026-09-12: a mesma vaga de Santa Bárbara/MG virou
2 linhas em `vagas` porque duas fontes escreveram `orgao`/`cargo` com
variação de acento/espaço ("Santa Bárbara" x "Santa Barbara",
"Ginecologista/Obstetra" x "Ginecologista / Obstetra") e a dedup de
`inserir_vaga_com_evidencia` comparava string exata. O site acabou
mostrando a linha incompleta (sem salário) enquanto a completa existia do
lado, sem nunca ser vista."""

from decimal import Decimal

from notifica_vagas_scraper import db


class _FakeCursor:
    def __init__(self, estado):
        self._estado = estado
        self._resultado = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        params = params or {}
        sql_norm = " ".join(sql.split())

        if sql_norm.startswith("select id, orgao, cargo from public.vagas"):
            self._resultado = [
                (v["id"], v["orgao"], v["cargo"])
                for v in self._estado["vagas"]
                if v["municipio_id"] == params["municipio_id"]
                and v["numero_edital"] == params["numero_edital"]
            ]
        elif sql_norm.startswith("insert into public.vagas"):
            novo_id = len(self._estado["vagas"]) + 1
            linha = {"id": novo_id, **{k: v for k, v in params.items()}}
            self._estado["vagas"].append(linha)
            self._resultado = (novo_id,)
        elif sql_norm.startswith("update public.vagas set"):
            linha = next(v for v in self._estado["vagas"] if v["id"] == params["vaga_id"])
            for campo, valor in params.items():
                if campo == "vaga_id":
                    continue
                if linha.get(campo) is None and valor is not None:
                    linha[campo] = valor
            self._resultado = None
        elif sql_norm.startswith("insert into public.vaga_evidencias"):
            chave = (params["fonte_id"], params["identificador_externo"])
            if chave in self._estado["evidencias"]:
                self._resultado = None
            else:
                self._estado["evidencias"].add(chave)
                novo_id = len(self._estado["evidencias"])
                self._resultado = (novo_id,)
        else:
            raise AssertionError(f"SQL não esperado no fake: {sql_norm[:80]}")

    def fetchone(self):
        return self._resultado

    def fetchall(self):
        return self._resultado


class _FakeConn:
    def __init__(self):
        self._estado = {"vagas": [], "evidencias": set()}

    def cursor(self):
        return _FakeCursor(self._estado)


def _inserir(conn, *, orgao, cargo, salario, identificador_externo, fonte_id="fonte-a"):
    return db.inserir_vaga_com_evidencia(
        conn,
        fonte_id=fonte_id,
        municipio_id=1,
        identificador_externo=identificador_externo,
        orgao=orgao,
        cargo=cargo,
        salario=Decimal(str(salario)) if salario is not None else None,
        salario_tipo="mensal" if salario is not None else None,
        tipo_oportunidade=None,
        numero_edital="01/2026",
        data_publicacao=None,
        inscricoes_inicio=None,
        inscricoes_fim=None,
        status="aberta",
        resumo="teste",
        url_evidencia="https://exemplo.org/edital",
        tipo_documento="pagina_html",
        texto_extraido=None,
    )


def test_dedup_ignora_acento_e_espaco_e_preenche_salario_faltando():
    conn = _FakeConn()

    primeiro = _inserir(
        conn,
        orgao="Prefeitura Municipal de Santa Bárbara",
        cargo="Médico Ginecologista/Obstetra",
        salario=None,
        identificador_externo="pci-1",
    )
    assert primeiro["vaga_criada"] is True

    segundo = _inserir(
        conn,
        orgao="Prefeitura Municipal de Santa Barbara",
        cargo="Médico Ginecologista / Obstetra",
        salario=6080.66,
        identificador_externo="ache-1",
        fonte_id="fonte-b",
    )

    assert segundo["vaga_criada"] is False
    assert segundo["vaga_id"] == primeiro["vaga_id"]
    assert len(conn._estado["vagas"]) == 1
    assert conn._estado["vagas"][0]["salario"] == Decimal("6080.66")


def test_dedup_nao_sobrescreve_valor_ja_preenchido():
    conn = _FakeConn()

    primeiro = _inserir(
        conn, orgao="Prefeitura de X", cargo="Médico", salario=5000, identificador_externo="a"
    )
    _inserir(
        conn,
        orgao="Prefeitura de X",
        cargo="Médico",
        salario=9999,
        identificador_externo="b",
        fonte_id="fonte-b",
    )

    assert conn._estado["vagas"][0]["salario"] == Decimal("5000")
    assert primeiro["vaga_id"] == 1


def test_cargos_diferentes_no_mesmo_edital_nao_colidem():
    conn = _FakeConn()

    vaga_acs = _inserir(
        conn, orgao="Prefeitura de Y", cargo="ACS", salario=None, identificador_externo="acs"
    )
    vaga_ace = _inserir(
        conn, orgao="Prefeitura de Y", cargo="ACE", salario=None, identificador_externo="ace"
    )

    assert vaga_acs["vaga_id"] != vaga_ace["vaga_id"]
    assert len(conn._estado["vagas"]) == 2
