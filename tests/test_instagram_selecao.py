from datetime import date, datetime, timedelta, timezone

from notifica_vagas_scraper.instagram.selecao import escolher, vaga_publicavel

HOJE = date(2026, 9, 28)
AGORA = datetime(2026, 9, 28, 15, tzinfo=timezone.utc)


def vaga(**kw):
    base = dict(id=kw.pop("id", "v"), cargo="Médico", orgao="Prefeitura", municipio="Ipatinga", uf="MG",
                salario=6000, inscricoes_fim=None, inscricoes_inicio=None, detectada_em=AGORA)
    base.update(kw)
    return base


def test_prefere_fim_de_prazo_mais_proximo():
    a = vaga(id="a", inscricoes_fim=HOJE + timedelta(days=3))
    b = vaga(id="b", inscricoes_fim=HOJE + timedelta(days=1))
    assert escolher([a, b], [], HOJE, AGORA) == ("fim_prazo", b)


def test_fim_de_prazo_vence_vaga_nova():
    fim = vaga(id="f", inscricoes_fim=HOJE)
    nova = vaga(id="n", inscricoes_fim=HOJE + timedelta(days=20))
    assert escolher([fim], [nova], HOJE, AGORA)[0] == "fim_prazo"


def test_sem_fim_de_prazo_pega_nova_recente_e_ignora_antiga():
    antiga = vaga(id="o", detectada_em=AGORA - timedelta(days=9))
    recente = vaga(id="r", detectada_em=AGORA - timedelta(days=2), inscricoes_fim=HOJE + timedelta(days=10))
    assert escolher([], [antiga, recente], HOJE, AGORA) == ("nova", recente)


def test_ignora_prazo_ja_vencido_e_prazo_longe():
    assert escolher([vaga(inscricoes_fim=HOJE - timedelta(days=1))], [], HOJE, AGORA) is None
    assert escolher([vaga(inscricoes_fim=HOJE + timedelta(days=10))], [], HOJE, AGORA) is None


def test_sem_candidata_nao_posta():
    assert escolher([], [], HOJE, AGORA) is None


def test_card_vazio_nao_e_publicavel():
    assert not vaga_publicavel(vaga(salario=None, inscricoes_fim=None))
    assert not vaga_publicavel(vaga(cargo=None))
    assert vaga_publicavel(vaga(salario=None, inscricoes_fim=HOJE))
