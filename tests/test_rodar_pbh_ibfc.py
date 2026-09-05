import rodar_pbh_ibfc as script
from notifica_vagas_scraper.fontes import pbh_ibfc


def _item(numero_edital="01/2025", modalidade="Concurso Público", area="Saúde"):
    return pbh_ibfc.ItemListagem(
        area=area,
        numero_edital=numero_edital,
        modalidade=modalidade,
        url="https://prefeitura.pbh.gov.br/saude/oportunidades-de-trabalho/concurso-publico-01-2025",
        resumo=None,
    )


def test_processos_externos_deixa_passar_concurso_publico_normal():
    itens = [_item()]
    assert script._processos_externos(itens) == itens


def test_processos_externos_barra_edital_155_2026_promocao_interna():
    # achado real (ver docstring de fontes/pbh_ibfc.py): o Edital
    # 155/2026 é procedimento seletivo INTERNO de promoção, não concurso
    # externo — não pode virar vaga no produto mesmo que apareça na
    # listagem geral.
    item_interno = _item(numero_edital="155/2026", modalidade="Seleção Interna")
    assert script._processos_externos([_item(), item_interno]) == [_item()]


class _RespostaFalsa:
    def __init__(self, *, text=None, content=None):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def test_processar_processo_diferencia_mesma_especialidade_em_jornadas_diferentes(monkeypatch):
    # o achado central da fonte: sem `montar_cargo_com_jornada`, as 3
    # linhas de "Médico - Pediatria" (12h/20h/24h, vagas e salários
    # diferentes) colidiriam no dedup e só 1 sobreviveria.
    documentos_html = "<table><tr><th>Título</th><th>Link</th><th>Arquivo</th><th>Data</th></tr></table>"

    def _fake_get(url, headers=None, timeout=None):
        if url.endswith("concurso-publico-01-2025"):
            return _RespostaFalsa(text=documentos_html)
        return _RespostaFalsa(content=b"pdf falso")

    monkeypatch.setattr(script.requests, "get", _fake_get)

    edital_falso = pbh_ibfc.Documento(
        titulo="Edital 01/2025 - compilado após 3ª retificação", url_pdf="https://x/edital.pdf", data=None
    )
    monkeypatch.setattr(script.pbh_ibfc, "listar_documentos", lambda html: [edital_falso])
    monkeypatch.setattr(script.pbh_ibfc, "escolher_edital_com_anexo", lambda docs: edital_falso)

    vagas_gemini = {
        "orgao": "Secretaria Municipal de Saúde",
        "numero_edital": "01/2025",
        "tipo_oportunidade": "concurso_efetivo",
        "data_publicacao": None,
        "inscricoes_inicio": None,
        "inscricoes_fim": None,
        "vagas": [
            {"cargo": "Médico - Pediatria", "salario": 3524.33, "salario_tipo": "mensal", "carga_horaria": "12 Horas", "requisitos": None},
            {"cargo": "Médico - Pediatria", "salario": 5873.89, "salario_tipo": "mensal", "carga_horaria": "20 Horas", "requisitos": None},
            {"cargo": "Médico - Pediatria", "salario": 7048.66, "salario_tipo": "mensal", "carga_horaria": "24 Horas", "requisitos": None},
        ],
    }
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: vagas_gemini)

    cargos_gravados = []

    def _fake_inserir(conn, *, cargo, salario, identificador_externo, **kwargs):
        cargos_gravados.append((cargo, salario, identificador_externo))
        return {"vaga_id": len(cargos_gravados), "evidencia_id": len(cargos_gravados)}

    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3106200, item=_item())

    assert total == 3
    cargos = [c for c, _s, _i in cargos_gravados]
    assert len(set(cargos)) == 3  # nenhuma das 3 jornadas foi descartada por colisão
    assert {"Médico - Pediatria (12 Horas)", "Médico - Pediatria (20 Horas)", "Médico - Pediatria (24 Horas)"} == set(
        cargos
    )
    identificadores = [i for _c, _s, i in cargos_gravados]
    assert len(set(identificadores)) == 3  # ids também não colidem


def test_processar_processo_sem_edital_publicado_pula_sem_erro(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(text="<table></table>"))
    monkeypatch.setattr(script.pbh_ibfc, "listar_documentos", lambda html: [])
    monkeypatch.setattr(script.pbh_ibfc, "escolher_edital_com_anexo", lambda docs: None)

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3106200, item=_item())
    assert total == 0


def test_processar_processo_gemini_sem_vagas_pula_sem_erro(monkeypatch):
    edital_falso = pbh_ibfc.Documento(titulo="Edital SMSA 01/2025", url_pdf="https://x/edital.pdf", data=None)
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(text="<table></table>", content=b"x"))
    monkeypatch.setattr(script.pbh_ibfc, "listar_documentos", lambda html: [edital_falso])
    monkeypatch.setattr(script.pbh_ibfc, "escolher_edital_com_anexo", lambda docs: edital_falso)
    monkeypatch.setattr(script.gemini_pdf, "extrair_vagas_de_pdf", lambda pdf_bytes: {"vagas": []})

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3106200, item=_item())
    assert total == 0
