from datetime import date

from notifica_vagas_scraper import completude_gemini, db
from notifica_vagas_scraper.revisao_ia import CotaGeminiEsgotadaError


def _vaga_base(**overrides):
    vaga = {c: "algo" for c in completude_gemini.CAMPOS_PEDIVEIS}
    vaga.update(
        id="v1", cargo="Médico", orgao="Prefeitura de X", nome="X", uf="MG",
        numero_edital="01/2026", url="https://exemplo.com/edital.pdf",
        tipo_documento="pdf", fonte_id="f1",
    )
    vaga.update(overrides)
    return vaga


def test_campos_faltando_so_os_null():
    vaga = {"salario": 5000, "banca_organizadora": None, "tem_prova": None, "requisitos": "X"}
    faltando = completude_gemini.campos_faltando(
        {c: vaga.get(c) for c in completude_gemini.CAMPOS_PEDIVEIS}
    )
    assert "banca_organizadora" in faltando
    assert "tem_prova" in faltando
    assert "salario" not in faltando
    assert "requisitos" not in faltando


def test_precisa_link_pdf_so_quando_evidencia_nao_e_pdf():
    assert completude_gemini.precisa_link_pdf({"tipo_documento": "pagina_html"})
    assert completude_gemini.precisa_link_pdf({"tipo_documento": "notícia"})
    assert not completude_gemini.precisa_link_pdf({"tipo_documento": "pdf"})


def test_montar_schema_texto_inclui_link_pdf_so_quando_pedido():
    com_pdf = completude_gemini.montar_schema_texto(["salario"], pedir_link_pdf=True)
    assert "link_edital_pdf" in com_pdf
    sem_pdf = completude_gemini.montar_schema_texto(["salario"], pedir_link_pdf=False)
    assert "link_edital_pdf" not in sem_pdf
    assert "eh_vaga_de_emprego" in com_pdf and "eh_vaga_de_emprego" in sem_pdf


def test_montar_prompt_inclui_pedido_de_natureza_e_pdf_quando_aplicavel():
    vaga = {"cargo": "Médico ESF", "orgao": "Prefeitura de X", "nome": "X", "uf": "MG", "numero_edital": "01/2026"}
    prompt = completude_gemini.montar_prompt(vaga, ["salario"], link="https://exemplo.com/noticia", pedir_link_pdf=True)
    assert "eh_vaga_de_emprego" in prompt
    assert "residência médica" in prompt
    assert "link_edital_pdf" in prompt


def test_filtrar_campos_aceitos_rejeita_confianca_baixa():
    resultado = {"salario": 5000, "confianca": "baixa"}
    assert completude_gemini.filtrar_campos_aceitos(resultado, ["salario"]) == {}


def test_filtrar_campos_aceitos_aceita_alta_e_media():
    resultado = {"salario": 5000, "confianca": "alta"}
    assert completude_gemini.filtrar_campos_aceitos(resultado, ["salario"]) == {"salario": 5000}


def test_converter_tipos_data_iso_vira_date():
    convertido = completude_gemini._converter_tipos({"data_prova": "2026-11-15", "salario": 5000})
    assert convertido["data_prova"] == date(2026, 11, 15)
    assert convertido["salario"] == 5000


# --- processar_vaga: pipeline completo, com db/consultar mockados ---


def _instalar_db_falso(monkeypatch, *, atualizar=None, registrar=None, marcar_nao_emprego=None, evidencia=None):
    if atualizar is not None:
        monkeypatch.setattr(db, "atualizar_campos_vaga", atualizar)
    if registrar is not None:
        monkeypatch.setattr(db, "registrar_conferencia", registrar)
    if marcar_nao_emprego is not None:
        monkeypatch.setattr(db, "marcar_nao_e_vaga_de_emprego", marcar_nao_emprego)
    if evidencia is not None:
        monkeypatch.setattr(db, "registrar_evidencia_adicional", evidencia)


def test_processar_vaga_grava_campo_aceito(monkeypatch):
    atualizacoes = []
    conferencias = []
    _instalar_db_falso(
        monkeypatch,
        atualizar=lambda conn, *, vaga_id, campos: atualizacoes.append((vaga_id, campos)),
        registrar=lambda conn, **k: conferencias.append(k),
    )
    monkeypatch.setattr(
        completude_gemini, "consultar",
        lambda vaga, campos, **k: {
            "salario": 5000, "eh_vaga_de_emprego": True, "motivo_natureza": "edital de concurso normal",
            "confianca": "alta", "fonte_usada": "edital.pdf",
        },
    )
    vaga = _vaga_base(salario=None)

    resultado = completude_gemini.processar_vaga(None, vaga)

    assert resultado == "campo_preenchido"
    assert atualizacoes == [("v1", {"salario": 5000})]
    assert conferencias[0]["conferido_por"] == "completude_gemini"
    assert conferencias[0]["resultado"] == "campo_preenchido"


