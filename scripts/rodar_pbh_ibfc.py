#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Prefeitura de Belo Horizonte/MG (PBH),
banca IBFC: lê a listagem geral filtrada por "Concurso Público"
(`fontes/pbh_ibfc.listar_processos`), pra cada item processável abre a
página fixa do edital, escolhe o PDF com o ANEXO I vigente
(`escolher_edital_com_anexo`) e usa Gemini pra extrair cargo/
especialidade/jornada/vagas/salário (sem estrutura HTML, só existe no
PDF — mesmo padrão de FGV/Actcon/FUNDEP/IMAM/IBGP/INEPAM).

Diferente das outras fontes deste projeto, **não há descoberta de
tenant/município nenhuma aqui** — é um único município fixo (Belo
Horizonte/MG, ver `fontes/pbh_ibfc.MUNICIPIO`/`UF`), resolvido uma vez só
no início via `ibge.buscar_codigo_ibge` (mesmo padrão de resolução das
outras fontes, sem hardcode do código IBGE).

Uso: python scripts/rodar_pbh_ibfc.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import pbh_ibfc

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "PBH (IBFC)"


def _processos_externos(itens: list[pbh_ibfc.ItemListagem]) -> list[pbh_ibfc.ItemListagem]:
    """Extraído à parte só pra ser testável sem rede (`test_rodar_pbh_ibfc.py`)
    — 2ª camada de defesa contra o Edital 155/2026 (promoção interna) e
    qualquer item parecido, ver `fontes/pbh_ibfc.eh_concurso_externo_processavel`."""
    return [i for i in itens if pbh_ibfc.eh_concurso_externo_processavel(i)]


def processar_processo(conn, fonte_id: str, codigo_ibge: int, item: pbh_ibfc.ItemListagem) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    documentos = pbh_ibfc.listar_documentos(resposta.text)
    edital = pbh_ibfc.escolher_edital_com_anexo(documentos)
    if edital is None:
        print(f"  aviso: '{item.area}' (edital {item.numero_edital}) sem PDF de edital publicado, pulando")
        return 0

    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{item.area}' ({edital.url_pdf})")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=pbh_ibfc.MUNICIPIO, uf=pbh_ibfc.UF)
    orgao = extraido.get("orgao") or f"Prefeitura de {pbh_ibfc.MUNICIPIO}/{pbh_ibfc.UF} — {item.area}"
    numero_edital = extraido.get("numero_edital") or item.numero_edital
    tipo_oportunidade = extraido.get("tipo_oportunidade")
    # `edital.data` vem da tabela estruturada da página fixa (data de
    # publicação DO DOCUMENTO escolhido, campo confiável de verdade) —
    # preferido sobre o que o Gemini eventualmente leia do próprio PDF,
    # mesmo padrão de `rodar_ibgp.py` (`data_publicacao=edital.data`).
    data_publicacao = edital.data or extraido.get("data_publicacao")
    inscricoes_inicio = extraido.get("inscricoes_inicio")
    inscricoes_fim = extraido.get("inscricoes_fim")

    total = 0
    for vaga in extraido["vagas"]:
        cargo_bruto = vaga.get("cargo")
        if not cargo_bruto:
            continue
        # ver docstring de `fontes/pbh_ibfc.montar_cargo_com_jornada`:
        # sem isso, especialidades com mais de uma jornada (ex: "Médico -
        # Pediatria" em 12h/20h/24h) colidiriam no dedup e perderiam
        # linhas silenciosamente.
        cargo = pbh_ibfc.montar_cargo_com_jornada(cargo_bruto, vaga.get("carga_horaria"))
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=pbh_ibfc.identificador_externo(numero_edital, cargo),
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=f"Concurso Público nº {numero_edital or '?'} — {cargo}"
            + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=edital.url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        salario_str = f"R$ {vaga['salario']:.2f}" if vaga.get("salario") else "salário não identificado"
        print(f"    {cargo} ({salario_str}): vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(
        pbh_ibfc.URL_LISTAGEM,
        params=pbh_ibfc.PARAMS_LISTAGEM,
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resposta.raise_for_status()
    itens = pbh_ibfc.listar_processos(resposta.text)
    processaveis = _processos_externos(itens)

    print(f"{len(processaveis)} concurso(s) público(s) externo(s) na listagem da PBH (de {len(itens)} listados).")

    codigo_ibge = ibge.buscar_codigo_ibge(pbh_ibfc.MUNICIPIO, pbh_ibfc.UF)
    if codigo_ibge is None:
        raise RuntimeError(f"{pbh_ibfc.MUNICIPIO}/{pbh_ibfc.UF} não encontrado no IBGE — não deveria acontecer.")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=pbh_ibfc.BASE_URL, tipo="oficial", uf=pbh_ibfc.UF)
        conn.commit()

        total_geral = 0
        for item in processaveis:
            print(f"Processando {item.area} — Edital {item.numero_edital}...")
            try:
                # savepoint por item: erro num edital não deixa a
                # transação inteira do lote em estado abortado.
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, codigo_ibge, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 edital derrubar o lote inteiro
                print(f"  ERRO processando '{item.area}' (edital {item.numero_edital}): {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_pbh_ibfc.py"):
        main()
