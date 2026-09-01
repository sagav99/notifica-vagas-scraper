#!/usr/bin/env python3
"""Entrypoint do cron: revisa toda vaga pendente via Gemini (1 chamada por
vaga) e grava aprovada/rejeitada direto no banco — sem admin humano no
loop (decisão do usuário em 2026-09-01, cancelou os botões aprovar/
rejeitar do painel /admin do repo principal). Ver
docs/revisao_automatica_gemini.md no repo principal para o desenho
completo e como auditar uma decisão.

Uso: python scripts/revisar_vagas.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import db, revisao_ia


def montar_dados_para_revisao(vaga: dict) -> dict:
    return {
        "municipio": f"{vaga['municipio_nome']}/{vaga['municipio_uf']}",
        "orgao": vaga["orgao"],
        "cargo": vaga["cargo"],
        "salario": float(vaga["salario"]) if vaga["salario"] is not None else None,
        "numero_edital": vaga["numero_edital"],
        "data_publicacao": vaga["data_publicacao"],
        "inscricoes_inicio": vaga["inscricoes_inicio"],
        "inscricoes_fim": vaga["inscricoes_fim"],
        "status": vaga["status"],
        "resumo": vaga["resumo"],
        "evidencias": [
            {
                "fonte": ev["fonte"],
                "url": ev["url"],
                "tipo_documento": ev["tipo_documento"],
                "texto_extraido": ev["texto_extraido"],
            }
            for ev in vaga["evidencias"]
        ],
    }


def main() -> None:
    conn = db.conectar()
    try:
        vagas = db.listar_vagas_pendentes(conn)
        print(f"{len(vagas)} vaga(s) pendente(s) de revisão.")
        for vaga in vagas:
            dados = montar_dados_para_revisao(vaga)
            resultado = revisao_ia.decidir_revisao(dados)
            db.aplicar_revisao(
                conn, vaga_id=vaga["id"], decisao=resultado["decisao"], motivo=resultado["motivo"]
            )
            conn.commit()  # por vaga, não em lote — sobrevive a timeout no meio do cron
            local = f"{vaga['municipio_nome']}/{vaga['municipio_uf']}"
            print(f"  {vaga['cargo']} ({local}): {resultado['decisao']} — {resultado['motivo']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
