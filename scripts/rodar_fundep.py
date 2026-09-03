#!/usr/bin/env python3
"""Entrypoint do cron pra fonte FUNDEP (fundep.selecao.net.br): descobre
processos com inscrição aberta em `/index/abertos/` (sem lista curada de
município, igual IMESO/IMAM/JCM/ACCESS), acha a versão mais recente do
edital entre as publicações e usa Gemini pra ler o PDF (salário não tem
campo estruturado em HTML — só cargo/quantidade têm, ver
`fontes/fundep.py`).

**Município nem sempre está na listagem** (achado real, diferente de
JCM/ACCESS/Avança SP): o card do DMAE/Uberlândia não cita "Uberlândia" em
lugar nenhum, só a sigla do órgão. `_resolver_municipio_uf` tenta, nesta
ordem: (1) `item.municipio` já extraído da listagem (cobre Prefeitura/
Câmara Municipal); (2) sufixos do TÍTULO DO EDITAL escolhido
(`fundep.candidatos_municipio_por_sufixo`, ex: "Uberlândia" no fim de
"... Departamento Municipal de Água e Esgoto  DMAE Uberlândia"),
validados contra o IBGE de verdade (única parte com rede desta função —
por isso mora aqui, não em `fontes/fundep.py`). Se nada bater em MG/SP, o
processo é pulado com aviso — sem risco de gravar município errado, só de
perder cobertura até reinvestigar.

Uso: python scripts/rodar_fundep.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import fundep

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "FUNDEP"

#: só MG foi confirmado na investigação (DMAE/Uberlândia, Câmara Municipal
#: de Passos), mas a FUNDEP é fundação da UFMG com alcance potencialmente
#: maior — filtra por segurança, mesmo padrão de rodar_jcm.py/rodar_access.py.
UFS_DO_PROJETO = ("MG", "SP")


def _resolver_municipio_uf(item: fundep.ItemListagem, edital: fundep.Documento | None) -> tuple[str, str, int] | None:
    """Devolve `(municipio, uf, codigo_ibge)` ou `None` se não achar em
    MG/SP nenhum candidato válido — ver docstring do módulo."""
    candidatos = [item.municipio] if item.municipio else []
    if edital is not None:
        candidatos.extend(fundep.candidatos_municipio_por_sufixo(edital.titulo))

    for candidato in candidatos:
        for uf in UFS_DO_PROJETO:
            codigo_ibge = ibge.buscar_codigo_ibge(candidato, uf)
            if codigo_ibge is not None:
                return candidato, uf, codigo_ibge
    return None


def processar_processo(conn, fonte_id: str, item: fundep.ItemListagem) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    documentos = fundep.listar_documentos(resposta.text)
    edital = fundep.escolher_edital(documentos)
    if edital is None:
        print(f"  aviso: '{item.tipo_processo} {item.numero_edital}' sem documentos listados")
        return 0

    resolvido = _resolver_municipio_uf(item, edital)
    if resolvido is None:
        print(f"  aviso: município não identificado pra '{item.titulo}' (nem na listagem, nem no título do edital), pulando")
        return 0
    municipio, uf, codigo_ibge = resolvido

    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{item.tipo_processo} {item.numero_edital}' ({edital.url_pdf})")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio, uf=uf)
    orgao = extraido.get("orgao") or f"{item.tipo_processo} de {municipio}/{uf}"
    numero_edital = extraido.get("numero_edital") or item.numero_edital
    tipo_oportunidade = extraido.get("tipo_oportunidade")

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
            identificador_externo=f"fundep-{item.processo_id}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=edital.data,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=f"{item.tipo_processo} nº {item.numero_edital} — {cargo}"
            + (f" ({vaga['requisitos']})" if vaga.get("requisitos") else "."),
            url_evidencia=edital.url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(f"{fundep.BASE_URL}/index/abertos/", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    itens = fundep.listar_processos_abertos(resposta.text)

    print(f"{len(itens)} processo(s) com inscrição aberta (vestibular/entrada em curso já filtrado).")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=fundep.BASE_URL, tipo="oficial", uf="MG")
        conn.commit()

        total_geral = 0
        for item in itens:
            print(f"Processando {item.titulo} ({item.tipo_processo} {item.numero_edital})...")
            try:
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.titulo}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_fundep.py"):
        main()
