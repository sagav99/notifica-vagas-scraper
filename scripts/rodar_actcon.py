#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Actcon.net: percorre o módulo "Processos
Seletivos" (`/processos-seletivos`) dos municípios confirmados (ver
`fontes/actcon.listar_municipios_actcon`), filtra os processos com
situação "Em andamento", acha o edital de abertura na lista de
publicações e usa Gemini pra ler o PDF (cargo/salário/vagas não têm
campo estruturado em HTML, igual FGV).

Uso: python scripts/rodar_actcon.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf
from notifica_vagas_scraper.fontes import actcon

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_TIPO = "oficial"


def _escolher_edital(publicacoes: list[actcon.Publicacao]) -> actcon.Publicacao:
    """Entre as publicações de um processo (edital de abertura,
    retificações, resultados, convocações), a que tem mais chance de
    conter cargo/salário/vagas completos é o edital de abertura — quando
    não dá pra identificar pelo título, usa a mais antiga (normalmente a
    primeira publicada, que costuma ser o edital original)."""
    for publicacao in reversed(publicacoes):
        if "edital" in publicacao.titulo.lower():
            return publicacao
    return publicacoes[-1]


def processar_processo(
    conn, fonte_id: str, municipio: actcon.MunicipioActcon, processo: actcon.ProcessoSeletivo
) -> int:
    publicacoes = actcon.listar_publicacoes(municipio.url_prefeitura, processo.cd)
    if not publicacoes:
        print(f"  aviso: '{processo.titulo}' sem publicações listadas")
        return 0

    edital = _escolher_edital(publicacoes)
    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{processo.titulo}' ({edital.url_pdf})")
        return 0

    orgao = extraido.get("orgao") or processo.unidade or f"Prefeitura Municipal de {municipio.nome}/{municipio.uf}"
    numero_edital = extraido.get("numero_edital") or processo.titulo
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
            identificador_externo=f"actcon-{processo.cd}-{slug_cargo}",
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
            resumo=f"{processo.titulo} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
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
        municipios = actcon.listar_municipios_actcon()
        print(f"{len(municipios)} município(s) Actcon.net confirmado(s).")

        total_geral = 0
        for municipio in municipios:
            print(f"Processando {municipio.nome}/{municipio.uf}...")
            fonte_id = db.upsert_fonte(
                conn,
                nome=f"Portal {municipio.nome}/{municipio.uf} (Actcon)",
                url=municipio.url_prefeitura,
                tipo=FONTE_TIPO,
                uf=municipio.uf,
            )
            conn.commit()
            try:
                processos = actcon.listar_processos_seletivos(municipio.url_prefeitura)
            except requests.exceptions.RequestException as exc:
                print(f"  ERRO listando processos de {municipio.nome}/{municipio.uf}: {exc}")
                continue

            for processo in processos:
                print(f"  {processo.titulo} ({processo.situacao})")
                try:
                    with conn.transaction():
                        total_geral += processar_processo(conn, fonte_id, municipio, processo)
                    conn.commit()
                except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                    print(f"    ERRO processando '{processo.titulo}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_actcon.py"):
        main()
