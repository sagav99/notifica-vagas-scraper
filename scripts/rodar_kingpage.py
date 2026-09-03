#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Kingpage: escaneia as primeiras
`kingpage.PAGINAS_POR_CATEGORIA` páginas de cada categoria relevante
(24/25 fixas + extras por município, ver `fontes/kingpage.py`) dos 3
tenants confirmados (`municipios_kingpage.csv`), acha o edital de
abertura entre os documentos de cada processo e usa Gemini pra ler o PDF
(cargo/salário não têm campo estruturado em HTML — mesmo padrão de
Actcon/JCM/FGV).

Descoberta de "o que é novo": a paginação por categoria não garante ordem
cronológica confiável entre páginas — o dedup do banco
(`identificador_externo` único por fonte+processo+cargo) evita duplicar o
que já foi processado em execuções anteriores; ver docstring de
`fontes/kingpage.py` pra decisão completa.

Uso: python scripts/rodar_kingpage.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf
from notifica_vagas_scraper.fontes import kingpage

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_TIPO = "oficial"


def coletar_itens(municipio: kingpage.MunicipioKingpage) -> list[kingpage.ItemListagem]:
    """Junta os itens das primeiras `PAGINAS_POR_CATEGORIA` páginas de
    cada categoria relevante do município, sem duplicar `processo_id`
    (pode repetir entre categorias/páginas)."""
    vistos: dict[int, kingpage.ItemListagem] = {}
    for categoria_id, categoria_slug in kingpage.categorias_do_municipio(municipio.nome):
        for pagina in range(1, kingpage.PAGINAS_POR_CATEGORIA + 1):
            url = f"{municipio.url_prefeitura.rstrip('/')}/concurso/categoria/{categoria_id}/{categoria_slug}/page/{pagina}"
            resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            if resposta.status_code != 200:
                break
            itens = kingpage.listar_processos_categoria(resposta.text, municipio.url_prefeitura)
            if not itens:
                break
            for item in itens:
                vistos.setdefault(item.processo_id, item)
    return list(vistos.values())


def processar_processo(conn, fonte_id: str, municipio: kingpage.MunicipioKingpage, item: kingpage.ItemListagem) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    documentos = kingpage.listar_documentos(resposta.text, municipio.url_prefeitura)
    edital = kingpage.escolher_edital(documentos)
    if edital is None:
        print(f"  aviso: '{item.objeto}' sem documentos listados")
        return 0

    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{item.objeto}' ({edital.url_pdf})")
        return 0

    orgao = extraido.get("orgao") or f"Prefeitura Municipal de {municipio.nome}/{municipio.uf}"
    numero_edital = extraido.get("numero_edital") or item.numero_ano
    tipo_oportunidade = extraido.get("tipo_oportunidade")

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
            identificador_externo=f"kingpage-{item.processo_id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=edital.data,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=f"{item.objeto} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=edital.url_pdf,
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
        municipios = kingpage.listar_municipios_kingpage()
        print(f"{len(municipios)} município(s) Kingpage confirmado(s).")

        total_geral = 0
        for municipio in municipios:
            print(f"Processando {municipio.nome}/{municipio.uf}...")
            fonte_id = db.upsert_fonte(
                conn,
                nome=f"Portal {municipio.nome}/{municipio.uf} (Kingpage)",
                url=municipio.url_prefeitura,
                tipo=FONTE_TIPO,
                uf=municipio.uf,
            )
            conn.commit()

            try:
                itens = coletar_itens(municipio)
            except requests.exceptions.RequestException as exc:
                print(f"  ERRO listando processos de {municipio.nome}/{municipio.uf}: {exc}")
                continue

            print(f"  {len(itens)} processo(s) encontrado(s).")
            for item in itens:
                print(f"  {item.objeto} ({item.numero_ano})")
                try:
                    with conn.transaction():
                        total_geral += processar_processo(conn, fonte_id, municipio, item)
                    conn.commit()
                except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                    print(f"    ERRO processando '{item.objeto}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_kingpage.py"):
        main()
