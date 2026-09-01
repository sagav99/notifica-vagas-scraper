#!/usr/bin/env python3
"""Entrypoint do cron pra fonte IMESO: descobre editais com inscrição
aberta (`/edital`, sem precisar de lista curada — diferente do DOM/AMM-MG,
aqui a busca por TODOS os editais é automática desde o início), extrai
vagas de cada um e grava no Supabase.

Só processa a aba "abertos" nesta primeira versão — "futuros" (inscrição
ainda não começou) fica de fora até decidir como mapear pro
`vagas.status` (aberta/encerrada/desconhecido não tem opção "ainda não
abriu"; ver TAREFAS.md).

Uso: python scripts/rodar_imeso.py
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import imeso

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "IMESO (Instituto Mineiro Educar & Sorrir)"


def processar_edital(conn, item: imeso.ItemListagem, fonte_id: str) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    vagas_extraidas = imeso.parsear_edital(resposta.text, url=item.url)
    if not vagas_extraidas:
        print(f"  aviso: nenhuma vaga extraída de {item.url}")
        return 0

    total = 0
    for vaga in vagas_extraidas:
        if not vaga.municipio or not vaga.uf:
            print(f"  aviso: sem município/UF identificado em {item.url}, pulando cargo '{vaga.cargo}'")
            continue

        codigo_ibge = ibge.buscar_codigo_ibge(vaga.municipio, vaga.uf)
        if codigo_ibge is None:
            print(f"  aviso: município '{vaga.municipio}/{vaga.uf}' não encontrado no IBGE, pulando")
            continue

        db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=vaga.municipio, uf=vaga.uf)
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=imeso.identificador_externo(vaga, edital_id=item.edital_id),
            orgao=vaga.orgao,
            cargo=vaga.cargo,
            salario=vaga.salario,
            numero_edital=vaga.numero_edital,
            data_publicacao=None,
            inscricoes_inicio=vaga.inscricoes_inicio,
            inscricoes_fim=vaga.inscricoes_fim,
            status="aberta",
            resumo=f"{vaga.tipo_processo} nº {vaga.numero_edital} — {vaga.cargo}"
            + (f" ({vaga.requisitos})" if vaga.requisitos else "."),
            url_evidencia=vaga.url,
            tipo_documento="pagina_html",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"  {vaga.cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(f"{imeso.BASE_URL}/edital", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    itens = imeso.listar_editais(resposta.text)
    abertos = [i for i in itens if i.status == "Inscrições Abertas"]

    print(f"{len(abertos)} edital(is) com inscrição aberta de {len(itens)} total.")

    conn = db.conectar()
    total_geral = 0
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=imeso.BASE_URL, tipo="oficial", uf="MG")
        for item in abertos:
            print(f"Processando edital {item.edital_id} ({item.entidade}): {item.url}")
            total_geral += processar_edital(conn, item, fonte_id)
        conn.commit()
        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_imeso.py"):
        main()
