"""Escrita direto no Postgres do Supabase via DATABASE_URL.

Mesmo padrão do runner de migrations do repo principal
(notifica-vagas/scripts/aplicar-migrations.mjs): conexão direta, dono da
tabela, ignora RLS — por isso nenhum client Supabase (supabase-py) é
necessário aqui, só psycopg.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg


def conectar() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL não definida. Copie a connection string do Postgres "
            "(Supabase > Project Settings > Database) para o ambiente."
        )
    return psycopg.connect(database_url)


def listar_nomes_municipios(conn: psycopg.Connection, ufs: list[str] | None = None) -> list[tuple[str, str]]:
    """(nome, uf) de todo município cadastrado — usado por fontes sem
    metadado de localização (ex: FGV) pra casar contra o título do
    concurso."""
    with conn.cursor() as cur:
        if ufs:
            cur.execute("select nome, uf from public.municipios where uf = any(%s)", (ufs,))
        else:
            cur.execute("select nome, uf from public.municipios")
        return [(row[0], row[1]) for row in cur.fetchall()]


def upsert_municipio(
    conn: psycopg.Connection,
    *,
    codigo_ibge: int,
    nome: str,
    uf: str,
    latitude: float | None = None,
    longitude: float | None = None,
    url_prefeitura: str | None = None,
    url_diario_oficial: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.municipios
                (codigo_ibge, nome, uf, latitude, longitude, url_prefeitura, url_diario_oficial)
            values (%(codigo_ibge)s, %(nome)s, %(uf)s, %(latitude)s, %(longitude)s,
                    %(url_prefeitura)s, %(url_diario_oficial)s)
            on conflict (codigo_ibge) do update set
                nome = excluded.nome,
                uf = excluded.uf,
                latitude = coalesce(excluded.latitude, public.municipios.latitude),
                longitude = coalesce(excluded.longitude, public.municipios.longitude),
                url_prefeitura = coalesce(excluded.url_prefeitura, public.municipios.url_prefeitura),
                url_diario_oficial = coalesce(excluded.url_diario_oficial, public.municipios.url_diario_oficial)
            """,
            {
                "codigo_ibge": codigo_ibge,
                "nome": nome,
                "uf": uf,
                "latitude": latitude,
                "longitude": longitude,
                "url_prefeitura": url_prefeitura,
                "url_diario_oficial": url_diario_oficial,
            },
        )


def upsert_fonte(
    conn: psycopg.Connection, *, nome: str, url: str, tipo: str, uf: str
) -> str:
    """tipo: 'indice' (agregador de descoberta) ou 'oficial' (fonte de verdade)."""
    with conn.cursor() as cur:
        cur.execute(
            "select id from public.fontes where nome = %(nome)s and url = %(url)s",
            {"nome": nome, "url": url},
        )
        row = cur.fetchone()
        if row:
            return str(row[0])

        cur.execute(
            """
            insert into public.fontes (nome, url, ativo, tipo, uf)
            values (%(nome)s, %(url)s, true, %(tipo)s, %(uf)s)
            returning id
            """,
            {"nome": nome, "url": url, "tipo": tipo, "uf": uf},
        )
        return str(cur.fetchone()[0])


def inserir_vaga_com_evidencia(
    conn: psycopg.Connection,
    *,
    fonte_id: str,
    municipio_id: int,
    identificador_externo: str,
    orgao: str | None,
    cargo: str,
    salario: Decimal | float | None,
    numero_edital: str | None,
    data_publicacao: date | None,
    inscricoes_inicio: date | None,
    inscricoes_fim: date | None,
    status: str,
    resumo: str | None,
    url_evidencia: str,
    tipo_documento: str,
    texto_extraido: str | None,
) -> dict[str, Any]:
    """Cria (ou reaproveita) a vaga canônica e sempre grava a evidência.

    Dedup: só reaproveita vaga existente em match exato de
    (municipio_id, orgao, cargo, numero_edital). A migration 002 documentou
    a regra sem `cargo` ("município+órgão+número de edital") pensando em
    evidências de fontes diferentes pra uma MESMA vaga — mas um edital real
    costuma listar vários cargos distintos (ex: ACS + ACE no mesmo
    Processo Seletivo nº 001/2026), e sem `cargo` na chave o segundo cargo
    era incorretamente absorvido pela vaga do primeiro. Ver TAREFAS.md.
    """
    with conn.cursor() as cur:
        vaga_id = None
        if numero_edital:
            cur.execute(
                """
                select id from public.vagas
                where municipio_id = %(municipio_id)s
                  and orgao = %(orgao)s
                  and cargo = %(cargo)s
                  and numero_edital = %(numero_edital)s
                """,
                {
                    "municipio_id": municipio_id,
                    "orgao": orgao,
                    "cargo": cargo,
                    "numero_edital": numero_edital,
                },
            )
            row = cur.fetchone()
            if row:
                vaga_id = row[0]

        if vaga_id is None:
            cur.execute(
                """
                insert into public.vagas
                    (municipio_id, orgao, cargo, salario, numero_edital,
                     data_publicacao, inscricoes_inicio, inscricoes_fim, status, resumo)
                values (%(municipio_id)s, %(orgao)s, %(cargo)s, %(salario)s, %(numero_edital)s,
                        %(data_publicacao)s, %(inscricoes_inicio)s, %(inscricoes_fim)s, %(status)s, %(resumo)s)
                returning id
                """,
                {
                    "municipio_id": municipio_id,
                    "orgao": orgao,
                    "cargo": cargo,
                    "salario": salario,
                    "numero_edital": numero_edital,
                    "data_publicacao": data_publicacao,
                    "inscricoes_inicio": inscricoes_inicio,
                    "inscricoes_fim": inscricoes_fim,
                    "status": status,
                    "resumo": resumo,
                },
            )
            vaga_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into public.vaga_evidencias
                (vaga_id, fonte_id, identificador_externo, url, tipo_documento, texto_extraido, verificado_por_ia)
            values (%(vaga_id)s, %(fonte_id)s, %(identificador_externo)s, %(url)s, %(tipo_documento)s, %(texto_extraido)s, false)
            on conflict (fonte_id, identificador_externo) do nothing
            returning id
            """,
            {
                "vaga_id": vaga_id,
                "fonte_id": fonte_id,
                "identificador_externo": identificador_externo,
                "url": url_evidencia,
                "tipo_documento": tipo_documento,
                "texto_extraido": texto_extraido,
            },
        )
        evidencia_row = cur.fetchone()

    return {"vaga_id": vaga_id, "evidencia_id": evidencia_row[0] if evidencia_row else None}
