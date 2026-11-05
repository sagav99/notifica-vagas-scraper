#!/usr/bin/env python3
"""Monitor de convocações (plano Max): lê as publicações novas de convocação/nomeação/chamada dos
diários oficiais de MG/SP/PR/SC/RS e cruza com o NOME dos assinantes (em qualquer concurso) e, se
cadastrado, com o município/concurso que acompanham. Grava avisos pendentes (`convocacao_avisos`);
o cron do site (`/api/cron/avisos-convocacao`) envia o e-mail e a cópia para o WhatsApp manual.

Custo: 0 Serper, 0 Gemini. ~1 requisição por publicação nova (cache em `convocacao_vistos`), independente
do número de assinantes. Detalhe: docs/convocacoes_monitoramento.md (repo principal).

Uso: python scripts/monitorar_convocacoes.py [--dias 3] [--uf MG ...] [--dry-run] [--exemplo-json arquivo]
Requer DATABASE_URL (exceto com --exemplo-json, que usa acompanhamentos de arquivo e não toca o banco).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db
from notifica_vagas_scraper.fontes import convocacoes as conv
from notifica_vagas_scraper.fontes import convocados_publicos as pub
from notifica_vagas_scraper.fontes import diarios_sul

ORCAMENTO_S = 60 * 55  # leitura do SIGPub para antes do timeout do workflow (90 min); QD, SC e gravação ainda cabem
PAUSA_S = 0.5
#: Teto de consultas Serper por execução (só município cadastrado sem diário agregador, em dia par).
LIMITE_SERPER_POR_EXECUCAO = 6
LIMITE_SERPER_NOME_POR_EXECUCAO = 10  # nomes de assinantes Max; cada nome só 1x a cada 7 dias


def pausa() -> None:
    time.sleep(PAUSA_S)


def carregar_acompanhamentos(conn) -> list[conv.Acompanhamento]:
    with conn.cursor() as cur:
        cur.execute(
            "select a.id::text, coalesce(a.usuario_id::text, a.assinante_bot_id::text), a.nome_edital, a.uf, a.municipio, a.concurso, a.cargo, a.classificacao, a.url_convocacoes "
            "from public.convocacao_acompanhamentos a "
            "left join public.bot_assinantes b on b.id = a.assinante_bot_id "
            # Assinante do bot (Max por código) só entra com o Max vigente.
            "where a.ativo and (a.assinante_bot_id is null or (b.plano like 'max\\_%%' and b.status in ('pago', 'no_grupo') and b.valido_ate > now())) "
            "order by a.criado_em"
        )
        return [conv.Acompanhamento(*linha) for linha in cur.fetchall()]


def carregar_paginas(conn, urls: list[str]) -> dict[str, set[str]]:
    if not urls:
        return {}
    with conn.cursor() as cur:
        cur.execute("select url, sentencas from public.convocacao_paginas where url = any(%s)", (urls,))
        return {url: set(sent) for url, sent in cur.fetchall()}


def gravar_paginas(conn, memoria: dict[str, set[str]]) -> None:
    with conn.cursor() as cur:
        for url, sentencas in memoria.items():
            cur.execute(
                "insert into public.convocacao_paginas (url, sentencas) values (%s, %s::jsonb) "
                "on conflict (url) do update set sentencas = excluded.sentencas, visto_em = now()",
                (url, json.dumps(sorted(sentencas))),
            )
    conn.commit()


def gravar_cobertura(conn, cobertura: dict[str, dict[str, str]]) -> None:
    """`cobertura[uf][municipio_norm] = nome do município` (fonte = agregador do estado)."""
    fontes = {"MG": "amm-mg", "SP": "apm+dosp+querido-diario", "PR": "amp", "RS": "famurs", "SC": "ciga-sc"}
    with conn.cursor() as cur:
        for uf, municipios in cobertura.items():
            for norm, nome in municipios.items():
                cur.execute(
                    "insert into public.convocacao_cobertura (uf, municipio_norm, municipio, fonte) values (%s, %s, %s, %s) "
                    "on conflict (uf, municipio_norm) do update set municipio = excluded.municipio, atualizado_em = now()",
                    (uf, norm, nome, fontes[uf]),
                )
    conn.commit()


def carregar_vagas_alvo(conn) -> list[pub.VagaAlvo]:
    """Vagas médicas aprovadas (qualquer status: concurso encerrado continua convocando). Uma por concurso+município."""
    with conn.cursor() as cur:
        cur.execute(
            "select v.id::text, m.uf, m.nome, v.cargo, v.numero_edital, v.banca_organizadora, m.codigo_ibge from public.vagas v "
            "join public.municipios m on m.codigo_ibge = v.municipio_id "
            "where v.revisao_status = 'aprovada' and v.categoria_saude = 'medico' and m.uf in ('MG','SP','PR','SC','RS')"
        )
        linhas = cur.fetchall()
    unicas: dict[tuple, pub.VagaAlvo] = {}
    for id_, uf, municipio, cargo, edital, banca, ibge in linhas:
        unicas.setdefault((uf, municipio, edital or cargo), pub.VagaAlvo(id_, uf, municipio, cargo, edital, banca, ibge))
    return list(unicas.values())


def gravar_convocados(conn, resultados: list[tuple[dict, pub.VagaAlvo, str | None, list[pub.Convocado]]]) -> int:
    novos = 0
    with conn.cursor() as cur:
        for p, vaga, concurso, convocados in resultados:
            cur.execute(
                "insert into public.convocacao_publicacoes (chave, fonte, uf, municipio, titulo, data_publicacao, url) "
                "values (%s, %s, %s, %s, %s, %s, %s) on conflict (chave) do nothing",
                (p["chave"], p["fonte"], p["uf"], vaga.municipio, p["titulo"][:300], p.get("data_publicacao"), p.get("url")),
            )
            for c in convocados:
                cur.execute(
                    "insert into public.convocacao_convocados (publicacao_chave, vaga_id, uf, municipio, concurso, cargo, nome, nome_norm, cpf_publicado, classificacao, data_publicacao, url) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (publicacao_chave, nome_norm) do nothing",
                    (p["chave"], vaga.id, p["uf"], vaga.municipio, concurso, c.cargo or vaga.cargo, c.nome, c.nome_norm, c.cpf_publicado, c.classificacao, p.get("data_publicacao"), p.get("url")),
                )
                novos += cur.rowcount
    conn.commit()
    return novos


def carregar_vistos(conn, dias: int = 30) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("select chave from public.convocacao_vistos where visto_em > now() - make_interval(days => %s)", (dias,))
        return {linha[0] for linha in cur.fetchall()}


def gravar_vistos(conn, chaves: list[str]) -> None:
    with conn.cursor() as cur:
        for chave in chaves:
            cur.execute("insert into public.convocacao_vistos (chave) values (%s) on conflict do nothing", (chave,))
    conn.commit()


def gravar(conn, achados: list[conv.Achado]) -> int:
    novos = 0
    with conn.cursor() as cur:
        for a in sorted(achados, key=lambda x: 0 if x.tipo == "nome" else 1):
            cur.execute(
                "insert into public.convocacao_publicacoes (chave, fonte, uf, municipio, titulo, data_publicacao, url) "
                "values (%s, %s, %s, %s, %s, %s, %s) on conflict (chave) do nothing",
                (a.chave, a.fonte, a.uf, a.municipio, a.titulo[:300], a.data_publicacao, a.url),
            )
            cur.execute(
                "insert into public.convocacao_avisos (acompanhamento_id, publicacao_chave, tipo, trecho, concurso_detectado, cargo_detectado, prazo_detectado, confianca) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (acompanhamento_id, publicacao_chave) do nothing",
                (a.acompanhamento_id, a.chave, a.tipo, a.trecho[:900], a.concurso, a.cargo, a.prazo, a.confianca),
            )
            novos += cur.rowcount
    conn.commit()
    return novos


def coletar(acompanhamentos, dias: int, ufs: set[str], vistos: set[str], ao_ler=lambda chave: None, conn=None, estado: dict | None = None, esgotado=lambda: False, ao_texto=None, ao_ponto=lambda achados: None, vagas_alvo=()) -> list[conv.Achado]:
    estado = estado if estado is not None else {}
    estado.setdefault("cobertura", {})
    hoje = diarios_sul.datetime_hoje_brasilia().date()
    lista_dias = [hoje - timedelta(days=n) for n in range(dias, -1, -1)]
    sessao = requests.Session()
    achados: list[conv.Achado] = []

    for uf in ("MG", "SP", "PR", "RS"):
        if uf not in ufs:
            continue
        try:
            estado["cobertura"][uf] = {diarios_sul.normalizar(n): n for n in conv.municipios_cobertos_sigpub(sessao, uf)}
        except requests.RequestException as erro:
            print(f"  cobertura {uf}: {erro}")
        novos, lidas, cartoes = conv.processar_sigpub(sessao, uf, lista_dias, acompanhamentos, vistos, pausa=pausa, ao_ler=ao_ler, esgotado=esgotado, ao_texto=ao_texto)
        print(f"{uf}: {cartoes} publicação(ões) na varredura, {lidas} nova(s) lida(s), {len(novos)} achado(s).")
        # Dia útil sem NENHUMA publicação de convocação num diário desse porte = diário quebrou/mudou: falhar alto.
        if cartoes == 0 and any(d.weekday() < 5 for d in lista_dias[-2:]) and uf in ("MG", "PR", "RS"):
            raise RuntimeError(f"{uf}: varredura do diário devolveu 0 publicações em dia útil (HTML do SIGPub mudou ou diário fora do ar?)")
        achados += novos
        ao_ponto(achados)  # grava o que já foi lido: um cancelamento do workflow não perde a rodada
        if esgotado():
            print(f"  AVISO: orçamento de tempo esgotado em {uf}; o restante será lido na próxima rodada.")

    if "SC" in ufs:  # sempre: a cobertura de SC vem dos ZIPs do CIGA
        zips = diarios_sul.listar_zips_sc(dias, hoje=hoje, sessao=sessao)
        achados_sc = 0
        for z in zips:
            try:
                resposta = sessao.get(z["url"], headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=180)
                resposta.raise_for_status()
                pubs_sc = diarios_sul.ler_zip_sc(resposta.content)
            except (requests.RequestException, OSError, ValueError) as erro:  # um ZIP lento do CIGA não derruba a rodada: tenta o próximo
                print(f"  ciga-sc {z.get('url', '')[-40:]}: {erro.__class__.__name__}")
                continue
            for pub in pubs_sc:
                if pub.municipio:
                    estado["cobertura"].setdefault("SC", {})[diarios_sul.normalizar(pub.municipio)] = pub.municipio
            novos = conv.achados_sc(pubs_sc, acompanhamentos, ao_texto=ao_texto)
            achados_sc += len(novos)
            achados += novos
        print(f"SC: {len(zips)} ZIP(s) do CIGA, {achados_sc} achado(s).")
        ao_ponto(achados)
        if not zips and any(d.weekday() < 5 for d in lista_dias[-2:]):
            raise RuntimeError("SC: nenhum ZIP do CIGA na janela em dia útil (CKAN mudou ou fora do ar?)")

    # DOSP/DiOE (~187 municípios de SP + alguns de MG/PR/SC/RS): varredura por ID, texto lido localmente.
    if ufs & {"SP", "MG", "PR", "SC", "RS"}:
        try:
            novos, lidos, publicados = conv.varrer_dosp(sessao, acompanhamentos, vistos, pausa=pausa, ao_ler=ao_ler, esgotado=esgotado, ao_texto=ao_texto)
        except (requests.RequestException, ValueError) as erro:
            print(f"  dosp: {erro}")
        else:
            ja = {(a.acompanhamento_id, diarios_sul.normalizar(a.municipio or ""), a.data_publicacao) for a in achados}
            novos = [a for a in novos if (a.acompanhamento_id, diarios_sul.normalizar(a.municipio or ""), a.data_publicacao) not in ja]
            achados += novos
            ao_ponto(achados)
            print(f"DOSP: {publicados} edição(ões) na varredura de IDs, {lidos} nova(s) lida(s), {len(novos)} achado(s).")
            if publicados == 0 and lista_dias[-1].weekday() < 5:
                raise RuntimeError("DOSP: nenhuma edição nos IDs varridos em dia útil (exibe_do.php mudou, ID inicial velho demais ou fora do ar?)")
            if "SP" in ufs:
                estado["cobertura"].setdefault("SP", {}).update({diarios_sul.normalizar(n): n for n in conv.municipios_dosp_sp()})

    # Querido Diário: busca o NOME (frase exata) em milhares de municípios, inclusive os que os agregadores acima não cobrem.
    achados_qd = 0
    for ac in acompanhamentos:
        try:
            novos = conv.achados_querido_diario(sessao, ac, hoje - timedelta(days=dias))
        except (requests.RequestException, ValueError) as erro:
            print(f"  querido-diario: {erro}")
            continue
        # mesma pessoa, mesmo município e dia já achados por SIGPub/CIGA: não duplica o aviso
        ja = {(a.acompanhamento_id, diarios_sul.normalizar(a.municipio or ""), a.data_publicacao) for a in achados}
        novos = [a for a in novos if (a.acompanhamento_id, diarios_sul.normalizar(a.municipio or ""), a.data_publicacao) not in ja]
        achados_qd += len(novos)
        achados += novos
        pausa()
    print(f"Querido Diário: {achados_qd} achado(s) para {len(acompanhamentos)} acompanhamento(s).")

    # Convocados públicos: Querido Diário filtrado pelos municípios dos concursos médicos que temos (fora dos agregadores).
    if ao_texto is not None and vagas_alvo:
        cons, lidos_qd, erros_qd = pub.varrer_querido_diario(
            sessao, list(vagas_alvo), hoje - timedelta(days=dias), vistos, ao_texto=ao_texto, ao_ler=ao_ler, pausa=pausa, esgotado=esgotado, user_agent=diarios_sul.USER_AGENT,
        )
        print(f"Querido Diário (concursos médicos): {cons} município(s) consultado(s), {lidos_qd} diário(s) lido(s), {erros_qd} erro(s).")
        ao_ponto(achados)

    # Diários oficiais ESTADUAIS (SES, hospitais e universidades estaduais, polícias): busca por nome, 1-2 chamadas por nome e UF.
    achados_doe = 0
    token_mg = None
    for ac in acompanhamentos:
        fim = hoje
        ini = hoje - timedelta(days=dias)
        tarefas = []
        if "SP" in ufs:
            tarefas.append(("doe-sp", lambda ac=ac: conv.achados_doe_sp(sessao, ac, ini, fim)))
        if "PR" in ufs:
            tarefas.append(("doe-pr", lambda ac=ac: conv.achados_doe_pr(sessao, ac, ini, fim)))
        if "MG" in ufs and ac.uf in (None, "MG"):
            try:
                token_mg = token_mg or conv.token_doe_mg(sessao)
                tarefas.append(("doe-mg", lambda ac=ac: conv.achados_doe_mg(sessao, ac, ini, fim, token=token_mg)))
            except (requests.RequestException, ValueError) as erro:
                print(f"  doe-mg: {erro}")
        for nome_fonte, fn in tarefas:
            try:
                novos = fn()
            except (requests.RequestException, ValueError, KeyError) as erro:
                print(f"  {nome_fonte}: {erro}")
                continue
            achados_doe += len(novos)
            achados += novos
            pausa()
    if "RS" in ufs:
        try:
            novos, lidas = conv.achados_doe_rs(sessao, acompanhamentos, lista_dias, vistos, ao_ler=ao_ler, esgotado=esgotado)
        except (requests.RequestException, ValueError, KeyError) as erro:
            print(f"  doe-rs: {erro}")
        else:
            achados_doe += len(novos)
            achados += novos
            print(f"DOE-RS: {lidas} edição(ões) nova(s) lida(s), {len(novos)} achado(s).")
    print(f"DOE estaduais (SP/MG/PR): {achados_doe} achado(s) para {len(acompanhamentos)} acompanhamento(s).")

    # Diário Oficial da União: concursos federais (EBSERH, universidades, ministérios). Mesma busca por nome.
    achados_dou = 0
    for ac in acompanhamentos:
        try:
            novos = conv.achados_dou(sessao, ac, hoje - timedelta(days=dias), hoje)
        except (requests.RequestException, ValueError) as erro:
            print(f"  dou: {erro}")
            continue
        achados_dou += len(novos)
        achados += novos
        pausa()
    print(f"DOU: {achados_dou} achado(s) para {len(acompanhamentos)} acompanhamento(s).")

    # Município cadastrado que o agregador do estado NÃO cobre: notícias (grátis) todo dia; Google (Serper) só em dia par, com teto.
    descobertos = [
        a for a in acompanhamentos
        if a.uf and a.municipio and a.uf in estado["cobertura"] and diarios_sul.normalizar(a.municipio) not in estado["cobertura"][a.uf]
    ]
    estado["descobertos"] = len(descobertos)
    chave_serper = os.environ.get("SERPER_API_KEY")
    gastos = 0
    for ac in descobertos:
        try:
            achados += conv.noticias_convocacao(sessao, ac, hoje - timedelta(days=dias))
        except requests.RequestException as erro:
            print(f"  notícias {ac.municipio}: {erro}")
        pausa()
        if chave_serper and conn is not None and hoje.toordinal() % 2 == 0 and gastos < LIMITE_SERPER_POR_EXECUCAO:
            consulta = conv.consulta_google(ac)
            resposta = sessao.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": chave_serper, "Content-Type": "application/json"},
                json={"q": consulta, "num": 10, "hl": "pt", "gl": "br", "tbs": "qdr:d3"},
                timeout=30,
            )
            gastos += 1
            if resposta.ok:
                novos = conv.achados_google(ac, resposta.json())
                achados += novos
                db.registrar_consulta_serper(conn, origem="convocacao", consulta=consulta, resultados=len(novos))
    estado["serper"] = gastos

    # Google pelo NOME do assinante: 1 consulta por nome a cada 7 dias (teto por execução; hash no log, nunca o nome).
    if chave_serper and conn is not None:
        gastos_nome = 0
        for ac in acompanhamentos:
            if gastos_nome >= LIMITE_SERPER_NOME_POR_EXECUCAO or esgotado():
                break
            rotulo = "nome:" + hashlib.sha1(diarios_sul.normalizar(ac.nome_edital).encode()).hexdigest()[:10]
            with conn.cursor() as cur:
                cur.execute("select 1 from public.serper_consultas_log where origem = 'convocacao' and consulta = %s and executado_em > now() - interval '7 days' limit 1", (rotulo,))
                if cur.fetchone():
                    continue
            resposta = sessao.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": chave_serper, "Content-Type": "application/json"},
                json={"q": conv.consulta_google_nome(ac), "num": 10, "hl": "pt", "gl": "br", "tbs": "qdr:w"},
                timeout=30,
            )
            gastos_nome += 1
            novos = conv.achados_google_nome(ac, resposta.json()) if resposta.ok else []
            achados += novos
            db.registrar_consulta_serper(conn, origem="convocacao", consulta=rotulo, resultados=len(novos), erro=None if resposta.ok else f"http {resposta.status_code}")
            pausa()
        estado["serper"] = estado.get("serper", 0) + gastos_nome
        print(f"Google por nome: {gastos_nome} consulta(s).")

    # Página de convocações informada pelo assinante (qualquer município, inclusive os não cobertos).
    com_pagina = [a for a in acompanhamentos if a.url_convocacoes and conv.url_de_pagina_valida(a.url_convocacoes)]
    memoria = carregar_paginas(conn, sorted({a.url_convocacoes for a in com_pagina})) if conn is not None else {}
    novas_memorias: dict[str, set[str]] = {}
    for url in sorted({a.url_convocacoes for a in com_pagina}):
        try:
            resposta = sessao.get(url, headers={"User-Agent": diarios_sul.USER_AGENT}, timeout=30)
            resposta.raise_for_status()
            texto = diarios_sul.texto_limpo(resposta.text[:2_000_000])
        except requests.RequestException as erro:
            print(f"  página {url[:60]}: {erro}")
            continue
        for ac in [a for a in com_pagina if a.url_convocacoes == url]:
            achados += conv.achados_da_pagina(ac, url, texto, memoria.get(url))
        novas_memorias[url] = memoria.get(url, set()) | conv.hashes_das_sentencas(texto)
        pausa()
    estado["paginas"] = novas_memorias
    return achados


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=2)
    ap.add_argument("--uf", nargs="*", default=["MG", "SP", "PR", "SC", "RS"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--exemplo-json", help="acompanhamentos de teste (lista de objetos) em vez do banco")
    args = ap.parse_args()
    ufs = set(args.uf)

    if args.exemplo_json:
        acompanhamentos = [conv.Acompanhamento(**item) for item in json.loads(Path(args.exemplo_json).read_text())]
        achados = coletar(acompanhamentos, args.dias, ufs, set())
        for a in achados:
            print(f"[{a.tipo}] {a.uf} {a.municipio} | {a.titulo[:80]} | {a.concurso} | {a.cargo} | {a.data_publicacao} | {a.url}")
        print(f"{len(achados)} achado(s).")
        return

    with db.rastrear_execucao("monitorar_convocacoes.py") as execucao:
        conn = db.conectar()
        try:
            acompanhamentos = carregar_acompanhamentos(conn)
            print(f"{len(acompanhamentos)} acompanhamento(s) ativo(s).")
            vistos = carregar_vistos(conn)
            lidos: list[str] = []
            estado: dict = {}
            vagas_alvo = carregar_vagas_alvo(conn)
            print(f"{len(vagas_alvo)} concurso(s) médico(s) cadastrado(s) para o PDF de convocados.")
            publicos: list = []

            def ao_texto(p: dict) -> None:
                r = pub.processar_publicacao(vagas_alvo, p)
                if r:
                    publicos.append((p, *r))

            inicio = time.monotonic()
            gravados = {"convocados": 0, "avisos": 0}

            def ponto(achados_parciais: list) -> None:
                """Grava (idempotente) o que já foi lido; só depois marca os vistos. Em dry-run não grava."""
                if args.dry_run:
                    return
                gravados["convocados"] += gravar_convocados(conn, publicos)
                publicos.clear()
                gravados["avisos"] += gravar(conn, achados_parciais)
                gravar_vistos(conn, lidos)
                lidos.clear()

            achados = coletar(acompanhamentos, args.dias, ufs, vistos, ao_ler=lidos.append, conn=conn, estado=estado, esgotado=lambda: time.monotonic() - inicio > ORCAMENTO_S, ao_texto=ao_texto, ao_ponto=ponto, vagas_alvo=vagas_alvo)
            if args.dry_run:
                for p, vaga, concurso, cs in publicos:
                    print(f"[convocados] {p['uf']} {vaga.municipio} | {concurso} | {p['titulo'][:60]} | {len(cs)} nome(s): {', '.join(c.nome for c in cs[:4])} | {p['url']}")
            ponto(achados)
            novos = gravados["avisos"]
            convocados_novos = gravados["convocados"]
            if not args.dry_run:
                gravar_paginas(conn, estado.get("paginas", {}))
                gravar_cobertura(conn, estado.get("cobertura", {}))
            execucao.detalhe = (
                f"acompanhamentos={len(acompanhamentos)} achados={len(achados)} avisos_novos={novos} "
                f"descobertos={estado.get('descobertos', 0)} serper={estado.get('serper', 0)} convocados_publicos={convocados_novos}"
            )
            print(execucao.detalhe)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
