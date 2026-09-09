from pathlib import Path

import rodar_itamonte_mg as script

from notifica_vagas_scraper.fontes import itamonte_mg

FIXTURES = Path(__file__).parent / "fixtures" / "itamonte_mg"


def _ler_bytes(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()


def _item_ps_001() -> itamonte_mg.ItemListagem:
    return itamonte_mg.ItemListagem(
        data=None,
        titulo="Edital e Rerratificações Processo Seletivo 001/2026",
        url="https://www.itamonte.mg.gov.br/imagens/licitacao/6f90c194798b02ccf0cfac0f8453a529.",
    )


class _RespostaFalsa:
    def __init__(self, *, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def test_processar_item_baixa_zip_e_nao_descarta_nenhum_medico(monkeypatch):
    # PRIORIDADE #1 do produto (CLAUDE.md): confirma que Médico ESF e
    # Médico CAPS aparecem, com a carga horária/salário da versão mais
    # recente que os define (1ª rerratificação) — 100% determinístico,
    # sem Gemini nenhum no caminho.
    conteudo_zip = _ler_bytes("ps_001_2026_pacote.zip")
    monkeypatch.setattr(script.requests, "get", lambda url, headers=None, timeout=None: _RespostaFalsa(content=conteudo_zip))
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda conn, nome, uf: None)
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: 3132305)

    cargos_gravados = []

    def _fake_inserir(conn, *, cargo, salario, salario_tipo, numero_edital, inscricoes_fim, **kwargs):
        cargos_gravados.append((cargo, salario, salario_tipo, numero_edital, inscricoes_fim))
        return {"vaga_id": len(cargos_gravados), "evidencia_id": len(cargos_gravados)}

    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_item(conn=None, fonte_id="fonte-x", item=_item_ps_001())

    assert total == 15
    cargos = {c for c, *_ in cargos_gravados}
    assert "Médico – CAPS" in cargos
    assert "Médico - ESF" in cargos

    caps = next(c for c in cargos_gravados if c[0] == "Médico – CAPS")
    assert caps[1] == 7964.38 or float(caps[1]) == 7964.38
    assert caps[2] == "mensal"
    assert caps[3] == "001/2026"
    assert caps[4].isoformat() == "2026-09-14"

    esf = next(c for c in cargos_gravados if c[0] == "Médico - ESF")
    assert float(esf[1]) == 17802.73


def test_processar_item_pdf_direto_tambem_funciona(monkeypatch):
    # achado real: nem todo item da listagem vem em ZIP — quando o link
    # já é um PDF puro (magic bytes %PDF), `extrair_pdfs` devolve o
    # próprio conteúdo, sem exigir descompactação nenhuma.
    conteudo_pdf = _ler_bytes("ps_001_2026_edital_original.pdf")
    monkeypatch.setattr(script.requests, "get", lambda url, headers=None, timeout=None: _RespostaFalsa(content=conteudo_pdf))
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda conn, nome, uf: None)
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: 3132305)

    cargos_gravados = []
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)
    monkeypatch.setattr(
        script.db,
        "inserir_vaga_com_evidencia",
        lambda conn, *, cargo, **kwargs: cargos_gravados.append(cargo) or {"vaga_id": 1, "evidencia_id": 1},
    )

    total = script.processar_item(conn=None, fonte_id="fonte-x", item=_item_ps_001())

    assert total == 15
    assert "Médico – CAPS" in cargos_gravados
    assert "Médico - ESF" in cargos_gravados


def test_processar_item_conteudo_desconhecido_pula_sem_erro(monkeypatch):
    monkeypatch.setattr(script.requests, "get", lambda url, headers=None, timeout=None: _RespostaFalsa(content=b"nao e pdf nem zip"))
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_item(conn=None, fonte_id="fonte-x", item=_item_ps_001())

    assert total == 0
    assert inserir_chamado == []


def test_processar_item_municipio_sem_codigo_ibge_pula_sem_erro(monkeypatch):
    conteudo_zip = _ler_bytes("ps_001_2026_pacote.zip")
    monkeypatch.setattr(script.requests, "get", lambda url, headers=None, timeout=None: _RespostaFalsa(content=conteudo_zip))
    monkeypatch.setattr(script.db, "buscar_codigo_ibge_local", lambda conn, nome, uf: None)
    monkeypatch.setattr(script.ibge, "buscar_codigo_ibge", lambda nome, uf: None)
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_item(conn=None, fonte_id="fonte-x", item=_item_ps_001())

    assert total == 0
    assert inserir_chamado == []
