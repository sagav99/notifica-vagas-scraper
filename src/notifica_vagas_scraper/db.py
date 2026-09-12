"""Escrita direto no Postgres do Supabase via DATABASE_URL.

Mesmo padrão do runner de migrations do repo principal
(notifica-vagas/scripts/aplicar-migrations.mjs): conexão direta, dono da
tabela, ignora RLS — por isso nenhum client Supabase (supabase-py) é
necessário aqui, só psycopg.
"""

from __future__ import annotations

import os
import traceback
import unicodedata
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


def _normalizar_nome_municipio(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return sem_acento.strip().lower()


_cache_local_codigo_por_uf: dict[str, list[tuple[int, str]]] = {}


def buscar_codigo_ibge_local(conn: psycopg.Connection, nome: str, uf: str) -> int | None:
    """codigo_ibge de um município já cadastrado em `public.municipios`,
    por nome normalizado (sem acento/case) — evita bater na API externa
    do IBGE por item processado. Achado real (2026-09-09): a partir do
    runner do GitHub Actions, `servicodados.ibge.gov.br` passou a dar
    timeout de forma persistente (não esporádica) em pelo menos 3 crons
    diários seguidos (06-08/09), derrubando silenciosamente vaga já
    extraída via Gemini (FGV/IMESO/IMAM/Ache Concursos todos chamavam a
    API externa 1x por item, mesmo o município já estando no nosso
    próprio cadastro) — mesma rede funciona normal de fora do Actions.
    Cacheado em memória por UF dentro do processo, mesmo padrão de
    `ibge.listar_municipios`."""
    if uf not in _cache_local_codigo_por_uf:
        with conn.cursor() as cur:
            cur.execute("select codigo_ibge, nome from public.municipios where uf = %s", (uf,))
            _cache_local_codigo_por_uf[uf] = [(row[0], row[1]) for row in cur.fetchall()]

    alvo = _normalizar_nome_municipio(nome)
    for codigo, nome_db in _cache_local_codigo_por_uf[uf]:
        if _normalizar_nome_municipio(nome_db) == alvo:
            return codigo
    return None


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


def listar_identificadores_processados(conn: psycopg.Connection, fonte_id: str) -> set[str]:
    """Todo `identificador_externo` já gravado em `vaga_evidencias` pra uma
    fonte — usado por fonte com listagem CUMULATIVA de histórico completo
    (ex: `fontes/pmjm_mg.py`, que expõe 1000+ processos desde 2011 numa
    página só, sem filtro nativo de "só o vigente") pra decidir quais itens
    pular ANTES de baixar PDF/chamar Gemini de novo, em vez de confiar só
    no `on conflict do nothing` de `inserir_vaga_com_evidencia` (que evita
    duplicar linha no banco, mas não evita o custo de rede/IA de
    reprocessar um item antigo a cada cron)."""
    with conn.cursor() as cur:
        cur.execute(
            "select identificador_externo from public.vaga_evidencias where fonte_id = %(fonte_id)s",
            {"fonte_id": fonte_id},
        )
        return {row[0] for row in cur.fetchall()}


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


def registrar_cobertura_municipio(
    conn: psycopg.Connection, *, fonte: str, municipio_id: int, status: str, detalhe: str | None = None
) -> None:
    """Grava/atualiza a linha de cobertura de 1 município numa fonte —
    migration 024, decisão do usuário 2026-09-11: banco vira fonte da
    verdade do roster (`municipios_instar.csv` etc. viram só cache local),
    destravando a view de cobertura do painel `/admin` (Next.js não lê
    arquivo do repo scraper em runtime na Vercel). `status`: 'coberto' (achou
    processo/edital nesta rodada), 'sem_dados' (endpoint respondeu, mas
    vazio) ou 'erro' (falha de rede/parsing, não dá pra saber se tem dado).
    Chamar 1x por município a cada rodada do script correspondente — a
    própria coluna `atualizado_em` funciona como "última checagem", sem
    precisar de tabela de execução separada pra isso."""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.cobertura_municipios (fonte, municipio_id, status, detalhe, atualizado_em)
            values (%(fonte)s, %(municipio_id)s, %(status)s, %(detalhe)s, now())
            on conflict (fonte, municipio_id) do update set
                status = excluded.status,
                detalhe = excluded.detalhe,
                atualizado_em = excluded.atualizado_em
            """,
            {"fonte": fonte, "municipio_id": municipio_id, "status": status, "detalhe": detalhe},
        )


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
    pagina_pdf: int | None = None,
    url_print_pagina: str | None = None,
    numero_vagas: int | None = None,
    taxa_inscricao: Decimal | float | None = None,
    carga_horaria: str | None = None,
    valor_hora: Decimal | float | None = None,
    data_prova: date | None = None,
    requisitos: str | None = None,
    banca_organizadora: str | None = None,
    tem_prova: bool | None = None,
    exige_curriculo: bool | None = None,
) -> dict[str, Any]:
    """Cria (ou reaproveita) a vaga canônica e sempre grava a evidência.

    Dedup: só reaproveita vaga existente em match exato de
    (municipio_id, orgao, cargo, numero_edital). A migration 002 documentou
    a regra sem `cargo` ("município+órgão+número de edital") pensando em
    evidências de fontes diferentes pra uma MESMA vaga — mas um edital real
    costuma listar vários cargos distintos (ex: ACS + ACE no mesmo
    Processo Seletivo nº 001/2026), e sem `cargo` na chave o segundo cargo
    era incorretamente absorvido pela vaga do primeiro. Ver TAREFAS.md.

    `numero_vagas`/`taxa_inscricao`/`carga_horaria`/`valor_hora`/
    `data_prova`/`requisitos` (migration 018, 2026-09-10) e
    `banca_organizadora`/`tem_prova`/`exige_curriculo` (migration 028,
    2026-09-12) só são gravados na CRIAÇÃO da vaga, igual todo outro campo
    aqui — se a vaga já existir (dedup), passar esses campos de novo não
    atualiza a linha existente (mesma limitação que já valia pra
    salario/status antes desta mudança, não é regressão nova). Vaga já
    existente com campo faltante é reprocessada por
    `scripts/auditar_completude_vagas.py`, não por aqui.
    """
    with conn.cursor() as cur:
        vaga_id = None
        vaga_criada = False
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
                     numero_edital, data_publicacao, inscricoes_inicio, inscricoes_fim, status, resumo,
                     numero_vagas, taxa_inscricao, carga_horaria, valor_hora, data_prova, requisitos,
                     banca_organizadora, tem_prova, exige_curriculo)
                values (%(municipio_id)s, %(orgao)s, %(cargo)s, %(salario)s, %(salario_tipo)s,
                        %(tipo_oportunidade)s, %(numero_edital)s, %(data_publicacao)s,
                        %(inscricoes_inicio)s, %(inscricoes_fim)s, %(status)s, %(resumo)s,
                        %(numero_vagas)s, %(taxa_inscricao)s, %(carga_horaria)s, %(valor_hora)s,
                        %(data_prova)s, %(requisitos)s, %(banca_organizadora)s, %(tem_prova)s,
                        %(exige_curriculo)s)
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
                    "numero_vagas": numero_vagas,
                    "taxa_inscricao": taxa_inscricao,
                    "carga_horaria": carga_horaria,
                    "valor_hora": valor_hora,
                    "data_prova": data_prova,
                    "requisitos": requisitos,
                    "banca_organizadora": banca_organizadora,
                    "tem_prova": tem_prova,
                    "exige_curriculo": exige_curriculo,
                },
            )
            vaga_id = cur.fetchone()[0]
            vaga_criada = True

        cur.execute(
            """
            insert into public.vaga_evidencias
                (vaga_id, fonte_id, identificador_externo, url, tipo_documento, texto_extraido,
                 verificado_por_ia, pagina_pdf, url_print_pagina)
            values (%(vaga_id)s, %(fonte_id)s, %(identificador_externo)s, %(url)s, %(tipo_documento)s,
                    %(texto_extraido)s, false, %(pagina_pdf)s, %(url_print_pagina)s)
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
                "pagina_pdf": pagina_pdf,
                "url_print_pagina": url_print_pagina,
            },
        )
        evidencia_row = cur.fetchone()

    return {"vaga_id": vaga_id, "evidencia_id": evidencia_row[0] if evidencia_row else None, "vaga_criada": vaga_criada}


