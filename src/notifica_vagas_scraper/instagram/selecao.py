"""Escolha da vaga do dia: 1 post por dia.

Prioridade: vaga com inscrição acabando (`fim_prazo`, até 3 dias) que ainda
não foi postada assim; senão vaga nova dos últimos 7 dias (`nova`). Só vaga
aprovada, médica, aberta e verificada pela IA — mesmo critério do catálogo
do site. Sem candidata, não posta (não repete, não inventa).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

DIAS_FIM_PRAZO = 3
DIAS_NOVA = 7

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
    """Evita card vazio: precisa de cargo, órgão, município e ao menos
    remuneração ou fim de inscrição."""
    if not (vaga.get("cargo") and vaga.get("orgao") and vaga.get("municipio")):
        return False
    return vaga.get("salario") is not None or vaga.get("inscricoes_fim") is not None


def _chave_fim_prazo(vaga: dict[str, Any]) -> tuple:
    return (vaga["inscricoes_fim"], -(float(vaga["salario"]) if vaga.get("salario") is not None else 0.0))


def _chave_nova(vaga: dict[str, Any]) -> tuple:
    fim = vaga.get("inscricoes_fim") or date.max
    return (fim, -(float(vaga["salario"]) if vaga.get("salario") is not None else 0.0))


def escolher(
    candidatas_fim_prazo: list[dict[str, Any]],
    candidatas_nova: list[dict[str, Any]],
    hoje: date,
    agora: datetime | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Puro (testável): devolve ("fim_prazo"|"nova", vaga) ou None."""
    agora = agora or datetime.now(timezone.utc)

    fim_prazo = [
        v
        for v in candidatas_fim_prazo
        if vaga_publicavel(v)
        and v.get("inscricoes_fim") is not None
        and hoje <= v["inscricoes_fim"] <= hoje + timedelta(days=DIAS_FIM_PRAZO)
    ]
    if fim_prazo:
        return "fim_prazo", min(fim_prazo, key=_chave_fim_prazo)

    limite = agora - timedelta(days=DIAS_NOVA)
    novas = [
        v
        for v in candidatas_nova
        if vaga_publicavel(v)
        and v.get("detectada_em") is not None
        and v["detectada_em"] >= limite
        and (v.get("inscricoes_fim") is None or v["inscricoes_fim"] >= hoje)
    ]
    if novas:
        return "nova", min(novas, key=_chave_nova)
    return None


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
        cur.execute(_PREVIEW, {"excluir_id": excluir_id, "limite": limite})
        candidatas = cur.fetchall()
    return [v for v in candidatas if vaga_publicavel(v)][:limite]
