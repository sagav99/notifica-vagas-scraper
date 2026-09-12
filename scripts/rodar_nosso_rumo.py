#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Instituto Nosso Rumo (`nossorumo.org.br`):
lê a homepage (único índice de descoberta — histórico completo dos
certames, sem paginação, ver `fontes/nosso_rumo.py`), casa cada certame
contra um município de MG/SP via `fgv.encontrar_municipio` e busca os
cargos via `/Cargo/Pesquisar`, processando só quem tem `status ==
"Inscrições Abertas"` (mesma decisão de escopo do Instituto Mais/INEPAM:
inscrição já fechada = nada novo pra notificar agora).

**Sem Gemini/PDF nesta fonte**: cargo, quantidade de vagas, salário,
escolaridade e requisitos já vêm estruturados na API JSON (ver docstring
do módulo `nosso_rumo.py`) — o PDF do edital está bloqueado pra download
direto, mas isso não afeta a gravação porque os campos essenciais não
dependem dele. `url_evidencia` aponta pra própria URL da API (auditável
por qualquer humano sem sessão de navegador).

Achado que motivou esta fonte (2026-09-06): Olímpia/SP 01/2026 tem 16
vagas médicas especialistas (R$ 7.727,13, prazo até 22/09/2026) hospedadas
só no domínio da banca — não aparecem no parser de município (padrão
Instar) porque o edital não está no site oficial da prefeitura.

Uso: python scripts/rodar_nosso_rumo.py
Requer DATABASE_URL no ambiente. Não usa Gemini.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import fgv, nosso_rumo

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Instituto Nosso Rumo"

#: portfólio confirmado na investigação é só SP (17 municípios + 2
#: secretarias estaduais), mas o filtro cobre MG também por segurança
#: (mesmo padrão de rodar_instituto_mais.py/rodar_access.py) — se a banca
#: abrir concurso em MG no futuro, é coberto automaticamente.
UFS_DO_PROJETO = ["MG", "SP"]


def _montar_resumo(cargo: nosso_rumo.Cargo) -> str:
    resumo = f"{cargo.concurso} — {cargo.cargo}"
    detalhes: list[str] = []
    if cargo.vagas is not None:
        detalhes.append(f"{cargo.vagas} vaga(s)" if cargo.vagas else "0 vaga(s) (cadastro de reserva)")
    if cargo.escolaridade:
        detalhes.append(cargo.escolaridade)
    if detalhes:
        resumo += f" ({'; '.join(detalhes)})"
    return resumo


def processar_certame(
    conn, fonte_id: str, item: nosso_rumo.ItemListagem, municipio: str, uf: str
) -> int:
    url_cargos = nosso_rumo.montar_url_cargos(item.id_projeto)
    resposta = requests.get(url_cargos, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    todos_cargos = nosso_rumo.listar_cargos(resposta.json())
    abertos = [c for c in todos_cargos if c.status == nosso_rumo.STATUS_INSCRICOES_ABERTAS]
    if not abertos:
        print(
            f"  aviso: '{item.nome_certame}' sem cargo com inscrição aberta agora "
            f"({len(todos_cargos)} cargo(s) no total), pulando"
        )
        return 0

    codigo_ibge = db.buscar_codigo_ibge_local(conn, municipio, uf) or ibge.buscar_codigo_ibge(municipio, uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{municipio}/{uf}' não encontrado no IBGE, pulando")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio, uf=uf)
    inscricoes_inicio, inscricoes_fim = nosso_rumo.parsear_periodo_inscricao(item.periodo_inscricao)

    total = 0
    for cargo in abertos:
        numero_edital = nosso_rumo.extrair_numero_edital(cargo.concurso) or nosso_rumo.extrair_numero_edital(
            item.nome_certame
        )
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=nosso_rumo.identificador_externo(cargo.id_cargo),
            orgao=cargo.concurso,
            cargo=cargo.cargo,
            salario=cargo.salario,
            salario_tipo="mensal" if cargo.salario is not None else None,
            tipo_oportunidade=nosso_rumo.classificar_tipo_oportunidade(cargo.concurso),
            numero_edital=numero_edital,
            data_publicacao=None,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=_montar_resumo(cargo),
            url_evidencia=url_cargos,
            tipo_documento="pagina_html",
            texto_extraido=None,
            banca_organizadora="Instituto Nosso Rumo",
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        salario_str = f"R$ {cargo.salario:.2f}" if cargo.salario is not None else "salário não identificado"
        print(f"    {cargo.cargo} ({salario_str}): vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(nosso_rumo.BASE_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    certames = nosso_rumo.listar_certames(resposta.text)

    print(f"{len(certames)} certame(s) encontrado(s) na homepage (histórico completo).")

    conn = db.conectar()
    try:
        municipios = db.listar_nomes_municipios(conn, ufs=UFS_DO_PROJETO)
        print(f"{len(municipios)} município(s) de MG/SP carregados pra match.")

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=nosso_rumo.BASE_URL, tipo="oficial", uf="SP")
        conn.commit()

        total_geral = 0
        for item in certames:
            # entidade sem município correspondente (ex: SEFAZ/SP, SEDS/SP
            # — secretarias estaduais) não mapeia 1:1 pra um município do
            # produto, é pulada sem aviso alarmante (esperado, não erro).
            match = fgv.encontrar_municipio(item.nome_certame, municipios)
            if match is None:
                continue
            municipio, uf = match

            print(f"Processando {item.nome_certame} -> {municipio}/{uf}...")
            try:
                # savepoint por item: erro num certame não deixa a
                # transação inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_certame(conn, fonte_id, item, municipio, uf)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 certame derrubar o lote inteiro
                print(f"  ERRO processando '{item.nome_certame}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_nosso_rumo.py"):
        main()
