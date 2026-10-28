#!/usr/bin/env python3
"""Entrypoint do cron da Fundatec (`fontes/fundatec.py`).

Fluxo: lista o bucket público de editais -> concursos com edital publicado
nos últimos `--dias` dias -> PDF -> texto local (sem IA) -> só prefeitura
de UF do projeto com linha de cargo "Médico..." e número nunca visto ->
Gemini (1 chamada por edital com médico) -> 1 vaga por cargo médico.
Datas de inscrição vêm do Gemini (a listagem do site é bloqueada por WAF).

Uso: python scripts/rodar_fundatec.py [--dry-run] [--dias 45] [--ultimos 80] [--max-gemini 6]
Requer DATABASE_URL e GEMINI_API_KEY (a menos que --dry-run).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, gemini_util
from notifica_vagas_scraper.classificar_medico import eh_cargo_medico
from notifica_vagas_scraper.fontes import fundatec
from notifica_vagas_scraper.ufs import UFS_PROJETO

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Fundatec (editais publicados)"
FONTE_URL = "https://www.fundatec.org.br/portal/concursos/"


def _get(url, **kw):
    kw.setdefault("headers", {"User-Agent": USER_AGENT})
    return requests.get(url, **kw)


def _data_iso(texto) -> date | None:
    try:
        return date.fromisoformat(str(texto)[:10]) if texto else None
    except ValueError:
        return None


def candidatos(dias: int, ultimos: int) -> list[tuple[fundatec.EditalFundatec, str, str]]:
    """Editais recentes de prefeituras das UFs do projeto com cargo médico."""
    ids = fundatec.listar_ids_concursos(_get)
    print(f"{len(ids)} concurso(s) no bucket; olhando os {ultimos} mais recentes.")
    achados = []
    for cid in ids[-ultimos:]:
        editais = fundatec.editais_do_concurso(_get, cid)
        if not editais or not fundatec.recentes(editais, dias=dias):
            continue
        edital = editais[-1]  # versão mais recente (retificação)
        try:
            resp = _get(edital.url, timeout=90)
            resp.raise_for_status()
            texto = fundatec.texto_pdf(resp.content)
        except Exception as exc:  # 1 PDF ruim não derruba o lote
            print(f"  aviso: concurso {cid}: {exc}")
            continue
        local = fundatec.municipio_e_uf(texto[:1500])
        if local is None or local[1] not in UFS_PROJETO:
            continue
        if not fundatec.tem_cargo_medico(texto):
            continue
        achados.append((edital, local[0], local[1]))
    return achados


def tem_medico_em_aberto(conn, municipio_id: int) -> bool:
    """Evita reprocessar (e duplicar) edital que OCM/Ache/diários já trouxeram."""
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from public.vagas where municipio_id = %s and categoria_saude = 'medico' "
            "and (inscricoes_fim is null or inscricoes_fim >= current_date) limit 1",
            (municipio_id,),
        )
        return cur.fetchone() is not None


def processar(conn, fonte_id: str, edital, nome: str, uf: str, municipios, max_gemini: list[int]) -> int:
    codigo = fundatec.casar_municipio(nome, uf, municipios)
    if codigo is None:
        print(f"  aviso: município '{nome}/{uf}' não casou no cadastro")
        return 0
    if tem_medico_em_aberto(conn, codigo):
        print("  município já tem vaga médica aberta/futura (outra fonte); ignorado")
        return 0
    if max_gemini[0] <= 0:
        print("  limite de Gemini da execução atingido; fica para a próxima")
        return 0
    max_gemini[0] -= 1
    pdf = _get(edital.url, timeout=90)
    pdf.raise_for_status()
    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf.content)
    fim = _data_iso(extraido.get("inscricoes_fim"))
    if fim is not None and fim < date.today():
        print(f"  inscrição encerrada em {fim}; ignorado")
        return 0
    inicio = _data_iso(extraido.get("inscricoes_inicio"))
    status = "futura" if inicio and inicio > date.today() else "aberta"
    orgao = extraido.get("orgao") or f"Prefeitura Municipal de {nome}"
    total = 0
    for vaga in extraido.get("vagas") or []:
        cargo = vaga.get("cargo")
        if not cargo or not eh_cargo_medico(cargo):
            continue
        slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo,
            identificador_externo=f"{fundatec.ID_PREFIX}-{edital.concurso_id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=extraido.get("tipo_oportunidade"),
            numero_edital=extraido.get("numero_edital"),
            data_publicacao=_data_iso(extraido.get("data_publicacao")),
            inscricoes_inicio=inicio,
            inscricoes_fim=fim,
            status=status,
            resumo=f"{orgao} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=edital.url,
            tipo_documento="pdf",
            texto_extraido=None,
            **gemini_util.campos_estruturados_extras(extraido, vaga),
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--dias", type=int, default=45)
    p.add_argument("--ultimos", type=int, default=80)
    p.add_argument("--max-gemini", type=int, default=6)
    args = p.parse_args(argv)

    achados = candidatos(args.dias, args.ultimos)
    print(f"{len(achados)} edital(is) de prefeitura com cargo médico.")
    if args.dry_run:
        for e, nome, uf in achados:
            print(f"  {e.concurso_id} | {nome}/{uf} | {e.modificado:%Y-%m-%d} | {e.url}")
        return

    conn = db.conectar()
    try:
        municipios = {
            (fundatec._sem_acento(nome), uf): codigo
            for codigo, nome, uf in db.listar_municipios_com_codigo(conn, ufs=list(UFS_PROJETO))
        }
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=FONTE_URL, tipo="oficial", uf="RS")
        conn.commit()
        ja = db.listar_identificadores_processados(conn, fonte_id)
        restante = [args.max_gemini]
        total = 0
        for edital, nome, uf in achados:
            if db.item_ja_processado(f"{fundatec.ID_PREFIX}-{edital.concurso_id}-", ja):
                continue
            print(f"Processando {nome}/{uf} (concurso {edital.concurso_id})...")
            try:
                with conn.transaction():
                    total += processar(conn, fonte_id, edital, nome, uf, municipios, restante)
                conn.commit()
            except Exception as exc:
                print(f"  ERRO processando concurso {edital.concurso_id}: {exc}")
        print(f"\nOk. {total} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        main()
    else:
        with db.rastrear_execucao("rodar_fundatec.py"):
            main()
