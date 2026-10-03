#!/usr/bin/env python3
"""Entrypoint (a cada 3 dias) da conferência com O Concurso Médico: baixa a
lista deles, filtra MG/SP ainda abertos ou previstos, descarta o que o nosso
banco já tem (mesmo município + mesmo fim de inscrição) e, pro que falta,
baixa o PDF do edital (link da banca/prefeitura, não texto deles) e deixa o
Gemini extrair cargo/salário — daí em diante é o pipeline normal
(revisão + completude).

Trava de cota: no máximo `MAX_NOVOS_POR_EXECUCAO` editais novos por rodada;
o resto fica pra próxima. `--dry-run` só lista o que faltaria, sem Gemini
nem escrita.

Uso: python scripts/rodar_oconcursomedico.py [--dry-run]
Requer DATABASE_URL (e GEMINI_API_KEY fora do dry-run).
"""

from __future__ import annotations

import sys
import unicodedata
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, gemini_util
from notifica_vagas_scraper.fontes import oconcursomedico as ocm

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "O Concurso Médico (índice)"
MAX_NOVOS_POR_EXECUCAO = 5


def _chave(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().replace("-", " ").split())


def ja_temos(conn, municipio_id: int, fim: date | None) -> bool:
    """Já existe vaga de médico (qualquer status de revisão) nesse município
    com o mesmo fim de inscrição — mesmo edital já coletado por outra fonte."""
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from public.vagas where municipio_id = %s and categoria_saude = 'medico' "
            "and inscricoes_fim is not distinct from %s limit 1",
            (municipio_id, fim),
        )
        return cur.fetchone() is not None


def processar(conn, cand: ocm.Candidato, codigo_ibge: int) -> int:
    resposta = requests.get(cand.edital_url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resposta.raise_for_status()
    extraido = gemini_pdf.extrair_vagas_de_pdf(resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra {cand.edital_url}")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=cand.cidade, uf=cand.uf)
    fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=ocm.SITE_URL, tipo="oficial", uf=cand.uf)
    orgao = extraido.get("orgao") or cand.titulo
    numero_edital = extraido.get("numero_edital") or cand.titulo
    hoje = date.today()
    status = "futura" if cand.inscricoes_inicio and cand.inscricoes_inicio > hoje else "aberta"

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        slug_cargo = "".join(c if c.isalnum() else "-" for c in cargo.lower()).strip("-")
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=f"ocm-{cand.id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=extraido.get("tipo_oportunidade"),
            numero_edital=numero_edital,
            data_publicacao=None,
            inscricoes_inicio=cand.inscricoes_inicio,
            inscricoes_fim=cand.inscricoes_fim,
            status=status,
            resumo=f"{cand.titulo} — {cargo}" + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=cand.edital_url,
            tipo_documento="pdf",
            texto_extraido=None,
            **gemini_util.campos_estruturados_extras(extraido, vaga),
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1
    return total


def main(dry_run: bool) -> None:
    conn = db.conectar()
    try:
        resposta = requests.get(ocm.FEED_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
        resposta.raise_for_status()
        candidatos = ocm.listar_candidatos(resposta.json())
        print(f"{len(candidatos)} concurso(s) de MG/SP abertos ou previstos na lista deles.")

        codigo_por_nome_uf = {
            (_chave(nome), uf): codigo for codigo, nome, uf in db.listar_municipios_com_codigo(conn, ufs=["MG", "SP"])
        }
        ja_processados = db.listar_identificadores_por_fonte_nome(conn, FONTE_NOME)
        hoje = date.today()

        faltando: list[tuple[ocm.Candidato, int]] = []
        for cand in candidatos:
            if cand.inscricoes_fim and cand.inscricoes_fim < hoje:
                continue
            codigo = codigo_por_nome_uf.get((_chave(cand.cidade), cand.uf))
            if codigo is None:
                print(f"  sem município no cadastro: {cand.titulo} ({cand.cidade}/{cand.uf})")
                continue
            if ja_temos(conn, codigo, cand.inscricoes_fim) or db.item_ja_processado(f"ocm-{cand.id}-", ja_processados):
                continue
            if not cand.edital_url:
                print(f"  falta, mas sem link do edital ainda: {cand.titulo} ({cand.cidade}/{cand.uf})")
                continue
            faltando.append((cand, codigo))

        print(f"{len(faltando)} edital(is) que o nosso banco não tem.")
        for cand, _ in faltando:
            print(f"  FALTA: {cand.titulo} | {cand.cidade}/{cand.uf} | fim {cand.inscricoes_fim} | {cand.edital_url}")
        if dry_run:
            return

        total = 0
        for cand, codigo in faltando[:MAX_NOVOS_POR_EXECUCAO]:
            print(f"Processando: {cand.titulo} ({cand.edital_url})")
            try:
                with conn.transaction():
                    total += processar(conn, cand, codigo)
                conn.commit()
            except Exception as exc:  # um item ruim não derruba o lote
                print(f"  ERRO processando '{cand.titulo}': {exc}")
        restante = max(0, len(faltando) - MAX_NOVOS_POR_EXECUCAO)
        print(f"\nOk. {total} vaga(s) gravada(s); {restante} edital(is) ficam pra próxima rodada.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    with db.rastrear_execucao("rodar_oconcursomedico.py"):
        main(dry)
