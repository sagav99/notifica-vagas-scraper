from datetime import date

import rodar_maua as script
from notifica_vagas_scraper.fontes import maua

CARGOS_EDITAL_57_2026 = [
    "Médico Cardiologista",
    "Médico Dermatologista",
    "Médico Ginecologista",
    "Médico Hematologista",
    "Médico Neurologista",
    "Médico Neurologista Pediátrico",
    "Médico Pneumologista na área de tisiologia",
    "Médico Psiquiatra",
    "Médico Psiquiatra Pediátrico",
    "Médico Reumatologista",
]


class _RespostaFalsa:
    def __init__(self, *, text=None, content=None):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _extraido_gemini_edital_57_2026():
    # espelha a resposta real observada rodando gemini_pdf.extrair_vagas_de_pdf
    # contra o PDF real do Edital 57/2026 (docs/fixtures/maua_sp/ no repo
    # principal): salario/salario_tipo vêm null pra TODOS os cargos porque
    # a remuneração é "R$ 130,00/hora", fora do que o prompt compartilhado
    # de gemini_pdf converte em número (ver docstring de fontes/maua.py).
    return {
        "numero_edital": "57/2026",
        "orgao": "Prefeitura Municipal de Mauá",
        "data_publicacao": "2026-08-31",
        "inscricoes_inicio": "2026-09-03",
        "inscricoes_fim": "2026-09-17",
        "taxa_inscricao": None,
        "data_prova": None,
        "tipo_oportunidade": "processo_seletivo_temporario",
        "vagas": [
            {
                "cargo": cargo,
                "pagina": 1,
                "vagas_qtd": None,
                "salario": None,
                "salario_tipo": None,
                "requisitos": "Superior completo em Medicina, registro profissional, residência ou título de especialista",
                "carga_horaria": "20hs",
            }
            for cargo in CARGOS_EDITAL_57_2026
        ],
    }


def _edital_vigente():
    return maua.EditalVigente(
        numero_edital="57/2026",
        pdf_url="https://processoseletivo.maua.sp.gov.br/public/docs/edital_abertura_57_2026.pdf",
        inscricoes_inicio=date(2026, 9, 3),
        inscricoes_fim=date(2026, 9, 17),
    )


def test_processar_edital_nao_descarta_nenhuma_das_10_especialidades(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(content=b"pdf falso"))
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: _extraido_gemini_edital_57_2026())
    monkeypatch.setattr(script, "_extrair_texto_pdf", lambda conteudo: "Médico X 20hs R$ 130,00/ hora.")
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    cargos_gravados = []

    def _fake_inserir(conn, *, cargo, salario, salario_tipo, identificador_externo, **kwargs):
        cargos_gravados.append((cargo, salario, salario_tipo, identificador_externo))
        return {"vaga_id": len(cargos_gravados), "evidencia_id": len(cargos_gravados)}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_edital(conn=None, fonte_id="fonte-x", codigo_ibge=3529401, edital=_edital_vigente())

    assert total == 10
    cargos = {c for c, _s, _t, _i in cargos_gravados}
    assert cargos == set(CARGOS_EDITAL_57_2026)  # nenhuma especialidade descartada
    identificadores = [i for _c, _s, _t, i in cargos_gravados]
    assert len(set(identificadores)) == 10  # sem colisão de dedup entre especialidades


def test_processar_edital_aplica_valor_hora_uniforme_quando_gemini_deixa_salario_null(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(content=b"pdf falso"))
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: _extraido_gemini_edital_57_2026())
    monkeypatch.setattr(script, "_extrair_texto_pdf", lambda conteudo: "Médico X 20hs R$ 130,00/ hora.")
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    salarios_gravados = []

    def _fake_inserir(conn, *, cargo, salario, salario_tipo, **kwargs):
        salarios_gravados.append((salario, salario_tipo))
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    script.processar_edital(conn=None, fonte_id="fonte-x", codigo_ibge=3529401, edital=_edital_vigente())

    # todo cargo recebeu o valor-hora uniforme (130.0) com salario_tipo
    # "hora" (migration 022 do repo principal, ver docstring de fontes/maua.py).
    assert all(salario == 130.0 and tipo == "hora" for salario, tipo in salarios_gravados)


def test_processar_edital_sem_valor_hora_identificavel_grava_salario_null(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(content=b"pdf falso"))
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: _extraido_gemini_edital_57_2026())
    # texto do PDF sem remuneração reconhecível (ex: heurística de
    # uniformidade não confirmou) — nunca inventa um valor.
    monkeypatch.setattr(script, "_extrair_texto_pdf", lambda conteudo: "edital sem remuneração legível")
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    salarios_gravados = []

    def _fake_inserir(conn, *, cargo, salario, salario_tipo, **kwargs):
        salarios_gravados.append((salario, salario_tipo))
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    script.processar_edital(conn=None, fonte_id="fonte-x", codigo_ibge=3529401, edital=_edital_vigente())

    assert all(salario is None and tipo is None for salario, tipo in salarios_gravados)


def test_processar_edital_gemini_sem_vagas_pula_sem_erro(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(content=b"pdf falso"))
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: {"vagas": []})

    total = script.processar_edital(conn=None, fonte_id="fonte-x", codigo_ibge=3529401, edital=_edital_vigente())
    assert total == 0


def test_processar_edital_resumo_menciona_cadastro_reserva(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(content=b"pdf falso"))
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: _extraido_gemini_edital_57_2026())
    monkeypatch.setattr(script, "_extrair_texto_pdf", lambda conteudo: "R$ 130,00/ hora")
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    resumos = []

    def _fake_inserir(conn, *, resumo, **kwargs):
        resumos.append(resumo)
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    script.processar_edital(conn=None, fonte_id="fonte-x", codigo_ibge=3529401, edital=_edital_vigente())

    assert all("cadastro reserva" in r for r in resumos)
