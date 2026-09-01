#!/usr/bin/env python3
"""Entrypoint do cron pra fonte WordPress: para cada município confirmado
(ver `fontes/wordpress_editais.listar_municipios_wordpress`), busca posts
recentes via API REST do WordPress (`wp-json/wp/v2/posts?search=...`),
acha o PDF do edital anexado e usa Gemini pra ler cargo/salário/vagas —
mesmo padrão da FGV/Actcon (sem campo estruturado em HTML).

Uso: python scripts/rodar_wordpress.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf
from notifica_vagas_scraper.fontes import wordpress_editais as wp

USER_AGENT = wp.USER_AGENT
FONTE_TIPO = "oficial"
JANELA_DIAS = 90


def processar_post(conn, fonte_id: str, municipio: wp.MunicipioWordpress, post: wp.PostEdital) -> int:
    pdfs = wp.extrair_pdfs(post.conteudo_html)
    url_pdf = wp.escolher_pdf_edital(pdfs)
    if not url_pdf:
        print(f"    aviso: '{post.titulo}' sem PDF anexado")
        return 0

    pdf_resposta = requests.get(url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"    aviso: Gemini não retornou vagas pra '{post.titulo}' ({url_pdf})")
        return 0

    orgao = extraido.get("orgao") or f"Prefeitura Municipal de {municipio.nome}/{municipio.uf}"
    numero_edital = extraido.get("numero_edital")

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=municipio.codigo_ibge,
            identificador_externo=f"wp-{post.id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            numero_edital=numero_edital,
            data_publicacao=post.data,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=f"{post.titulo} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    conn = db.conectar()
    try:
        municipios = wp.listar_municipios_wordpress()
        print(f"{len(municipios)} município(s) WordPress confirmado(s).")

        data_limite = date.today() - timedelta(days=JANELA_DIAS)
        total_geral = 0
        for municipio in municipios:
            print(f"Processando {municipio.nome}/{municipio.uf}...")
            fonte_id = db.upsert_fonte(
                conn,
                nome=f"Portal {municipio.nome}/{municipio.uf} (WordPress)",
                url=municipio.url_prefeitura,
                tipo=FONTE_TIPO,
                uf=municipio.uf,
            )
            conn.commit()

            posts_vistos: set[int] = set()
            for termo in wp.TERMOS_BUSCA:
                for post in wp.buscar_posts(municipio.url_prefeitura, termo):
                    if post.id in posts_vistos:
                        continue
                    posts_vistos.add(post.id)
                    if post.data is not None and post.data < data_limite:
                        continue
                    print(f"  {post.titulo}")
                    try:
                        with conn.transaction():
                            total_geral += processar_post(conn, fonte_id, municipio, post)
                        conn.commit()
                    except Exception as exc:  # nunca deixar 1 post derrubar o lote inteiro
                        print(f"    ERRO processando '{post.titulo}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_wordpress.py"):
        main()
