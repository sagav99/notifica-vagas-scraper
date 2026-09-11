from pathlib import Path

import rodar_pmjm_mg as script

from notifica_vagas_scraper import gemini_pdf
from notifica_vagas_scraper.fontes import pmjm_mg

FIXTURES = Path(__file__).parent / "fixtures" / "pmjm_mg"


def _ler_texto(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def _ler_bytes(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()


class _RespostaFalsa:
    def __init__(self, *, text: str | None = None, content: bytes | None = None):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _item_1420() -> pmjm_mg.ItemListagem:
    return pmjm_mg.ItemListagem(
        processo_id=1420,
        numero_edital="12/2026",
        categoria="Processos Seletivos",
        titulo="Edital 12-2026 Médico Plantonista - ORTOPEDISTA",
        data_publicacao=None,
        url="https://www.pmjm.mg.gov.br/concursos_view/1420",
    )


def _extraido_gemini_edital_12_2026_ortopedista() -> dict:
    # espelha o que o Gemini (leitura visual, o PDF real é escaneado/sem
    # texto — ver docstring de fontes/pmjm_mg.py) extrairia do Edital
    # 12/2026: cargo/salário/requisitos conferidos cruzando com o
    # "Resultado Preliminar" de texto real do MESMO processo (fixture
    # resultado_preliminar_12_2026_texto_com_edital_completo.pdf, que
    # repete a mesma tabela de especificação do cargo).
    return {
        "numero_edital": "12/2026",
        "orgao": "Secretaria Municipal de Saúde de João Monlevade",
        "data_publicacao": "2026-07-09",
        "inscricoes_inicio": None,
        "inscricoes_fim": None,
        "taxa_inscricao": None,
        "data_prova": None,
        "tipo_oportunidade": "processo_seletivo_temporario",
        "vagas": [
            {
                "cargo": "Médico Plantonista (Ortopedista)",
                "pagina": 1,
                "vagas_qtd": 1,
                "salario": 820.14,
                "salario_tipo": "plantao",
                "requisitos": "Ensino Superior em Medicina; especialização em Ortopedia; registro no CRM",
                "carga_horaria": "Plantão de 06 horas corridas por dia",
            }
        ],
    }


def test_processar_processo_le_pdf_escaneado_via_gemini_e_grava_medico(monkeypatch):
    html_detalhe = _ler_texto("concursos_view_1420_medico_ortopedista.html")
    pdf_bytes = _ler_bytes("edital_12_2026_ortopedista_escaneado.pdf")

    respostas = {
        "https://www.pmjm.mg.gov.br/concursos_view/1420": _RespostaFalsa(text=html_detalhe),
        "https://www.pmjm.mg.gov.br/concursos/d884ec4ef59af2a07c06d85a0df75f7d.pdf": _RespostaFalsa(content=pdf_bytes),
    }

    def _fake_get(url, headers=None, timeout=None):
        return respostas[url]

    monkeypatch.setattr(script.requests, "get", _fake_get)
    # patch no módulo `gemini_pdf` de verdade (não em `script`, que não o
    # importa direto — quem chama é `processamento_pdf_gemini.py`, que
    # importou o MESMO objeto de módulo).
    monkeypatch.setattr(gemini_pdf, "extrair_vagas_de_pdf", lambda conteudo: _extraido_gemini_edital_12_2026_ortopedista())
    monkeypatch.setattr(script.db, "upsert_municipio", lambda *a, **k: None)

    gravados = []

    def _fake_inserir(conn, *, cargo, identificador_externo, url_evidencia, salario, salario_tipo, **kwargs):
        gravados.append((cargo, identificador_externo, url_evidencia, salario, salario_tipo))
        return {"vaga_id": 1, "evidencia_id": 1}

    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", _fake_inserir)

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3138203, item=_item_1420())

    assert total == 1
    cargo, identificador, url_evidencia, salario, salario_tipo = gravados[0]
    assert cargo == "Médico Plantonista (Ortopedista)"
    # nunca usa Resultado Preliminar/Final como fonte primária — sempre o
    # PDF do edital de ABERTURA (achado real: os 3 anexos do processo têm
    # Tipo="Edital", só a Descrição diferencia — ver escolher_pdf_edital).
    assert url_evidencia == "https://www.pmjm.mg.gov.br/concursos/d884ec4ef59af2a07c06d85a0df75f7d.pdf"
    assert identificador.startswith("pmjm-mg-1420-")
    assert salario == 820.14
    assert salario_tipo == "plantao"


_HTML_SO_COM_RESULTADO = """
<html><body>
<table id="tb_concursos">
<tr><td>Nº/ Ano:</td><td>99/2026</td></tr>
<tr><td>Tipo:</td><td>Processos Seletivos</td></tr>
<tr><td>Súmula:</td><td><p>Edital 99-2026 Enfermeiro</p></td></tr>
<tr><td>Data:</td><td>01/08/2026</td></tr>
</table>
<table id="tb_anexos_concursos"><tbody>
<tr>
  <td>Edital</td><td>Resultado Final PS 99-2026 Enfermeiro</td><td>20/08/2026</td>
  <td><a href="/concursos/resultado.pdf">baixar</a></td>
</tr>
</tbody></table>
</body></html>
"""


def test_processar_processo_sem_edital_de_abertura_pula_sem_chamar_gemini(monkeypatch):
    # só documento de resultado listado ainda (edital de abertura não
    # apareceu, ou foi removido) — nunca inventa dado a partir dele;
    # também nunca gasta uma chamada de Gemini nesse caso.
    item = pmjm_mg.ItemListagem(
        processo_id=99,
        numero_edital="99/2026",
        categoria="Processos Seletivos",
        titulo="Edital 99-2026 Enfermeiro",
        data_publicacao=None,
        url="https://www.pmjm.mg.gov.br/concursos_view/99",
    )
    monkeypatch.setattr(script.requests, "get", lambda *a, **k: _RespostaFalsa(text=_HTML_SO_COM_RESULTADO))

    chamou_gemini = []
    monkeypatch.setattr(gemini_pdf, "extrair_vagas_de_pdf", lambda *a, **k: chamou_gemini.append(1))
    inserir_chamado = []
    monkeypatch.setattr(script.db, "inserir_vaga_com_evidencia", lambda *a, **k: inserir_chamado.append(1))

    total = script.processar_processo(conn=None, fonte_id="fonte-x", codigo_ibge=3138203, item=item)

    assert total == 0
    assert chamou_gemini == []
    assert inserir_chamado == []