def test_processar_vaga_reverte_quando_nao_e_vaga_de_emprego(monkeypatch):
    revertidas = []
    _instalar_db_falso(
        monkeypatch,
        marcar_nao_emprego=lambda conn, *, vaga_id, motivo: revertidas.append((vaga_id, motivo)),
    )
    monkeypatch.setattr(
        completude_gemini, "consultar",
        lambda vaga, campos, **k: {
            "eh_vaga_de_emprego": False,
            "motivo_natureza": "edital fala em 'processo seletivo para residência médica'",
            "confianca": "alta", "fonte_usada": "edital.pdf",
        },
    )
    vaga = _vaga_base()

    resultado = completude_gemini.processar_vaga(None, vaga)

    assert resultado == "rejeitada_nao_e_concurso"
    assert revertidas == [("v1", "edital fala em 'processo seletivo para residência médica'")]


def test_processar_vaga_nao_reverte_com_confianca_baixa(monkeypatch):
    # achado real 2026-09-18 (Santa Casa de BH): não vale reverter uma vaga
    # aprovada com base numa leitura de baixa confiança -- exige alta/media,
    # mesmo critério já usado pros campos de completude.
    conferencias = []
    revertidas = []
    _instalar_db_falso(
        monkeypatch,
        registrar=lambda conn, **k: conferencias.append(k),
        marcar_nao_emprego=lambda conn, **k: revertidas.append(k),
    )
    monkeypatch.setattr(
        completude_gemini, "consultar",
        lambda vaga, campos, **k: {
            "eh_vaga_de_emprego": False, "motivo_natureza": "não tenho certeza",
            "confianca": "baixa", "fonte_usada": "não encontrado",
        },
    )
    vaga = _vaga_base()

    resultado = completude_gemini.processar_vaga(None, vaga)

    assert resultado == "sem_alteracao"
    assert revertidas == []


def test_processar_vaga_registra_link_pdf_quando_evidencia_nao_e_pdf(monkeypatch):
    evidencias = []
    _instalar_db_falso(
        monkeypatch,
        registrar=lambda conn, **k: None,
        evidencia=lambda conn, **k: evidencias.append(k),
    )
    monkeypatch.setattr(
        completude_gemini, "consultar",
        lambda vaga, campos, **k: {
            "link_edital_pdf": "https://prefeitura.exemplo.gov.br/edital.pdf",
            "eh_vaga_de_emprego": True, "motivo_natureza": "concurso normal",
            "confianca": "alta", "fonte_usada": "edital.pdf",
        },
    )
    vaga = _vaga_base(tipo_documento="pagina_html", url="https://noticia.exemplo.com/materia")

    resultado = completude_gemini.processar_vaga(None, vaga)

    assert resultado == "sem_alteracao"
    assert evidencias == [{
        "vaga_id": "v1", "fonte_id": "f1",
        "identificador_externo": "completude-gemini-pdf-v1",
        "url": "https://prefeitura.exemplo.gov.br/edital.pdf", "tipo_documento": "pdf",
    }]


def test_processar_vaga_nao_pede_link_pdf_quando_ja_tem_pdf(monkeypatch):
    evidencias = []
    _instalar_db_falso(monkeypatch, registrar=lambda conn, **k: None, evidencia=lambda conn, **k: evidencias.append(k))
    prompts_pedir_pdf = []

    def _consultar_falso(vaga, campos, **k):
        prompts_pedir_pdf.append(completude_gemini.precisa_link_pdf(vaga))
        return {"eh_vaga_de_emprego": True, "motivo_natureza": "ok", "confianca": "alta", "fonte_usada": "x"}

    monkeypatch.setattr(completude_gemini, "consultar", _consultar_falso)
    vaga = _vaga_base(tipo_documento="pdf")

    completude_gemini.processar_vaga(None, vaga)

    assert prompts_pedir_pdf == [False]
    assert evidencias == []


def test_processar_vaga_propaga_erro_de_cota(monkeypatch):
    def _consultar_cota_esgotada(vaga, campos, **k):
        raise CotaGeminiEsgotadaError("HTTP 429")

    monkeypatch.setattr(completude_gemini, "consultar", _consultar_cota_esgotada)
    vaga = _vaga_base()

    try:
        completude_gemini.processar_vaga(None, vaga)
        assert False, "deveria ter propagado CotaGeminiEsgotadaError"
    except CotaGeminiEsgotadaError:
        pass


def test_processar_vaga_erro_gemini_registra_e_nao_quebra(monkeypatch):
    conferencias = []
    _instalar_db_falso(monkeypatch, registrar=lambda conn, **k: conferencias.append(k))

    def _consultar_com_erro(vaga, campos, **k):
        raise completude_gemini.ErroCompletudeGemini("falhou")

    monkeypatch.setattr(completude_gemini, "consultar", _consultar_com_erro)
    vaga = _vaga_base()

    resultado = completude_gemini.processar_vaga(None, vaga)

    assert resultado == "erro_gemini"
    assert conferencias[0]["resultado"] == "erro_gemini"
