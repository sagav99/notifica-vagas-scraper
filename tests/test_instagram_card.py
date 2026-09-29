from datetime import date

from notifica_vagas_scraper.instagram.card import (
    especialidade_curta,
    formatar_salario,
    formatar_valor_hora,
    montar_htmls,
    rotulo_prazo,
    valor_hora,
)
from notifica_vagas_scraper.instagram.legenda import montar_legenda

HOJE = date(2026, 9, 28)
VAGA = dict(
    id="v1", cargo="MÉDICO SAÚDE NA HORA", orgao="Prefeitura Municipal de Ipatinga", municipio="Ipatinga", uf="MG",
    salario=6192.43, salario_tipo="mensal", tipo_oportunidade="processo_seletivo_temporario",
    numero_edital="01/2024", numero_vagas=1, taxa_inscricao=None, carga_horaria="20 horas semanais",
    data_prova=None, requisitos="Ensino Superior em Medicina <b>com registro</b> no CRM", inscricoes_inicio=None,
    inscricoes_fim=date(2026, 9, 30), banca_organizadora="Própria", tem_prova=False, exige_curriculo=True,
)
VAGA_EXTRA_1 = dict(
    id="v2", cargo="Médico Ginecologista", orgao="Prefeitura Municipal de Jarinu", municipio="Jarinu", uf="SP",
    salario=8713.0, salario_tipo="mensal", tipo_oportunidade="concurso_efetivo", numero_edital="03/2026",
    numero_vagas=4, taxa_inscricao=90.0, carga_horaria="20h semanais", data_prova=None, requisitos=None,
    inscricoes_inicio=None, inscricoes_fim=date(2026, 10, 15), banca_organizadora="IBAM", tem_prova=True,
    exige_curriculo=False,
)
VAGA_EXTRA_2 = dict(
    id="v3", cargo="Médico Ortopedista", orgao="Município de São Carlos", municipio="São Carlos", uf="SP",
    salario=9833.0, salario_tipo="mensal", tipo_oportunidade="concurso_efetivo", numero_edital="02/2026",
    numero_vagas=1, taxa_inscricao=65.0, carga_horaria="20h semanais", data_prova=date(2026, 11, 22),
    requisitos=None, inscricoes_inicio=None, inscricoes_fim=date(2026, 10, 29), banca_organizadora="INDEPAC",
    tem_prova=True, exige_curriculo=False,
)


def test_formatos():
    assert formatar_salario(VAGA) == "R$ 6.192,43/mensal"
    assert formatar_salario({"salario": None}) == "Não informado"
    assert rotulo_prazo(HOJE, HOJE) == "encerra hoje"
    assert rotulo_prazo(date(2026, 9, 29), HOJE) == "encerra amanhã"
    assert rotulo_prazo(date(2026, 9, 30), HOJE) == "últimos dias — até 30/09"
    assert rotulo_prazo(date(2026, 10, 20), HOJE) == "inscrições abertas"


def test_especialidade_curta_remove_prefixo_medico():
    assert especialidade_curta("Médico Cardiologista") == "Cardiologista"
    assert especialidade_curta("MÉDICO GINECOLOGISTA") == "GINECOLOGISTA"
    assert especialidade_curta("Enfermeiro") == "Enfermeiro"  # sem prefixo "médico", devolve como está


def test_valor_hora_calculo_e_ausencia():
    vaga = dict(salario=9833.0, salario_tipo="mensal", carga_horaria="20h semanais")
    vh = valor_hora(vaga)
    assert vh is not None and round(vh, 2) == 113.46
    assert formatar_valor_hora(vaga) == "R$ 113,46"
    assert valor_hora({"salario": None}) is None
    assert valor_hora({"salario": 1000, "salario_tipo": "mensal", "carga_horaria": None}) is None
    assert valor_hora({"salario": 1000, "salario_tipo": "plantao", "carga_horaria": "20h"}) is None


def test_montar_htmls_gera_4_slides_com_conteudo_esperado():
    htmls = montar_htmls(VAGA, "fim_prazo", HOJE, [VAGA_EXTRA_1, VAGA_EXTRA_2])
    assert len(htmls) == 4

    slide1, slide2, slide3, slide4 = htmls
    assert "1/4" in slide1 and "VAGA COM PRAZO ACABANDO" in slide1
    assert "ÚLTIMOS DIAS — ATÉ 30/09" in slide1
    assert "SAÚDE NA HORA" in slide1  # especialidade sem o prefixo "MÉDICO"
    assert "&lt;b&gt;" in slide1 and "<b>com registro" not in slide1  # requisitos escapados
    assert "Não informado" in slide1

    assert "2/4" in slide2 and "O QUE PESA NA DECISÃO" in slide2 and "R$ 71,45" in slide2

    assert "3/4" in slide3 and "EDITAL OFICIAL" in slide3
    assert "registro no CRM" in slide3  # checklist fixo, não muda por vaga

    assert "4/4" in slide4 and "ESSA É SÓ" in slide4
    assert "Jarinu" in slide4 and "São Carlos" in slide4
    assert "R$29,99/mês" in slide4  # preço real do plano basic_mensal (lib/planos.ts no repo do site)


def test_montar_htmls_tipo_nova_usa_eyebrow_diferente():
    htmls = montar_htmls(VAGA, "nova", HOJE, [])
    assert "VAGA DA SEMANA" in htmls[0]
    assert "VAGA COM PRAZO ACABANDO" not in htmls[0]


def test_legenda_menciona_prazos_e_hashtags():
    legenda = montar_legenda(VAGA, "fim_prazo", HOJE)
    assert "Últimos dias" in legenda
    assert "Início das inscrições: Não informado" in legenda
    assert "Fim das inscrições: 30/09/2026" in legenda
    assert "#concursosmg" in legenda and "link na bio" in legenda
    assert "Vaga nova" in montar_legenda(VAGA, "nova", HOJE)
