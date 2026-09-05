"""Escrita direto no Postgres do Supabase via DATABASE_URL.

Mesmo padrão do runner de migrations do repo principal
(notifica-vagas/scripts/aplicar-migrations.mjs): conexão direta, dono da
tabela, ignora RLS — por isso nenhum client Supabase (supabase-py) é
necessário aqui, só psycopg.
"""

from __future__ import annotations

import os
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

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


def listar_municipios_com_codigo(
    conn: psycopg.Connection, ufs: list[str] | None = None
) -> list[tuple[int, str, str]]:
    """(codigo_ibge, nome, uf) — usado por fonte de descoberta ampla
    (PCI Concursos, Google News RSS) que casa o título contra o nome do
    município (igual `listar_nomes_municipios`) mas depois precisa do
    código IBGE pra persistir o sinal, sem chamar a API externa do IBGE
    de novo pra um município que já está no nosso próprio cadastro."""
    with conn.cursor() as cur:
        if ufs:
            cur.execute("select codigo_ibge, nome, uf from public.municipios where uf = any(%s)", (ufs,))
        else:
            cur.execute("select codigo_ibge, nome, uf from public.municipios")
        return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def listar_dominios_fontes_conhecidas(conn: psycopg.Connection) -> set[str]:
    """Domínios (netloc) de toda `fontes.url` já cadastrada — usado por
    fonte de descoberta ampla pra decidir se um link externo citado numa
    notícia/RSS já é coberto por um parser oficial (mera confirmação) ou
    é candidato a fonte nova de verdade (ver `sinais_descoberta_externa`,
    migration 015)."""
    from urllib.parse import urlparse

    with conn.cursor() as cur:
        cur.execute("select url from public.fontes")
        return {urlparse(row[0]).netloc.lower() for row in cur.fetchall() if row[0]}


