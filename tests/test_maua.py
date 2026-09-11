from datetime import date
from io import BytesIO
from pathlib import Path

import pdfplumber

from notifica_vagas_scraper.fontes import maua

FIXTURES = Path(__file__).parent / "fixtures" / "maua"


def _ler_fixture_texto(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def _ler_fixture_bytes(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()


def _texto_pdf_real() -> str:
    with pdfplumber.open(BytesIO(_ler_fixture_bytes("edital_abertura_57_2026_medicos.pdf"))) as pdf:
        return "\n".join((pagina.extract_text() or "") for pagina in pdf.pages)


def test_extrair_edital_vigente_le_home_real():
    html = _ler_fixture_texto("processoseletivo_home_edital_57_2026.html")
    edital = maua.extrair_edital_vigente(html)

    assert edital is not None
    assert edital.numero_edital == "57/2026"
    assert edital.pdf_url == "https://processoseletivo.maua.sp.gov.br/public/docs/edital_abertura_57_2026.pdf"
    assert edital.inscricoes_inicio == date(2026, 9, 3)
    assert edital.inscricoes_fim == date(2026, 9, 17)


def test_extrair_edital_vigente_ignora_link_comentado_e_pega_o_visivel():
    # achado real da fonte: retificações antigas ficam comentadas em HTML
    # (`<!-- ... -->`) até a próxima retificação de verdade ser publicada
    # — um link comentado não pode ser confundido com o edital vigente.
    html = """
    <html><body>
    <!-- <p><a href="/public/docs/edital_retificacao_29_2026.pdf">Edital de Retificação 29/2026</a></p> -->
    <p><a href="/public/docs/edital_abertura_57_2026.pdf">Edital de Abertura 57/2026</a></p>
    </body></html>
    """
    edital = maua.extrair_edital_vigente(html)
    assert edital is not None
    assert edital.numero_edital == "57/2026"
    assert edital.pdf_url.endswith("edital_abertura_57_2026.pdf")


def test_extrair_edital_vigente_sem_link_retorna_none():
    assert maua.extrair_edital_vigente("<html><body>sem PSS no momento</body></html>") is None


def test_extrair_salario_por_hora_uniforme_no_pdf_real_do_edital_57_2026():
    # as 10 especialidades do Edital 57/2026 têm a mesma remuneração "R$
    # 130,00/hora + benefícios" — confirma que a heurística de
    # uniformidade acha o valor mesmo com a tabela embaralhada pela
    # extração linear do pdfplumber.
    valor = maua.extrair_salario_por_hora_uniforme(_texto_pdf_real())
    assert valor == 130.0


def test_extrair_salario_por_hora_uniforme_sem_ocorrencia_retorna_none():
    assert maua.extrair_salario_por_hora_uniforme("Edital sem nenhuma remuneração mencionada.") is None


def test_extrair_salario_por_hora_uniforme_valores_diferentes_retorna_none():
    # segurança: se um edital futuro tiver valor de hora DIFERENTE por
    # especialidade, a função nunca aplica um valor errado a outro cargo
    # — devolve None e deixa quem chama decidir (ver rodar_maua.py).
    texto = "Médico A R$ 130,00/ hora. Médico B R$ 150,00/ hora."
    assert maua.extrair_salario_por_hora_uniforme(texto) is None


def test_identificador_externo_estavel_e_sem_colisao_entre_cargos():
    id_cardiologista = maua.identificador_externo("57/2026", "Médico Cardiologista")
    id_dermatologista = maua.identificador_externo("57/2026", "Médico Dermatologista")
    assert id_cardiologista != id_dermatologista
    assert id_cardiologista == maua.identificador_externo("57/2026", "Médico Cardiologista")


def test_identificador_externo_sem_numero_edital_nao_quebra():
    assert maua.identificador_externo(None, "Médico Cardiologista") == "maua-sp-sem-numero-medico-cardiologista"