def listar_vagas_pendentes(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Vagas com revisao_status='pendente', com dados de município e
    evidências — usado pela revisão automática via Gemini
    (revisao_ia.py + scripts/revisar_vagas.py)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select v.id, v.orgao, v.cargo, v.salario, v.salario_tipo, v.numero_edital,
                   v.data_publicacao, v.inscricoes_inicio, v.inscricoes_fim,
                   v.status, v.resumo, m.nome, m.uf
            from public.vagas v
            join public.municipios m on m.codigo_ibge = v.municipio_id
            where v.revisao_status = 'pendente'
            order by v.detectada_em
            """
        )
        colunas = [
            "id", "orgao", "cargo", "salario", "salario_tipo", "numero_edital", "data_publicacao",
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


def listar_vagas_revisadas_para_consistencia(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Vagas já revisadas (aprovada/rejeitada/incompleta), com os mesmos
    campos que `listar_vagas_pendentes` usa pra montar o payload de
    revisão, mais `fonte_id`/`municipio_id` — usado por
    `scripts/verificar_consistencia_revisao.py` pra agrupar "vagas irmãs"
    do mesmo edital real (mesma fonte + mesmo município + mesmo
    `numero_edital`) e achar decisão que diverge da maioria (achado da
    auditoria de revisão, 2026-09-11: a revisão não é determinística —
    mesmo dado de entrada, decisão diferente chamada a chamada). Só
    considera a PRIMEIRA evidência de cada vaga pra `fonte_id` — uma vaga
    tem no máximo 1 fonte na prática (achado confirmado em
    `aplicar_revisao`, que já marca toda evidência da vaga junto).

    Exclui vaga rejeitada pelo filtro determinístico de médico
    (`revisao_motivo` começando com `[filtro médico]`, ver
    `scripts/revisar_vagas.py`/`classificar_medico.py`, 2026-09-12): sem
    isso, um edital com vários cargos não-médico (maioria, sempre
    rejeitada por profissão) e 1 médico aprovado marcaria o médico como
    "divergente da maioria" e arriscaria reverter a aprovação real — essa
    2ª passada existe pra corrigir inconsistência do Gemini, não faz
    sentido pra decisão que nunca passou por ele."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select v.id, v.orgao, v.cargo, v.salario, v.salario_tipo, v.numero_edital,
                   v.data_publicacao, v.inscricoes_inicio, v.inscricoes_fim,
                   v.status, v.resumo, v.revisao_status, v.municipio_id, m.nome, m.uf,
                   (
                     select ve.fonte_id from public.vaga_evidencias ve
                     where ve.vaga_id = v.id
                     order by ve.detectada_em asc
                     limit 1
                   ) as fonte_id
            from public.vagas v
            join public.municipios m on m.codigo_ibge = v.municipio_id
            where v.revisao_status in ('aprovada', 'rejeitada', 'incompleta')
              and v.numero_edital is not null
              and left(coalesce(v.revisao_motivo, ''), 15) != '[filtro médico]'
            order by v.municipio_id, v.numero_edital
            """
        )
        colunas = [
            "id", "orgao", "cargo", "salario", "salario_tipo", "numero_edital", "data_publicacao",
            "inscricoes_inicio", "inscricoes_fim", "status", "resumo", "revisao_status",
            "municipio_id", "municipio_nome", "municipio_uf", "fonte_id",
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


def listar_evidencias_pdf_sem_print(conn: psycopg.Connection, *, limite: int) -> list[dict[str, Any]]:
    """Evidência tipo `pdf` sem `pagina_pdf`/`url_print_pagina` gravados —
    backlog anterior a 2026-09-09 (campo `pagina` só passou a existir no
    prompt do Gemini nessa data, ver `evidencia_imagem.py`). Ordenado pela
    vaga mais recente primeiro (`vagas.detectada_em desc`), decisão do
    usuário 2026-09-10: priorizar vaga atual sobre backlog antigo.
    Usado por `scripts/backfill_print_evidencias.py`."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select ve.id, ve.vaga_id, ve.url, v.cargo
            from public.vaga_evidencias ve
            join public.vagas v on v.id = ve.vaga_id
            where ve.tipo_documento = 'pdf'
              and ve.pagina_pdf is null
              and ve.url_print_pagina is null
              and ve.url is not null
            order by v.detectada_em desc
            limit %(limite)s
            """,
            {"limite": limite},
        )
        colunas = ["id", "vaga_id", "url", "cargo"]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def atualizar_print_evidencia(
    conn: psycopg.Connection, *, evidencia_id: str, pagina_pdf: int, url_print_pagina: str
) -> None:
    """Preenche `pagina_pdf`/`url_print_pagina` numa evidência já
    existente (backfill) — único ponto do código que faz `update` nesses
    2 campos; toda gravação nova continua passando por
    `inserir_vaga_com_evidencia`."""
    with conn.cursor() as cur:
        cur.execute(
            """
            update public.vaga_evidencias
            set pagina_pdf = %(pagina_pdf)s, url_print_pagina = %(url_print_pagina)s
            where id = %(evidencia_id)s
            """,
            {"evidencia_id": evidencia_id, "pagina_pdf": pagina_pdf, "url_print_pagina": url_print_pagina},
        )