def registrar_sinal_descoberta(
    conn: psycopg.Connection,
    *,
    fonte_descoberta: str,
    municipio_id: int,
    titulo: str,
    url: str,
    dominios_externos: list[str],
    coberto_por_fonte_oficial: bool,
) -> bool:
    """Grava 1 sinal de descoberta ampla (PCI Concursos, Google News RSS)
    em `sinais_descoberta_externa` (migration 015). Idempotente por `url`
    (on conflict do nothing) — devolve True só quando é sinal novo, pra
    quem chama poder logar "X novo(s) de Y encontrados" sem duplicar
    contagem em execuções repetidas do mesmo dia."""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.sinais_descoberta_externa
                (fonte_descoberta, municipio_id, titulo, url, dominios_externos, coberto_por_fonte_oficial)
            values (%(fonte_descoberta)s, %(municipio_id)s, %(titulo)s, %(url)s,
                    %(dominios_externos)s, %(coberto_por_fonte_oficial)s)
            on conflict (url) do nothing
            returning id
            """,
            {
                "fonte_descoberta": fonte_descoberta,
                "municipio_id": municipio_id,
                "titulo": titulo,
                "url": url,
                "dominios_externos": dominios_externos,
                "coberto_por_fonte_oficial": coberto_por_fonte_oficial,
            },
        )
        return cur.fetchone() is not None


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
    salario_tipo: str | None,
    tipo_oportunidade: str | None,
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
                    (municipio_id, orgao, cargo, salario, salario_tipo, tipo_oportunidade,
                     numero_edital, data_publicacao, inscricoes_inicio, inscricoes_fim, status, resumo)
                values (%(municipio_id)s, %(orgao)s, %(cargo)s, %(salario)s, %(salario_tipo)s,
                        %(tipo_oportunidade)s, %(numero_edital)s, %(data_publicacao)s,
                        %(inscricoes_inicio)s, %(inscricoes_fim)s, %(status)s, %(resumo)s)
                returning id
                """,
                {
                    "municipio_id": municipio_id,
                    "orgao": orgao,
                    "cargo": cargo,
                    "salario": salario,
                    "salario_tipo": salario_tipo,
                    "tipo_oportunidade": tipo_oportunidade,
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


def listar_vagas_pendentes(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Vagas com revisao_status='pendente', com dados de município e
    evidências — usado pela revisão automática via Gemini
    (revisao_ia.py + scripts/revisar_vagas.py)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select v.id, v.orgao, v.cargo, v.salario, v.numero_edital,
                   v.data_publicacao, v.inscricoes_inicio, v.inscricoes_fim,
                   v.status, v.resumo, m.nome, m.uf
            from public.vagas v
            join public.municipios m on m.codigo_ibge = v.municipio_id
            where v.revisao_status = 'pendente'
            order by v.detectada_em
            """
        )
        colunas = [
            "id", "orgao", "cargo", "salario", "numero_edital", "data_publicacao",
            "inscricoes_inicio", "inscricoes_fim", "status", "resumo",
            "municipio_nome", "municipio_uf",
        ]
        vagas = [dict(zip(colunas, row)) for row in cur.fetchall()]

    with conn.cursor() as cur:
        for vaga in vagas:
            cur.execute(
                """
                select ve.url, ve.tipo_documento, ve.texto_extraido, f.nome
                from public.vaga_evidencias ve
                join public.fontes f on f.id = ve.fonte_id
                where ve.vaga_id = %(vaga_id)s
                """,
                {"vaga_id": vaga["id"]},
            )
            vaga["evidencias"] = [
                {"url": row[0], "tipo_documento": row[1], "texto_extraido": row[2], "fonte": row[3]}
                for row in cur.fetchall()
            ]
    return vagas


def aplicar_revisao(conn: psycopg.Connection, *, vaga_id: str, decisao: str, motivo: str) -> None:
    """Grava o resultado da revisão automática via Gemini: revisao_status,
    revisao_motivo, revisado_em; revisado_por fica NULL (sem humano — ver
    docs/revisao_automatica_gemini.md no repo principal). Marca toda
    evidência da vaga como verificado_por_ia=true — o Gemini avaliou os
    dados extraídos de todas elas nesta mesma chamada."""
    with conn.cursor() as cur:
        cur.execute(
            """
            update public.vagas
            set revisao_status = %(decisao)s,
                revisao_motivo = %(motivo)s,
                revisado_em = now(),
                revisado_por = null
            where id = %(vaga_id)s
            """,
            {"decisao": decisao, "motivo": motivo, "vaga_id": vaga_id},
        )
        cur.execute(
            "update public.vaga_evidencias set verificado_por_ia = true where vaga_id = %(vaga_id)s",
            {"vaga_id": vaga_id},
        )


def _gravar_execucao(
    script: str, *, iniciado_em: datetime, status: str, detalhe: str | None
) -> None:
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into public.execucoes_scraper (script, iniciado_em, status, detalhe)
            values (%(script)s, %(iniciado_em)s, %(status)s, %(detalhe)s)
            """,
            {"script": script, "iniciado_em": iniciado_em, "status": status, "detalhe": detalhe},
        )
        conn.commit()


@contextmanager
def rastrear_execucao(script: str) -> Iterator[None]:
    """Registra em `public.execucoes_scraper` o resultado de rodar um
    `scripts/rodar_*.py`/`revisar_vagas.py` inteiro — resolve item
    pendente do TAREFAS.md ("Acompanhar falhas de monitoramento"): antes
    disso, uma falha (ex: canário do DOM/AMM-MG abortando por
    throttling, timeout do IMESO no IBGE) só existia no log do GitHub
    Actions, sem histórico consultável.

    Uso: `with db.rastrear_execucao("rodar_instar.py"): ...corpo do
    main()...`. Grava "sucesso" se o bloco terminar sem levantar,
    "falha" com o traceback (truncado) se levantar — e sempre relança a
    exceção original, nunca a engole (quem chama continua decidindo o
    que fazer com a falha, ex: não derrubar os outros steps do cron)."""
    inicio = datetime.now(timezone.utc)
    try:
        yield
    except Exception:
        _gravar_execucao(script, iniciado_em=inicio, status="falha", detalhe=traceback.format_exc()[-4000:])
        raise
    else:
        _gravar_execucao(script, iniciado_em=inicio, status="sucesso", detalhe=None)
