from datetime import date, timedelta

from notifica_vagas_scraper.instagram.selecao import escolher, vaga_publicavel

HOJE = date(2026, 9, 28)


def vaga(**kw):
    base = dict(id=kw.pop("id", "v"), cargo="Médico", orgao="Prefeitura", municipio="Ipatinga", uf="MG",
                salario=6000, inscricoes_fim=HOJE + timedelta(days=20), carga_horaria="20h semanais",
                banca_organizadora="IBGP", requisitos="CRM", tipo_oportunidade="concurso")
    base.update(kw)
    return base


def test_prazo_curto_vira_tipo_fim_prazo_e_prazo_longo_vira_nova():
    assert escolher([vaga(id="a", inscricoes_fim=HOJE + timedelta(days=2))], [], HOJE)[0] == "fim_prazo"
    assert escolher([], [vaga(id="b")], HOJE)[0] == "nova"


def test_prazo_curto_nao_tem_prioridade_e_escolha_varia_com_o_dia():
    vagas = [vaga(id=f"v{i}", inscricoes_fim=HOJE + timedelta(days=60 + i)) for i in range(10)]
    escolhidas = {escolher([], vagas, HOJE + timedelta(days=d))[1]["id"] for d in range(30)}
    assert len(escolhidas) > 3


def test_escolha_e_estavel_no_mesmo_dia_e_une_as_duas_listas():
    a, b = vaga(id="a"), vaga(id="b")
    assert escolher([a], [a, b], HOJE) == escolher([a], [a, b], HOJE)


def test_ignora_vaga_encerrada_ou_incompleta():
    assert escolher([], [vaga(inscricoes_fim=HOJE - timedelta(days=1))], HOJE) is None
    assert escolher([], [vaga(carga_horaria=None)], HOJE) is None


def test_sem_candidata_nao_posta():
    assert escolher([], [], HOJE) is None


def test_vaga_precisa_de_todos_os_dados():
    assert vaga_publicavel(vaga())
    for campo in ("salario", "inscricoes_fim", "banca_organizadora", "requisitos", "cargo"):
        assert not vaga_publicavel(vaga(**{campo: None}))
