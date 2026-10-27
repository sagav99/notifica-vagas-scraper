#!/usr/bin/env python3
"""Entrypoint do cron pros tenants ProSeleta do Sul: FURB
(concursos.furb.br), Objetiva (concursos.objetivas.com.br) e Univali
(univali.selecao.net.br). Investigação: `docs/investigacao_proseleta_sul_
2026-10-27.md` no repo principal.

Diferente de JCM/ACCESS, NÃO usa Gemini: a página de detalhe do processo
traz a tabela de vagas (cargo, escolaridade, salário/vencimento, carga
horária, quantidade, taxa) já estruturada em HTML — ver
`proseleta.listar_vagas_tabela`. Só cargo médico (`eh_cargo_medico`) é
gravado, mas TODA linha médica da tabela é gravada (nenhuma
especialidade é descartada). O PDF do edital de abertura entra só como
`url_evidencia`.

Lê `/index/abertos/` e `/index/1/` (em andamento, que inclui os que abrem
em breve), dedup por `processo_id`, e só processa quem tem fim de
inscrição >= hoje (Brasília) — ou está em `abertos` sem datas.

Uso: python scripts/rodar_proseleta_sul.py [--tenant furb|objetiva|univali] [--dry-run]
`--dry-run` não grava nada (nem fonte, nem município, nem vaga, nem
`execucoes_scraper`); ainda lê o banco (município/dedup) e a rede.
Requer DATABASE_URL no ambiente.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.classificar_medico import eh_cargo_medico
from notifica_vagas_scraper.fontes import proseleta
from notifica_vagas_scraper.fontes.proseleta import Tenant
from notifica_vagas_scraper.ufs import UFS_PROJETO

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
UFS_DO_PROJETO = set(UFS_PROJETO)
PAUSA_ENTRE_REQUISICOES = 1.0


def hoje_brasilia() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def tipo_oportunidade(item: proseleta.ItemListagemTenant) -> str | None:
    texto = f"{item.tipo or ''} {item.titulo}".lower()
    if "concurso p" in texto:
        return "concurso_efetivo"
    if "processo seletivo" in texto or " act " in f" {texto} ":
        return "processo_seletivo_temporario"
    return None


def identificador_externo(tenant: Tenant, processo_id: int, codigo: str | None, cargo: str) -> str:
    return f"proseleta-{tenant.chave}-{processo_id}-{codigo or cargo}"


def deve_processar(item: proseleta.ItemListagemTenant, hoje: date) -> bool:
    if item.inscricoes_fim is not None:
        return item.inscricoes_fim >= hoje
    return item.em_abertos  # sem datas: só se o site o lista em "abertos"


def _baixar(url: str) -> str:
    resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resposta.raise_for_status()
    return proseleta.decodificar(resposta.content)


def listar_itens(tenant: Tenant) -> list[proseleta.ItemListagemTenant]:
    listas = []
    for caminho in proseleta.CAMINHOS_LISTAGEM:
        html = _baixar(f"{tenant.base_url}{caminho}")
        listas.append(proseleta.listar_processos_abertos(html, tenant, em_abertos=caminho == "/index/abertos/"))
        time.sleep(PAUSA_ENTRE_REQUISICOES)
    return proseleta.mesclar_listagens(*listas)


def _resolver_codigo_ibge(conn, municipio: str, uf: str) -> int | None:
    return db.buscar_codigo_ibge_local(conn, municipio, uf) or ibge.buscar_codigo_ibge(municipio, uf)


def processar_processo(
    conn,
    fonte_id: str | None,
    tenant: Tenant,
    item: proseleta.ItemListagemTenant,
    ja_processados: set[str],
    *,
    dry_run: bool = False,
) -> int:
    if not item.municipio or not item.uf:
        print(f"  aviso: município/UF não identificado no título '{item.titulo}', pulando")
        return 0
    if item.uf not in UFS_DO_PROJETO:
        print(f"  fora do projeto ({item.uf}), pulando")
        return 0

    html = _baixar(item.url)
    time.sleep(PAUSA_ENTRE_REQUISICOES)
    tabela = proseleta.listar_vagas_tabela(html)
    medicas = [v for v in tabela if eh_cargo_medico(v.cargo)]
    print(f"  {len(tabela)} cargo(s) na tabela, {len(medicas)} médico(s)")
    if not medicas:
        return 0

    edital = proseleta.escolher_edital_sul(proseleta.listar_documentos(html))
    if edital is None:
        print("  aviso: sem documentos listados (url_evidencia obrigatória), pulando")
        return 0

    codigo_ibge = _resolver_codigo_ibge(conn, item.municipio, item.uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{item.municipio}/{item.uf}' não encontrado no IBGE, pulando")
        return 0

    if not dry_run:
        db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=item.municipio, uf=item.uf)

    total = 0
    for vaga in medicas:
        ident = identificador_externo(tenant, item.processo_id, vaga.codigo, vaga.cargo)
        if ident in ja_processados:
            continue
        salario_tipo = "mensal" if vaga.salario is not None else None
        if dry_run:
            print(f"    [dry-run] {vaga.cargo} salário={vaga.salario} taxa={vaga.taxa_inscricao} id={ident}")
            total += 1
            continue
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=ident,
            orgao=item.orgao,
            cargo=vaga.cargo,
            salario=vaga.salario,
            salario_tipo=salario_tipo,
            tipo_oportunidade=tipo_oportunidade(item),
            numero_edital=item.numero_edital,
            data_publicacao=edital.data,
            inscricoes_inicio=item.inscricoes_inicio,
            inscricoes_fim=item.inscricoes_fim,
            status="aberta",
            resumo=f"{item.tipo or 'Processo'} nº {item.numero_edital or '?'} — {vaga.cargo}"
            + (f" ({vaga.quantidade} vaga(s))." if vaga.quantidade else " (cadastro de reserva)."),
            url_evidencia=edital.url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
            numero_vagas=vaga.quantidade,
            taxa_inscricao=vaga.taxa_inscricao,
            carga_horaria=vaga.carga_horaria,
            banca_organizadora=tenant.banca,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {vaga.cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def main(tenants: list[Tenant], dry_run: bool) -> None:
    hoje = hoje_brasilia()
    conn = db.conectar()
    try:
        total_geral = 0
        for tenant in tenants:
            print(f"== {tenant.nome_fonte} ==")
            itens = [i for i in listar_itens(tenant) if deve_processar(i, hoje)]
            print(f"{len(itens)} processo(s) vigente(s).")

            fonte_id = None
            ja_processados: set[str] = set()
            if not dry_run:
                fonte_id = db.upsert_fonte(
                    conn, nome=tenant.nome_fonte, url=tenant.base_url, tipo="oficial", uf=tenant.uf_padrao or "RS"
                )
                conn.commit()
                ja_processados = db.listar_identificadores_processados(conn, fonte_id)

            for item in itens:
                print(f"Processando {item.titulo}...")
                try:
                    if dry_run:
                        total_geral += processar_processo(conn, fonte_id, tenant, item, ja_processados, dry_run=True)
                    else:
                        with conn.transaction():
                            total_geral += processar_processo(conn, fonte_id, tenant, item, ja_processados)
                        conn.commit()
                except Exception as exc:  # nunca deixar 1 processo derrubar o lote
                    print(f"  ERRO processando '{item.titulo}': {exc}")
        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _parse() -> tuple[list[Tenant], bool]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", choices=sorted(proseleta.TENANTS), default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    tenants = [proseleta.TENANTS[args.tenant]] if args.tenant else list(proseleta.TENANTS.values())
    return tenants, args.dry_run


if __name__ == "__main__":
    _tenants, _dry = _parse()
    if _dry:
        main(_tenants, True)
    else:
        with db.rastrear_execucao("rodar_proseleta_sul.py"):
            main(_tenants, False)
