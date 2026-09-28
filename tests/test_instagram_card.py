from datetime import date

from notifica_vagas_scraper.instagram.card import formatar_salario, montar_html, rotulo_prazo
from notifica_vagas_scraper.instagram.legenda import montar_legenda

HOJE = date(2026, 9, 28)
VAGA = dict(
    cargo="MÉDICO SAÚDE NA HORA", orgao="Prefeitura Municipal de Ipatinga", municipio="Ipatinga", uf="MG",
    salario=6192.43, salario_tipo="mensal", tipo_oportunidade="processo_seletivo_temporario",
    numero_edital="01/2024", numero_vagas=1, taxa_inscricao=None, carga_horaria="20 horas semanais",
    data_prova=None, requisitos="Ensino Superior em Medicina <b>com registro</b> no CRM", inscricoes_inicio=None,
    inscricoes_fim=date(2026, 9, 30), banca_organizadora="Própria", tem_prova=False, exige_curriculo=True,
)


def test_formatos():
    assert formatar_salario(VAGA) == "R$ 6.192,43/mensal"
    assert formatar_salario({"salario": None}) == "Não informado"
    assert rotulo_prazo(HOJE, HOJE) == "encerra hoje"
    assert rotulo_prazo(date(2026, 9, 29), HOJE) == "encerra amanhã"
    assert rotulo_prazo(date(2026, 9, 30), HOJE) == "últimos dias — até 30/09"
    assert rotulo_prazo(date(2026, 10, 20), HOJE) == "inscrições abertas"


def test_html_traz_prazos_e_escapa_conteudo():
    html = montar_html(VAGA, "fim_prazo", HOJE)
    assert "FIM DAS INSCRIÇÕES" in html and "30/09/2026" in html
    assert "INÍCIO DAS INSCRIÇÕES" in html
    assert "ÚLTIMOS DIAS — ATÉ 30/09" in html
    assert "&lt;b&gt;" in html and "<b>com registro" not in html
    assert "Não informado" in html


def test_legenda_menciona_prazos_e_hashtags():
    legenda = montar_legenda(VAGA, "fim_prazo", HOJE)
    assert "Últimos dias" in legenda
    assert "Início das inscrições: Não informado" in legenda
    assert "Fim das inscrições: 30/09/2026" in legenda
    assert "#concursosmg" in legenda and "link na bio" in legenda
    assert "Vaga nova" in montar_legenda(VAGA, "nova", HOJE)
