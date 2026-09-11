from pathlib import Path

import rodar_bocaiuva_mg as script

from notifica_vagas_scraper import gemini_pdf
from notifica_vagas_scraper.fontes import bocaiuva_mg

FIXTURES = Path(__file__).parent / "fixtures" / "bocaiuva_mg"


def _ler_texto(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


class _RespostaFalsa:
    def __init__(self, *, text: str | None = None, content: bytes | None = None):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _item_81() -> bocaiuva_mg.ItemListagem:
    return bocaiuva_mg.ItemListagem(
        processo_id=81,
        numero_edital="3/2026",
        categoria="Edital",
        titulo="EDITAL DE PROCESSO SELETIVO SIMPLIFICADO N 03/2026 MODALIDADE: ANÁLISE DE TÍTULOS E EXPERIÊNCIA PROFISSIONAL",
        area="Saúde",
        data_publicacao=None,
        url="https://www.bocaiuva.mg.gov.br/concursos_view/81",
    )


def _extraido_gemini_edital_03_2026() -> dict:
    # espelha o que o Gemini (leitura visual — PDF escaneado, mesmo
    # achado de João Monlevade) extrairia do Edital 03/2026: cargo/
    # salário/requisitos não conferidos contra fonte de texto real (não
    # há PDF de conferência cruzada nas fixtures de Bocaiúva, diferente
    # de João Monlevade) — usado só pra testar o fluxo de gravação.
    return {
        "numero_edital": "3/2026",
        "orgao": "Secretaria Municipal de Saúde de Bocaiúva",
        "data_publicacao": "2026-04-16",
        "inscricoes_inicio": None,
        "inscricoes_fim": None,
        "taxa_inscricao": None,
        "data_prova": None,
        "tipo_oportunidade": "processo_seletivo_temporario",
        "vagas": [
            {
                "cargo": "Médico",
                "pagina": 1,
                "vagas_qtd": 2,
                "salario": 12000.0,
                "salario_tipo": "mensal",
                "requisitos": "Ensino Superior em Medicina; registro no CRM",
                "carga_horaria": "20 horas semanais",
            }
        ],
    }


def test_processar_processo_le_retificacao_via_gemini_e_grava_medico(monkeypatch):
    html_detalhe = _ler_texto("edital_03_2026_saude_medico_detalhe.html")
    pdf_bytes = b"%PDF-1.4 conteudo falso pra teste, nunca lido de verdade (gemini mockado)"

    respostas = {
        "https://www.bocaiuva.mg.gov.br/concursos_view/81": _RespostaFalsa(text=html_detalhe),
        "https://www.bocaiuva.mg.gov.br/licitacoes/92144bd45665291994b8b0b454aa6ee2.pdf": _RespostaFalsa(content=pdf_bytes),
    }

    def _fake_get(url, headers=None, timeout=None):
        return respostas[url]

    monkeypatch.setattr(script.requests, "get", _fake_get)
    # patch no módulo `gemini_pdf` de verdade (não em `script`, que não o
    # importa direto — quem chama é `processamento_pdf_gemini.py`, que
    # importou o MESMO objeto de módulo).
    monkeypatch.setattr(gemini_pdf, "extrair_vagas_de_pdf", lambda conteudo: _extraido_gemini_edital_03_2026())
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    gravados = []

    def _fake_inserir(conn, *, cargo, identificador_externo, url_evidencia, salario, salario_tipo, **kwargs):
        gravados.append((cargo, identificador_externo, url_evidencia, salario, salario_tipo))
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3107307, item=_item_81())

    assert total == 1
    cargo, identificador, url_evidencia, salario, salario_tipo = gravados[0]
    assert cargo == "Médico"
    # nunca usa anexo de Convocação/Homologação/Resultado como fonte
    # primária — sempre o PDF do edital de ABERTURA vigente (a
    # retificação mais recente entre os candidatos não-excluídos, ver
    # escolher_pdf_edital / test_bocaiuva_mg.py).
    assert url_evidencia == "https://www.bocaiuva.mg.gov.br/licitacoes/92144bd45665291994b8b0b454aa6ee2.pdf"
    assert identificador.startswith("bocaiuva-mg-81-")
    assert salario == 12000.0
    assert salario_tipo == "mensal"


_HTML_SO_COM_CONVOCACAO = """
<html><body>
<div id="print-view">
<div class="badge badge-primary px-3 py-2 shadow-sm">Edital nº 99/2026</div>
<label class="text-muted text-uppercase small">Súmula / Descrição</label>
<div><p>Edital 99/2026 Enfermeiro</p></div>
<label class="text-muted small">DATA DE PUBLICAÇÃO</label>
<p class="font-weight-bold">01/08/2026</p>
</div>
<table id="tb_anexos_concursos"><tbody>
<tr>
  <td>Convocação</td><td>Convocação processo 99/2026</td><td>20/08/2026</td>
  <td><a href="/licitacoes/convocacao.pdf">baixar</a></td>
</tr>
</tbody></table>
</body></html>
"""


def test_processar_processo_sem_edital_de_abertura_pula_sem_chamar_gemini(monkeypatch):
    # só documento de convocação listado ainda (edital de abertura não
    # apareceu, ou foi removido) — nunca inventa dado a partir dele;
    # também nunca gasta uma chamada de Gemini nesse caso.
    item = bocaiuva_mg.ItemListagem(
        processo_id=99,
        numero_edital="99/2026",
        categoria="Edital",
        titulo="Edital 99/2026 Enfermeiro",
        area="Saúde",
        data_publicacao=None,
        url="https://www.bocaiuva.mg.gov.br/concursos_view/99",
    )
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(text=_HTML_SO_COM_CONVOCACAO))

    chamou_gemini = []
    monkeypatch.setattr(gemini_pdf, "extrair_vagas_de_pdf", lambda *a, **k: chamou_gemini.append(1))
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3107307, item=item)

    assert total == 0
    assert chamou_gemini == []
    assert inserir_chamado == []
