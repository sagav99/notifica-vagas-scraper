#!/usr/bin/env python3
"""2ª passada de verificação da revisão automática — reavalia vaga cuja
decisão diverge da maioria de vagas irmãs do mesmo edital (mesma fonte +
município + numero_edital), com o consenso das irmãs como contexto extra
na chamada ao Gemini. Ver docstring de
`notifica_vagas_scraper.consistencia_revisao` pro desenho completo e o
achado da auditoria que motivou isso (2026-09-11).

Uso: python scripts/verificar_consistencia_revisao.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import consistencia_revisao, db, revisao_ia
from revisar_vagas import montar_dados_para_revisao


def main() -> None:
    conn = db.conectar()
    try:
        vagas = db.listar_vagas_revisadas_para_consistencia(conn)
        divergentes = consistencia_revisao.achar_decisoes_divergentes(vagas)
        print(f"{len(divergentes)} vaga(s) com decisão divergente da maioria de irmãs do mesmo edital.")

        corrigidas = 0
        for indice, vaga in enumerate(divergentes):
            dados = montar_dados_para_revisao(vaga)
            contexto = consistencia_revisao.montar_contexto_irmas(vaga)
            try:
                resultado = revisao_ia.decidir_revisao(dados, contexto_irmas=contexto)
            except revisao_ia.CotaGeminiEsgotadaError:
                print(
                    f"  Cota do Gemini esgotada — parando aqui. "
                    f"{len(divergentes) - indice} vaga(s) seguem pra próxima execução."
                )
                break

            local = f"{vaga['municipio_nome']}/{vaga['municipio_uf']}"

            # `decidir_revisao` devolve "rejeitada" tanto quando o Gemini
            # decide isso de verdade quanto quando a própria chamada falha
            # (rede, JSON inválido — ver docstring do módulo, prefixo
            # "[revisão automática]" no motivo). Achado da auditoria de
            # segurança (2026-09-11): sem essa distinção, uma falha
            # transitória na 2ª chamada derrubaria por engano uma vaga que
            # já estava "aprovada" — aqui é uma FALHA TÉCNICA, não uma
            # reavaliação real, então pula sem aplicar, igual ao 429.
            if resultado["motivo"].startswith("[revisão automática]"):
                print(f"  Pulada (falha técnica na 2ª chamada, não reavaliação real) — {vaga['cargo']} ({local}): {resultado['motivo']}")
                continue

            if resultado["decisao"] != vaga["revisao_status"]:
                db.aplicar_revisao(
                    conn,
                    vaga_id=vaga["id"],
                    decisao=resultado["decisao"],
                    motivo=f"[2ª passada, consenso de irmãs do edital] {resultado['motivo']}",
                )
                conn.commit()
                corrigidas += 1
                print(
                    f"  CORRIGIDA — {vaga['cargo']} ({local}): "
                    f"{vaga['revisao_status']} -> {resultado['decisao']}"
                )
            else:
                print(
                    f"  Mantida — {vaga['cargo']} ({local}): confirmado "
                    f"'{resultado['decisao']}' mesmo com o contexto das irmãs."
                )

        print(f"{corrigidas} vaga(s) corrigida(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("verificar_consistencia_revisao.py"):
        main()
