#!/usr/bin/env python3
"""Entrypoint do cron pra plataforma ACT Sistemas (Instituto Legalle e
Instituto Tupy, bancas do Sul). 100% determinístico: sem Gemini, sem Serper.

Fluxo por tenant: GET `/edital/index/abertos` -> editais com inscrição
aberta -> GET `/edital/ver/<id>` de cada um (município-UF, PDFs, cargos).
Só cargos médicos (`classificar_medico.eh_cargo_medico`, que já exclui
Médico Veterinário) viram vaga; nenhum cargo médico é descartado (toda
especialidade listada é gravada). Legalle traz remuneração no HTML; Tupy
não (salário fica null, vem do PDF na etapa de verificação/completude).

Dedup: `identificador_externo` (`act-<tenant>-<edital>-<cargo>`) contra o já
gravado na fonte, antes de qualquer insert; cargo novo numa retificação
entra, o que já existe não é reprocessado.

Uso: python scripts/rodar_act_sistemas.py [--dry-run]
--dry-run só lista (conecta ao banco só para ler; não escreve).
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.classificar_medico import eh_cargo_medico
from notifica_vagas_scraper.fontes import act_sistemas as act
from notifica_vagas_scraper.ufs import UFS_PROJETO

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
UFS_DO_PROJETO = set(UFS_PROJETO)


def _get(url: str) -> str:
    resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()
    return resposta.text


def processar_edital(conn, fonte_id: str, tenant: str, item: act.ItemListagem, ja_processados: set[str], dry_run: bool) -> int:
    detalhe = act.parse_detalhe(_get(item.url_detalhe))
    if not detalhe.municipio or not detalhe.uf:
        print(f"  aviso: '{item.orgao}' ({item.url_detalhe}) sem 'Município - UF' no detalhe, pulando")
        return 0
    if detalhe.uf not in UFS_DO_PROJETO:
        print(f"  fora do escopo ({detalhe.uf}): {item.orgao}")
        return 0

    medicos = [c for c in detalhe.cargos if eh_cargo_medico(c.nome)]
    if not medicos:
        print(f"  sem cargo médico: {item.orgao} ({len(detalhe.cargos)} cargo(s))")
        return 0

    codigo_ibge = db.buscar_codigo_ibge_local(conn, detalhe.municipio, detalhe.uf) or ibge.buscar_codigo_ibge(
        detalhe.municipio, detalhe.uf
    )
    if codigo_ibge is None:
        print(f"  aviso: município '{detalhe.municipio}/{detalhe.uf}' não encontrado no IBGE, pulando")
        return 0

    edital = act.escolher_edital(detalhe.arquivos)
    url_evidencia = edital.url if edital else item.url_detalhe
    tipo_documento = "pdf" if edital else "html"
    titulo = detalhe.titulo or item.tipo_numero
    numero = act.numero_edital(titulo)
    banca = act.TENANTS[tenant]["banca"]

    if not dry_run:
        db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=detalhe.municipio, uf=detalhe.uf)

    total = 0
    gravados: set[str] = set()
    for cargo in medicos:
        ident = act.identificador_externo(tenant, item.edital_id, cargo.nome)
        if ident in ja_processados or ident in gravados:
            continue
        gravados.add(ident)
        if dry_run:
            print(f"    FALTA: {cargo.nome} | {detalhe.municipio}/{detalhe.uf} | {cargo.salario} | {url_evidencia}")
            total += 1
            continue
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=ident,
            orgao=detalhe.orgao or item.orgao,
            cargo=cargo.nome,
            salario=cargo.salario,
            salario_tipo="mensal" if cargo.salario is not None else None,
            tipo_oportunidade=act.tipo_oportunidade(titulo),
            numero_edital=numero,
            data_publicacao=edital.data if edital else None,
            inscricoes_inicio=detalhe.inscricoes_inicio or item.inscricoes_inicio,
            inscricoes_fim=detalhe.inscricoes_fim or item.inscricoes_fim,
            status="aberta",
            resumo=f"{titulo} — {cargo.nome}" + (f" ({cargo.requisitos})" if cargo.requisitos else "."),
            url_evidencia=url_evidencia,
            tipo_documento=tipo_documento,
            texto_extraido=None,
            requisitos=cargo.requisitos,
            banca_organizadora=banca,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo.nome}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def main(dry_run: bool) -> None:
    conn = db.conectar()
    try:
        total_geral = 0
        for tenant, cfg in act.TENANTS.items():
            nome_fonte = cfg["banca"]
            itens = act.parse_listagem(_get(act.url_listagem(tenant)), tenant)
            print(f"{nome_fonte}: {len(itens)} edital(is) com inscrição aberta.")
            fonte_id = db.upsert_fonte(conn, nome=nome_fonte, url=cfg["base"], tipo="oficial", uf="SC")
            if not dry_run:
                conn.commit()
            ja_processados = db.listar_identificadores_processados(conn, fonte_id)
            for item in itens:
                print(f"Processando {item.orgao} — {item.tipo_numero} (id {item.edital_id})...")
                try:
                    with conn.transaction():
                        total_geral += processar_edital(conn, fonte_id, tenant, item, ja_processados, dry_run)
                    if not dry_run:
                        conn.commit()
                except Exception as exc:  # um edital ruim não derruba o lote
                    print(f"  ERRO processando '{item.orgao}': {exc}")
        if dry_run:
            conn.rollback()
        print(f"\nOk. {total_geral} vaga(s) {'a gravar (dry-run)' if dry_run else 'processada(s)'}.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    with db.rastrear_execucao("rodar_act_sistemas.py"):
        main(dry)