#: Campos estruturados do edital (migrations 018/027/028 no repo
#: principal) que `scripts/auditar_completude_vagas.py` tenta preencher
#: numa vaga médica já aprovada mas incompleta — usado tanto pra achar
#: candidata (`listar_vagas_medicas_incompletas`) quanto como allowlist de
#: `atualizar_campos_vaga` (nunca aceita coluna fora desta lista, pra não
#: virar um update genérico arbitrário).
CAMPOS_COMPLETUDE = (
    "numero_vagas", "taxa_inscricao", "carga_horaria", "valor_hora",
    "data_prova", "requisitos", "banca_organizadora", "tem_prova",
    "exige_curriculo", "salario", "salario_tipo", "inscricoes_inicio", "inscricoes_fim",
)


def listar_vagas_medicas_incompletas(
    conn: psycopg.Connection, *, minimo_campos_faltando: int = 3, limite: int
) -> list[dict[str, Any]]:
    """Vaga médica já aprovada e visível ao usuário (mesma regra de
    `vagas_pagina` no repo principal: `revisao_status = 'aprovada'` +
    `categoria_saude = 'medico'`) com pelo menos `minimo_campos_faltando`
    dos campos de `CAMPOS_COMPLETUDE` nulos — candidata a reprocessamento
    por `scripts/auditar_completude_vagas.py` (objetivo do usuário,
    2026-09-12: vaga aprovada com informação incompleta obriga quem usa o
    site a abrir o edital original pra descobrir o básico). Só considera
    a PRIMEIRA evidência de cada vaga pra reler o link — mesma limitação
    documentada em `listar_vagas_revisadas_para_consistencia` (1 fonte por
    vaga na prática)."""
    campos_nulos_sql = " + ".join(f"(v.{campo} is null)::int" for campo in CAMPOS_COMPLETUDE)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            select v.id, v.cargo, v.orgao, v.numero_edital, v.municipio_id, m.nome, m.uf,
                   v.numero_vagas, v.taxa_inscricao, v.carga_horaria, v.valor_hora,
                   v.data_prova, v.requisitos, v.banca_organizadora, v.tem_prova,
                   v.exige_curriculo, v.salario, v.salario_tipo, v.inscricoes_inicio, v.inscricoes_fim,
                   ev.id as evidencia_id, ev.fonte_id, ev.url, ev.tipo_documento
            from public.vagas v
            join public.municipios m on m.codigo_ibge = v.municipio_id
            join lateral (
                select ve.id, ve.fonte_id, ve.url, ve.tipo_documento
                from public.vaga_evidencias ve
                where ve.vaga_id = v.id
                order by ve.detectada_em asc
                limit 1
            ) ev on true
            where v.revisao_status = 'aprovada'
              and v.categoria_saude = 'medico'
              and ({campos_nulos_sql}) >= %(minimo)s
            order by v.detectada_em desc
            limit %(limite)s
            """,
            {"minimo": minimo_campos_faltando, "limite": limite},
        )
        colunas = [coluna.name for coluna in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def atualizar_campos_vaga(conn: psycopg.Connection, *, vaga_id: str, campos: dict[str, Any]) -> None:
    """Preenche só os campos passados em `campos` (subconjunto de
    `CAMPOS_COMPLETUDE`) — usado por `scripts/auditar_completude_vagas.py`
    pra completar dado que faltava numa vaga já aprovada. Quem decide o
    que entra em `campos` é o chamador: só o que estava `null` e foi
    encontrado de novo — esta função nunca sobrescreve um campo que já
    tinha valor porque nunca recebe esse campo no dicionário pra começo
    de conversa. `campos` vazio é no-op (evita gerar `update` sem
    `set`)."""
    campos_invalidos = set(campos) - set(CAMPOS_COMPLETUDE)
    if campos_invalidos:
        raise ValueError(f"Campo não permitido em atualizar_campos_vaga: {sorted(campos_invalidos)}")
    if not campos:
        return
    atribuicoes = ", ".join(f"{coluna} = %({coluna})s" for coluna in campos)
    with conn.cursor() as cur:
        cur.execute(
            f"update public.vagas set {atribuicoes} where id = %(vaga_id)s",
            {**campos, "vaga_id": vaga_id},
        )


def registrar_evidencia_adicional(
    conn: psycopg.Connection,
    *,
    vaga_id: str,
    fonte_id: str,
    identificador_externo: str,
    url: str,
    tipo_documento: str,
) -> str | None:
    """Adiciona uma evidência EXTRA a uma vaga já existente — nunca troca
    nem apaga a evidência original, só soma (`vaga_evidencias.vaga_id` já
    é 1:N por design). Usado quando
    `scripts/auditar_completude_vagas.py` acha um link mais atual/correto
    pro mesmo edital via busca, porque o link original está quebrado ou
    não abre a página certa (achado que motivou esta rotina, 2026-09-12).
    `verificado_por_ia=false`: achar um link não é o mesmo que confirmar
    que o conteúdo bate — fica pendente de conferência, mesmo tratamento
    que toda evidência nova recebe no resto do pipeline. Devolve `None`
    (sem erro) se o mesmo `(fonte_id, identificador_externo)` já existir
    (dedup igual a `inserir_vaga_com_evidencia`)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.vaga_evidencias
                (vaga_id, fonte_id, identificador_externo, url, tipo_documento, verificado_por_ia)
            values (%(vaga_id)s, %(fonte_id)s, %(identificador_externo)s, %(url)s, %(tipo_documento)s, false)
            on conflict (fonte_id, identificador_externo) do nothing
            returning id
            """,
            {
                "vaga_id": vaga_id,
                "fonte_id": fonte_id,
                "identificador_externo": identificador_externo,
                "url": url,
                "tipo_documento": tipo_documento,
            },
        )
        row = cur.fetchone()
        return row[0] if row else None


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
