import json
from datetime import date
from pathlib import Path

import rodar_sorocaba as script

from notifica_vagas_scraper.fontes import sorocaba_sp

FIXTURES = Path(__file__).parent / "fixtures" / "sorocaba_sp"


class _RespostaFalsa:
    def __init__(self, *, content: bytes | None = None, json_dados=None):
        self.content = content
        self._json_dados = json_dados

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_dados


def _post_edital_01_2026(*, com_pdf_no_html: bool) -> sorocaba_sp.Post:
    conteudo_html = (
        '<p>Edital em anexo: <a href="https://noticias.sorocaba.sp.gov.br/wp-content/uploads/edital-01-2026.pdf">'
        "baixar</a></p>"
        if com_pdf_no_html
        else "<p>publicado, anexo disponível na área de mídia</p>"
    )
    return sorocaba_sp.Post(
        id=999,
        titulo="Prefeitura de Sorocaba abre Concurso Público nº 01/2026 para área da saúde",
        slug="prefeitura-abre-concurso-publico-01-2026-saude",
        data=date(2026, 2, 10),
        link="https://noticias.sorocaba.sp.gov.br/prefeitura-abre-concurso-publico-01-2026-saude/",
        conteudo_html=conteudo_html,
    )


def test_processar_post_le_pdf_direto_do_html_e_grava_7_cargos(monkeypatch):
    post = _post_edital_01_2026(com_pdf_no_html=True)
    pdf_bytes = (FIXTURES / "edital_abertura_01_2026_saude.pdf").read_bytes()

    def _fake_get(url, params=None, headers=None, timeout=None):
        assert url == "https://noticias.sorocaba.sp.gov.br/wp-content/uploads/edital-01-2026.pdf"
        return _RespostaFalsa(content=pdf_bytes)

    monkeypatch.setattr(script.requests, "get", _fake_get)

    gravados = []

    def _fake_inserir(conn, *, cargo, identificador_externo, salario, salario_tipo, banca_organizadora, tem_prova, exige_curriculo, **kwargs):
        gravados.append((cargo, identificador_externo, salario, salario_tipo, banca_organizadora, tem_prova, exige_curriculo))
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_post(conn=None, fonte_id="fonte-x", codigo_ibge=3552205, post=post)

    assert total == 7
    cargos = {linha[0] for linha in gravados}
    # achado real: caso mais denso das fixtures — 1 técnico + 6
    # especialidades médicas no mesmo edital, nenhuma descartada.
    assert cargos == {
        "Técnico de Enfermagem",
        "Médico I – Médico do Trabalho",
        "Médico I – Nefrologista",
        "Médico I – Neurologista Adulto",
        "Médico I – Neurologista Infantil",
        "Médico I – Psiquiatra Adulto",
        "Médico I – Psiquiatra Infantil",
    }
    for cargo, identificador, salario, salario_tipo, banca, tem_prova, exige_curriculo in gravados:
        assert identificador.startswith("sorocaba-sp-01-2026-")
        assert salario_tipo == "hora"
        assert salario is not None
        assert banca == "Fundação VUNESP"
        assert tem_prova is True
        assert exige_curriculo is False


def test_processar_post_sem_pdf_no_html_cai_pro_fallback_de_media(monkeypatch):
    post = _post_edital_01_2026(com_pdf_no_html=False)
    pdf_bytes = (FIXTURES / "edital_abertura_01_2026_saude.pdf").read_bytes()
    media_dados = json.loads((FIXTURES / "media_exemplo_post_com_pdf.json").read_text(encoding="utf-8"))
    # simula um anexo PDF de verdade pra este post especificamente
    media_dados = [{**media_dados[0], "source_url": "https://noticias.sorocaba.sp.gov.br/uploads/edital-via-media.pdf", "mime_type": "application/pdf"}]

    chamadas = []

    def _fake_get(url, params=None, headers=None, timeout=None):
        chamadas.append(url)
        if url == sorocaba_sp.URL_MEDIA:
            assert params == {"parent": post.id}
            return _RespostaFalsa(json_dados=media_dados)
        assert url == "https://noticias.sorocaba.sp.gov.br/uploads/edital-via-media.pdf"
        return _RespostaFalsa(content=pdf_bytes)

    monkeypatch.setattr(script.requests, "get", _fake_get)
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda conn, **k: {"vaga_id": 1, "evidencia_id": 1})

    total = script.processar_post(conn=None, fonte_id="fonte-x", codigo_ibge=3552205, post=post)

    assert total == 7
    assert sorocaba_sp.URL_MEDIA in chamadas


def test_processar_post_sem_pdf_em_lugar_nenhum_devolve_zero(monkeypatch):
    post = sorocaba_sp.Post(
        id=1000,
        titulo="Concurso Público nº 02/2026",
        slug="concurso-publico-02-2026",
        data=None,
        link="https://noticias.sorocaba.sp.gov.br/x/",
        conteudo_html="<p>sem anexo</p>",
    )

    def _fake_get(url, params=None, headers=None, timeout=None):
        return _RespostaFalsa(json_dados=[])

    monkeypatch.setattr(script.requests, "get", _fake_get)

    total = script.processar_post(conn=None, fonte_id="fonte-x", codigo_ibge=3552205, post=post)
    assert total == 0


def test_main_filtra_so_posts_candidatos_a_edital(monkeypatch):
    dados_listagem = json.loads((FIXTURES / "listagem_recente_wp_json.json").read_text(encoding="utf-8"))

    def _fake_get(url, params=None, headers=None, timeout=None):
        assert url == sorocaba_sp.URL_POSTS
        return _RespostaFalsa(json_dados=dados_listagem)

    monkeypatch.setattr(script.requests, "get", _fake_get)

    processados = []

    def _fake_processar(conn, fonte_id, codigo_ibge, post):
        processados.append(post.id)
        return 1

    monkeypatch.setattr(script, "processar_post", _fake_processar)

    class _TransacaoFalsa:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _ConnFalsa:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def transaction(self):
            return _TransacaoFalsa()

    monkeypatch.setattr(script.db, "conectar", lambda: _ConnFalsa())
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda *a, **k: 3552205)
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(script.db, "upsert_fonte", lambda *a, **k: "fonte-x")
    monkeypatch.setattr(script.db, "registrar_cobertura_municipio", lambda *a, **k: None)

    script.main()

    # achado real: o post do Concurso 01/2026 está temporariamente fora
    # da listagem recente (bloqueio de "período eleitoral" do WordPress),
    # então nenhuma das 20 notícias recentes da fixture é candidata —
    # não é bug do parser, é limitação temporal documentada.
    assert processados == []
