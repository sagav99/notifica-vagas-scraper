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


def checar_cronologia_inscricoes(vaga: dict) -> str:
    """Verificação determinística em Python (não confiar no Gemini pra
    aritmética de data — achado real analisando rejeição das vagas da FGV
    de São José dos Campos/SP em 2026-09-01: o modelo errou comparação de
    datas simples, afirmando 28/01 posterior a 03/02). Resultado vai no
    payload de revisão como fato já resolvido, pra revisao_ia.py não
    precisar (e não dever) recalcular."""
    inicio, fim = vaga["inscricoes_inicio"], vaga["inscricoes_fim"]
    if inicio is None or fim is None:
        return "Sem data de início e/ou fim de inscrição suficiente para checar ordem."
    if fim < inicio:
        return (
            f"INVÁLIDO: fim das inscrições ({fim.isoformat()}) é anterior ao "
            f"início ({inicio.isoformat()})."
        )
    return (
        f"Válido: fim das inscrições ({fim.isoformat()}) é igual ou posterior "
        f"ao início ({inicio.isoformat()})."
    )


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
        "checagem_cronologica_pre_computada": checar_cronologia_inscricoes(vaga),
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
    with db.rastrear_execucao("revisar_vagas.py"):
        main()
