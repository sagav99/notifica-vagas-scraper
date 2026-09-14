#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Instituto Consulplan
(`institutoconsulplan.org.br`): descobre clientes via `sitemap.xml` (sem
lista curada de município, igual FGV/Actcon/JCM/FUNDEP — slug de cliente
é opaco, não segue regex fixo, ver `fontes/institutoconsulplan.py`), lê
o `<title>` de cada página de cliente pra confirmar órgão/cidade/UF
(dado de graça, sem geocoding) e acha o edital de abertura entre os
documentos listados. Cargo/salário só existem dentro do PDF, extração
100% via Gemini (mesmo padrão de FGV/Actcon/JCM/Kingpage — sem fonte
estruturada em HTML aqui, diferente de IBGP/Instituto Mais).

**Sem distinção entre "inscrição aberta" e "em breve" nesta fonte**
(diferente de IBGP, que tem 2 endpoints separados): o Instituto Consulplan
só tem 1 página por cliente, com o mesmo layout de tabela de documentos
independente de a inscrição já estar aberta ou não — por isso todo
cliente MG/SP com edital de abertura publicado é processado normalmente
aqui, mesmo com inscrição ainda futura (ex: Alvinópolis/MG, edital
01/2026, inscrições 16/11 a 15/12/2026 na investigação de 2026-09-13).
`status="aberta"` é só o valor inicial de gravação (mesmo de toda fonte
oficial) — a recomputação real de `aberta`/`futura`/`encerrada` a partir
de `inscricoes_inicio`/`inscricoes_fim` é feita depois pelo cron do site
principal (`app/api/cron/recalcular-status`, `lib/situacaoReal.ts`), não
aqui.

Uso: python scripts/rodar_institutoconsulplan.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, gemini_util, ibge
from notifica_vagas_scraper.fontes import institutoconsulplan

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Instituto Consulplan"
SITEMAP_URL = "https://www.institutoconsulplan.org.br/sitemap.xml"

#: portfólio confirmado na investigação é MG/SP (9 clientes mapeados),
#: mesmo padrão de filtro de FUNDEP/JCM/IBGP pra fonte multi-estado.
UFS_DO_PROJETO = ("MG", "SP")


def processar_cliente(conn, fonte_id: str, url: str) -> int:
    resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    html = resposta.text

    cliente = institutoconsulplan.identificar_cliente(html, url)
    if cliente is None:
        # página institucional ou fora do padrão de título conhecido —
        # não é falha, ver docstring de `identificar_cliente`.
        return 0
    if cliente.uf not in UFS_DO_PROJETO:
        print(f"  {cliente.slug} ({cliente.cidade}/{cliente.uf}): fora do escopo MG/SP, pulando")
        return 0

    if institutoconsulplan.pagina_sem_publicacao(html):
        print(f"  {cliente.slug} ({cliente.orgao}): ainda sem publicação, pulando")
        return 0

    documentos = institutoconsulplan.listar_documentos(html)
    if not documentos:
        print(f"  {cliente.slug} ({cliente.orgao}): sem documentos listados, pulando")
        return 0

    edital = institutoconsulplan.escolher_edital_abertura(documentos)
    if edital is None:
        print(f"  {cliente.slug} ({cliente.orgao}): nenhum documento de edital identificado, pulando")
        return 0

    codigo_ibge = db.buscar_codigo_ibge_local(conn, cliente.cidade, cliente.uf) or ibge.buscar_codigo_ibge(
        cliente.cidade, cliente.uf
    )
    if codigo_ibge is None:
        print(f"  aviso: município '{cliente.cidade}/{cliente.uf}' não encontrado no IBGE, pulando")
        return 0

    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{cliente.orgao}' ({edital.url_pdf})")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=cliente.cidade, uf=cliente.uf)
    orgao = extraido.get("orgao") or cliente.orgao
    numero_edital = extraido.get("numero_edital") or institutoconsulplan.extrair_numero_edital_do_titulo(
        edital.titulo
    )
    tipo_oportunidade = extraido.get("tipo_oportunidade")

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue

        campos_extras = gemini_util.campos_estruturados_extras(extraido, vaga)
        # banca é fixa nesta fonte — usa o valor do Gemini se vier, senão
        # a constante conhecida (nunca fica null à toa, mesmo padrão de
        # rodar_ibgp.py).
        campos_extras["banca_organizadora"] = campos_extras.get("banca_organizadora") or "Instituto Consulplan"

        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=institutoconsulplan.identificador_externo(cliente.slug, cargo),
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=edital.data,
            inscricoes_inicio=extraido.get("inscricoes_inicio"),
            inscricoes_fim=extraido.get("inscricoes_fim"),
            status="aberta",
            resumo=f"{orgao} — Edital nº {numero_edital or '?'} — {cargo}"
            + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=edital.url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
            **campos_extras,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        salario_str = f"R$ {vaga['salario']:.2f}" if vaga.get("salario") else "salário não identificado"
        print(f"    {cargo} ({salario_str}): vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(SITEMAP_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    urls_clientes = institutoconsulplan.listar_urls_clientes(resposta.text)
    print(f"{len(urls_clientes)} URL(s) candidata(s) a cliente no sitemap.xml.")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(
            conn, nome=FONTE_NOME, url="https://www.institutoconsulplan.org.br", tipo="oficial", uf="MG"
        )
        conn.commit()

        total_geral = 0
        for url in urls_clientes:
            print(f"Processando {url}...")
            try:
                with conn.transaction():
                    total_geral += processar_cliente(conn, fonte_id, url)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 cliente derrubar o lote inteiro
                print(f"  ERRO processando '{url}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_institutoconsulplan.py"):
        main()
