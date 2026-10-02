#!/usr/bin/env python3
"""Preenche `data_pagamento_taxa` e `data_resultado` das vagas médicas ABERTAS
que já estavam no banco antes dessas colunas existirem (migration 094 do repo
principal). Lote pequeno por execução, só vagas com evidência em PDF (leitura
do documento via `url_context`, sem `google_search`), cada vaga conferida no
máximo 1x (`vagas_conferencias.conferido_por = 'datas_extras_gemini'`, mesmo
quando nada é encontrado). Para na hora se a cota do Gemini esgotar.

Uso (raiz do repo scraper, venv ativada): python scripts/completude_datas_extras.py
Requer DATABASE_URL e GEMINI_API_KEY. Compartilha a cota/trava de ritmo do
Gemini (`quota_gemini`); roda no grupo de concorrência `gemini-pesado`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import completude_gemini, db
from notifica_vagas_scraper.revisao_ia import CotaGeminiEsgotadaError

MAX_CHAMADAS = 20  # chamadas ao Gemini por execução (1 por edital/PDF distinto)
LINHAS_BUSCADAS = 150
CONFERIDO_POR = "datas_extras_gemini"


def consultar_edital(vaga: dict) -> tuple[str, dict]:
    """1 chamada ao Gemini para o PDF desta vaga. Devolve (resultado, datas aceitas)."""
    campos = list(db.CAMPOS_DATAS_EXTRAS)
    try:
        bruto = completude_gemini.consultar(vaga, campos, api_key=None, modelo=None)
    except completude_gemini.ErroCompletudeGemini as exc:
        return f"erro_gemini: {str(exc)[:900]}", {}
    aceitos = completude_gemini._converter_tipos(completude_gemini.filtrar_campos_aceitos(bruto, campos))
    detalhe = f"Fonte: {bruto.get('fonte_usada', '')}"
    return ("campo_preenchido" if aceitos else "sem_alteracao") + f"|{detalhe[:900]}", aceitos


def registrar(conn, vaga: dict, resultado: str, aceitos: dict) -> str:
    """Grava as datas (se houver) e a conferência desta vaga. Devolve o resultado curto."""
    curto, _, detalhe = resultado.partition("|")
    curto = "erro_gemini" if curto.startswith("erro_gemini") else curto
    if aceitos:
        db.atualizar_datas_extras(conn, vaga_id=vaga["id"], campos=aceitos)
        detalhe = f"Campos: {sorted(aceitos)}. {detalhe}"
    db.registrar_conferencia(conn, vaga_id=vaga["id"], conferido_por=CONFERIDO_POR, resultado=curto, detalhe=(detalhe or resultado)[:1000])
    return curto


def main() -> None:
    conn = db.conectar()
    contagem = {"campo_preenchido": 0, "sem_alteracao": 0, "erro_gemini": 0, "erro": 0}
    try:
        candidatas = db.listar_vagas_abertas_para_datas_extras(conn, limite=LINHAS_BUSCADAS)
        por_edital: dict[str, list[dict]] = {}
        for vaga in candidatas:
            por_edital.setdefault(vaga["url"], []).append(vaga)
        print(f"{len(candidatas)} vaga(s) em {len(por_edital)} edital(is); até {MAX_CHAMADAS} chamada(s) nesta execução.")

        for url, vagas in list(por_edital.items())[:MAX_CHAMADAS]:
            print(f"{vagas[0]['cargo']} ({vagas[0]['nome']}/{vagas[0]['uf']}) + {len(vagas) - 1} do mesmo edital")
            try:
                resultado, aceitos = consultar_edital(vagas[0])
                for vaga in vagas:  # datas são do edital: valem para todas as vagas dele
                    contagem[registrar(conn, vaga, resultado, aceitos)] += 1
                conn.commit()
            except CotaGeminiEsgotadaError as exc:
                conn.rollback()
                print(f"  -> cota do Gemini esgotada ({exc}). Encerrando o lote aqui.")
                break
            except Exception as exc:
                conn.rollback()
                print(f"  -> erro inesperado, pulando: {exc!r}")
                contagem["erro"] += 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
    print(f"\nEncerrado: {contagem}")


if __name__ == "__main__":
    with db.rastrear_execucao("completude_datas_extras.py"):
        main()
