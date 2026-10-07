"""Escolha da vaga do dia: 1 post por dia.

Sorteia (estável por dia) uma vaga aberta e com todos os dados (variedade: prazo
longo ou curto, recém-publicada ou não), ainda não postada. Só vaga aprovada,
médica e verificada pela IA — mesmo critério do catálogo do site. Sem candidata,
não posta (não repete, não inventa).
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

DIAS_FIM_PRAZO = 3

_COLUNAS = """
    v.id, v.cargo, v.orgao, v.salario, v.salario_tipo, v.tipo_oportunidade,
    v.numero_edital, v.numero_vagas, v.taxa_inscricao, v.carga_horaria,
    v.data_prova, v.requisitos, v.inscricoes_inicio, v.inscricoes_fim,
    v.banca_organizadora, v.tem_prova, v.exige_curriculo, v.detectada_em,
    m.nome as municipio, m.uf
"""

_BASE = f"""
    select {_COLUNAS}
    from public.vagas v
    join public.municipios m on m.codigo_ibge = v.municipio_id
    where v.revisao_status = 'aprovada'
      and v.categoria_saude = 'medico'
      and v.status = 'aberta'
      and exists (
        select 1 from public.vaga_evidencias ve
        where ve.vaga_id = v.id and ve.verificado_por_ia = true
      )
      and not exists (
        select 1 from public.posts_instagram p
        where p.vaga_id = v.id and p.tipo = %(tipo)s
      )
"""


def vaga_publicavel(vaga: dict[str, Any]) -> bool:
    """Só vaga com todos os dados que o card e a página de divulgação mostram."""
    obrigatorios = ("cargo", "orgao", "municipio", "salario", "inscricoes_fim", "carga_horaria", "banca_organizadora", "requisitos", "tipo_oportunidade")
    return all(vaga.get(campo) not in (None, "") for campo in obrigatorios)


def escolher(
    candidatas_fim_prazo: list[dict[str, Any]],
    candidatas_nova: list[dict[str, Any]],
    hoje: date,
    agora: datetime | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Puro (testável): sorteia, de forma estável por dia, uma vaga aberta e completa.

    Devolve ("fim_prazo"|"nova", vaga) ou None. "fim_prazo" só descreve o selo do
    card (prazo em até DIAS_FIM_PRAZO dias); não tem mais prioridade na escolha.
    """
    por_id: dict[Any, dict[str, Any]] = {}
    for v in candidatas_fim_prazo:
        por_id[v["id"]] = v
    for v in candidatas_nova:
        por_id.setdefault(v["id"], v)
    abertas = sorted(
        (v for v in por_id.values() if vaga_publicavel(v) and v["inscricoes_fim"] >= hoje),
        key=lambda v: str(v["id"]),
    )
    if not abertas:
        return None
    vaga = random.Random(hoje.toordinal()).choice(abertas)
    tipo = "fim_prazo" if vaga["inscricoes_fim"] <= hoje + timedelta(days=DIAS_FIM_PRAZO) else "nova"
    return tipo, vaga


def selecionar_vaga(conn: psycopg.Connection, hoje: date) -> tuple[str, dict[str, Any]] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_BASE, {"tipo": "fim_prazo"})
        fim_prazo = cur.fetchall()
        cur.execute(_BASE, {"tipo": "nova"})
        nova = cur.fetchall()
    return escolher(fim_prazo, nova, hoje)


_PREVIEW = f"""
    select {_COLUNAS}
    from public.vagas v
    join public.municipios m on m.codigo_ibge = v.municipio_id
    where v.revisao_status = 'aprovada'
      and v.categoria_saude = 'medico'
      and v.status = 'aberta'
      and v.id != %(excluir_id)s
      and v.inscricoes_fim >= (now() at time zone 'America/Sao_Paulo')::date + 7
      and exists (
        select 1 from public.vaga_evidencias ve
        where ve.vaga_id = v.id and ve.verificado_por_ia = true
      )
    order by v.inscricoes_fim nulls last, v.detectada_em desc
    limit %(limite)s
"""


def vagas_preview(conn: psycopg.Connection, excluir_id: Any, limite: int = 2) -> list[dict[str, Any]]:
    """Até `limite` outras vagas médicas publicáveis (slide 4 do carrossel), diferentes de `excluir_id`."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_PREVIEW, {"excluir_id": excluir_id, "limite": limite * 25})  # sobra para o filtro de vaga completa
        candidatas = cur.fetchall()
    return [v for v in candidatas if vaga_publicavel(v)][:limite]


def vaga_por_id(conn: psycopg.Connection, vaga_id: str, hoje: date) -> tuple[str, dict[str, Any]] | None:
    """Vaga escolhida à mão (--vaga-id): mesmos filtros da seleção automática, sem sorteio."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_BASE + " and v.id = %(vaga_id)s", {"tipo": "fim_prazo", "vaga_id": vaga_id})
        vaga = cur.fetchone()
    if not vaga or not vaga_publicavel(vaga) or vaga["inscricoes_fim"] < hoje:
        return None
    tipo = "fim_prazo" if vaga["inscricoes_fim"] <= hoje + timedelta(days=DIAS_FIM_PRAZO) else "nova"
    return tipo, vaga
