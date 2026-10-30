#!/usr/bin/env python3
"""Entrypoint do cron pra Processos Seletivos da SES-SC (`fontes/ses_sc.py`).

Fluxo: listagem -> só PSS com cargo médico, inscrição ainda aberta e número
nunca visto -> artigo -> PDF do edital -> Gemini (1 chamada por PSS, ~1 a cada
2 semanas) -> 1 vaga por cargo. Município = cidade única de `Local`, senão
Florianópolis (sede da SES). Datas de inscrição vêm da listagem (mais
confiáveis que o PDF).

Uso: python scripts/rodar_ses_sc.py [--dry-run]
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper.fontes.diarios_sul import datetime_hoje_brasilia
from notifica_vagas_scraper import db, gemini_pdf, gemini_util
from notifica_vagas_scraper.fontes import ses_sc

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "SES-SC (Processos Seletivos)"
DRY_RUN = "--dry-run" in sys.argv


def processar_pss(conn, fonte_id: str, item: ses_sc.ItemPss, municipios_sc: dict[str, int]) -> int:
    artigo = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=30)
    artigo.raise_for_status()
    url_pdf = ses_sc.url_edital(artigo.text)
    if url_pdf is None:
        url_pagina = ses_sc.url_pagina_processo(artigo.text, item.numero)
        if url_pagina:
            pagina = requests.get(url_pagina, headers={"User-Agent": USER_AGENT}, timeout=30)
            pagina.raise_for_status()
            url_pdf = ses_sc.url_edital(pagina.text)
    if url_pdf is None:
        print(f"  aviso: sem link de edital em {item.url}")
        return 0
    pdf = requests.get(url_pdf, headers={"User-Agent": USER_AGENT}, timeout=90)
    pdf.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra {url_pdf}")
        return 0

    municipio = ses_sc.escolher_municipio(item.local, municipios_sc)
    codigo_ibge = municipios_sc[municipio]
    orgao = extraido.get("orgao") or "Secretaria de Estado da Saúde de Santa Catarina"
    numero_edital = extraido.get("numero_edital") or f"PSS {item.numero}/SES"

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=f"{ses_sc.ID_PREFIX}-{item.numero.replace('/', '-')}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=extraido.get("tipo_oportunidade"),
            numero_edital=numero_edital,
            data_publicacao=None,
            inscricoes_inicio=item.inscricoes_inicio,
            inscricoes_fim=item.inscricoes_fim,
            status="aberta",
            resumo=f"{item.titulo} — {cargo} ({item.local})" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
            **gemini_util.campos_estruturados_extras(extraido, vaga),
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def main() -> None:
    resposta = requests.get(ses_sc.PSS_LISTAGEM_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()
    itens = ses_sc.listar_pss(resposta.text)
    if not itens:
        raise RuntimeError("listagem da SES-SC sem nenhum PSS: layout mudou?")
    hoje = datetime_hoje_brasilia().date()
    abertos = [i for i in itens if i.tem_medico and i.inscricoes_fim and i.inscricoes_fim >= hoje]
    print(f"{len(itens)} PSS listados, {len(abertos)} com médico e inscrição aberta.")
    if DRY_RUN:
        for i in abertos:
            print(f"  {i.titulo} | {i.cargos} | {i.local} | {i.inscricoes_inicio}..{i.inscricoes_fim} | {i.url}")
        return

    conn = db.conectar()
    try:
        municipios_sc = {nome: codigo for codigo, nome, _uf in db.listar_municipios_com_codigo(conn, ufs=[ses_sc.UF])}
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=ses_sc.PSS_LISTAGEM_URL, tipo="oficial", uf=ses_sc.UF)
        conn.commit()
        ja = db.listar_identificadores_processados(conn, fonte_id)
        total = 0
        for item in abertos:
            if db.item_ja_processado(f"{ses_sc.ID_PREFIX}-{item.numero.replace('/', '-')}-", ja):
                continue
            print(f"Processando {item.titulo} ({item.url})...")
            try:
                with conn.transaction():
                    total += processar_pss(conn, fonte_id, item, municipios_sc)
                conn.commit()
            except Exception as exc:  # 1 PSS ruim não derruba o lote
                print(f"  ERRO processando '{item.titulo}': {exc}")
        print(f"\nOk. {total} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if DRY_RUN:
        main()
    else:
        with db.rastrear_execucao("rodar_ses_sc.py"):
            main()
