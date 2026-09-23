#!/usr/bin/env python3
"""Entrypoint do cron: audita vaga médica já aprovada com dado
estruturado incompleto, tenta completar relendo o mesmo link salvo
(Gemini) e, se o link estiver quebrado, tenta achar um substituto via
busca (Serper) — sem nunca apagar/trocar a evidência original, só soma.
Termina escrevendo um relatório em `relatorios/`.

Motivação (usuário, 2026-09-12): vaga incompleta obriga quem usa o site
a abrir o edital original pra descobrir o básico, e às vezes o link nem
abre a página certa.

Uso: python scripts/auditar_completude_vagas.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente; SERPER_API_KEY é
opcional (sem ela, só pula a etapa de link alternativo).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import db
from notifica_vagas_scraper.auditoria_completude import (
    LIMITE_SERPER_POR_EXECUCAO,
    buscar_link_alternativo,
    checar_link,
    encontrar_dados_cargo,
    extrair_dados_do_link,
    montar_campos_a_atualizar,
)
from notifica_vagas_scraper.revisao_ia import CotaGeminiEsgotadaError

#: Cap por execução — cada vaga aqui pode gastar 1 chamada de Gemini
#: (releitura) +, se o link estiver quebrado, 1 busca Serper. Cota
#: gratuita diária do Gemini Flash-Lite é ~1000 requisições (2 modelos
#: intercalados via `quota_gemini.py`, ver `gemini_quota_diaria`) — o
#: resto do pipeline (revisão + coleta) usa em torno de 450/dia no pico,
#: sobra folga real pra este job. Subido de 40 pra 200 (2026-09-23,
#: decisão do usuário) pra dar conta do backlog sem competir a ponto de
#: esgotar a cota do dia.
LIMITE_VAGAS_POR_EXECUCAO = 200
#: Baixado de 3 pra 1 (2026-09-23, decisão do usuário, achado da
#: auditoria de revisão Gemini 2026-09-22): com o teto de 3, vaga
#: faltando só 1-2 dos 13 campos (incl. banca_organizadora/tem_prova/
#: exige_curriculo, os 3 que a tela do usuário exibe) nunca era
#: selecionada pra reprocessar — 220 vagas médicas presas nessa situação
#: no achado real. Com cota sobrando, não faz sentido deixar vaga
#: incompleta parada só por um teto de lote conservador demais.
MINIMO_CAMPOS_FALTANDO = 1


def main() -> None:
    conn = db.conectar()
    linhas_relatorio: list[str] = []
    campos_preenchidos_total = 0
    vagas_completadas = 0
    links_quebrados = 0
    links_repostos = 0
    buscas_serper_usadas = 0
    sem_solucao: list[str] = []

    try:
        candidatas = db.listar_vagas_medicas_incompletas(
            conn, minimo_campos_faltando=MINIMO_CAMPOS_FALTANDO, limite=LIMITE_VAGAS_POR_EXECUCAO
        )
        print(f"{len(candidatas)} vaga(s) médica(s) aprovada(s) com dado incompleto (candidatas desta execução).")

        for vaga in candidatas:
            local = f"{vaga['nome']}/{vaga['uf']}"
            resultado_link = checar_link(vaga["url"])

            if not resultado_link.acessivel:
                links_quebrados += 1
                print(f"  {vaga['cargo']} ({local}): link quebrado — {resultado_link.motivo}")

                if buscas_serper_usadas >= LIMITE_SERPER_POR_EXECUCAO:
                    linhas_relatorio.append(
                        f"- **{vaga['cargo']}** ({local}) — link quebrado ({resultado_link.motivo}), "
                        f"teto de busca Serper desta execução já atingido, tenta na próxima."
                    )
                    sem_solucao.append(f"{vaga['cargo']} ({local}): link quebrado, sem busca nesta execução")
                    continue

                buscas_serper_usadas += 1
                item = buscar_link_alternativo(cargo=vaga["cargo"], orgao=vaga["orgao"], municipio=vaga["nome"], uf=vaga["uf"])
                if item is None or item.link == vaga["url"]:
                    linhas_relatorio.append(
                        f"- **{vaga['cargo']}** ({local}) — link quebrado ({resultado_link.motivo}), "
                        f"busca não achou substituto diferente."
                    )
                    sem_solucao.append(f"{vaga['cargo']} ({local}): link quebrado, sem substituto encontrado")
                    db.registrar_conferencia(
                        conn, vaga_id=vaga["id"], conferido_por="auditoria_completude",
                        resultado="link_quebrado_sem_substituto", detalhe=resultado_link.motivo,
                    )
                    conn.commit()
                    continue

                evidencia_id = db.registrar_evidencia_adicional(
                    conn,
                    vaga_id=vaga["id"],
                    fonte_id=vaga["fonte_id"],
                    identificador_externo=f"auditoria-completude-{vaga['id']}",
                    url=item.link,
                    tipo_documento="pagina_html",
                )
                if evidencia_id:
                    links_repostos += 1
                    linhas_relatorio.append(
                        f"- **{vaga['cargo']}** ({local}) — link original quebrado ({resultado_link.motivo}); "
                        f"achado e adicionado um novo: {item.link}"
                    )
                    db.registrar_conferencia(
                        conn, vaga_id=vaga["id"], conferido_por="auditoria_completude",
                        resultado="link_substituido", detalhe=item.link,
                    )
                else:
                    linhas_relatorio.append(
                        f"- **{vaga['cargo']}** ({local}) — link original quebrado; substituto achado já estava "
                        f"registrado como evidência desta vaga."
                    )
                    db.registrar_conferencia(
                        conn, vaga_id=vaga["id"], conferido_por="auditoria_completude",
                        resultado="link_substituto_ja_existia", detalhe=item.link,
                    )
                conn.commit()
                continue

            try:
                extraido = extrair_dados_do_link(vaga["url"], vaga["tipo_documento"])
            except CotaGeminiEsgotadaError:
                print(f"  Cota do Gemini esgotada — parando aqui.")
                linhas_relatorio.append("- Execução interrompida por cota do Gemini esgotada — resto fica pra próxima.")
                break

            if extraido is None:
                sem_solucao.append(f"{vaga['cargo']} ({local}): link acessível mas releitura falhou")
                db.registrar_conferencia(
                    conn, vaga_id=vaga["id"], conferido_por="auditoria_completude", resultado="releitura_falhou",
                )
                conn.commit()
                continue

            dados_cargo = encontrar_dados_cargo(extraido, vaga["cargo"])
            campos = montar_campos_a_atualizar(vaga, extraido, dados_cargo)

            if not campos:
                sem_solucao.append(f"{vaga['cargo']} ({local}): releitura não trouxe nada novo além do já salvo")
                db.registrar_conferencia(
                    conn, vaga_id=vaga["id"], conferido_por="auditoria_completude", resultado="sem_alteracao",
                )
                conn.commit()
                continue

            db.atualizar_campos_vaga(conn, vaga_id=vaga["id"], campos=campos)
            db.registrar_conferencia(
                conn, vaga_id=vaga["id"], conferido_por="auditoria_completude",
                resultado="campo_preenchido", detalhe=", ".join(sorted(campos)),
            )
            conn.commit()
            vagas_completadas += 1
            campos_preenchidos_total += len(campos)
            print(f"  {vaga['cargo']} ({local}): completou {sorted(campos)}")
            linhas_relatorio.append(f"- **{vaga['cargo']}** ({local}) — completou: {', '.join(sorted(campos))}")

    finally:
        conn.close()

    _escrever_relatorio(
        candidatas_verificadas=len(candidatas) if "candidatas" in locals() else 0,
        vagas_completadas=vagas_completadas,
        campos_preenchidos_total=campos_preenchidos_total,
        links_quebrados=links_quebrados,
        links_repostos=links_repostos,
        sem_solucao=sem_solucao,
        linhas_detalhe=linhas_relatorio,
    )


def _escrever_relatorio(
    *,
    candidatas_verificadas: int,
    vagas_completadas: int,
    campos_preenchidos_total: int,
    links_quebrados: int,
    links_repostos: int,
    sem_solucao: list[str],
    linhas_detalhe: list[str],
) -> None:
    diretorio = Path(__file__).parent.parent / "relatorios"
    diretorio.mkdir(exist_ok=True)
    caminho = diretorio / f"auditoria_completude_{date.today().isoformat()}.md"

    conteudo = [
        f"# Auditoria de completude — {date.today().isoformat()}",
        "",
        f"Rodado em {datetime.now(timezone.utc).isoformat()}.",
        "",
        "## Resumo",
        "",
        f"- Vagas médicas aprovadas verificadas nesta execução: {candidatas_verificadas}",
        f"- Vagas com algum campo completado: {vagas_completadas}",
        f"- Total de campos preenchidos: {campos_preenchidos_total}",
        f"- Links quebrados detectados: {links_quebrados}",
        f"- Links substitutos encontrados e adicionados: {links_repostos}",
        f"- Vagas sem solução nesta execução: {len(sem_solucao)}",
        "",
        "## Detalhe",
        "",
        *linhas_detalhe,
    ]

    if sem_solucao:
        conteudo += ["", "## Sem solução (ficou incompleta mesmo assim)", ""]
        conteudo += [f"- {linha}" for linha in sem_solucao]

    caminho.write_text("\n".join(conteudo) + "\n", encoding="utf-8")
    print(f"\nRelatório escrito em {caminho}")


if __name__ == "__main__":
    with db.rastrear_execucao("auditar_completude_vagas.py"):
        main()
