#!/usr/bin/env python3
"""Entrypoint do cron: busca as matérias conhecidas do DOM/AMM-MG, extrai
vagas e grava no Supabase (município, fonte, vaga, evidência).

Uso: python scripts/rodar.py
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import dom_amm_mg
from notifica_vagas_scraper.fontes_conhecidas import MATERIAS_DOM_AMM_MG

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"


def processar_materia(conn, materia) -> int:
    resposta = requests.get(materia.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    vagas_extraidas = dom_amm_mg.parsear_materia(resposta.text, url=materia.url)
    if not vagas_extraidas:
        print(f"  aviso: nenhuma vaga extraída de {materia.url}")
        return 0

    codigo_ibge = ibge.buscar_codigo_ibge(materia.municipio, materia.uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{materia.municipio}/{materia.uf}' não encontrado no IBGE, pulando")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=materia.municipio, uf=materia.uf)
    fonte_id = db.upsert_fonte(
        conn, nome=materia.fonte_nome, url=materia.fonte_url, tipo="oficial", uf=materia.uf
    )

    total = 0
    for vaga in vagas_extraidas:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=dom_amm_mg.identificador_externo(vaga),
            orgao=vaga.orgao,
            cargo=vaga.cargo,
            salario=vaga.salario,
            salario_tipo=None,
            tipo_oportunidade=None,
            numero_edital=vaga.numero_edital,
            data_publicacao=vaga.data_publicacao,
            inscricoes_inicio=vaga.inscricoes_inicio,
            inscricoes_fim=vaga.inscricoes_fim,
            status="aberta",
            resumo=f"{vaga.tipo_processo} nº {vaga.numero_edital} — {vaga.cargo} ({vaga.vagas_qtd or '?'} vaga(s), {vaga.carga_horaria or 'carga horária não informada'}).",
            url_evidencia=vaga.url,
            tipo_documento="pagina_html",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"  {vaga.cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    conn = db.conectar()
    total_geral = 0
    try:
        for materia in MATERIAS_DOM_AMM_MG:
            print(f"Processando {materia.municipio}/{materia.uf}: {materia.url}")
            total_geral += processar_materia(conn, materia)
        conn.commit()
        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
