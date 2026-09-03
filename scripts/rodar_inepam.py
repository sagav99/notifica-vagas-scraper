#!/usr/bin/env python3
"""Entrypoint do cron pra fonte INEPAM (`app.inepam.org.br`): descobre
processos com inscrição aberta em `/home.do` (seção "Inscrições Abertas"),
resolve o edital anexado na página do concurso e usa Gemini pra ler o PDF
(salário/vagas não têm campo estruturado em HTML — só nome do cargo tem,
ver `fontes/inepam.py`).

**Só processa "Inscrições Abertas"** — "Em Andamento" nesta banca, pelo
nome da seção e por analogia com o IMAM (banca mineira com nomenclatura
idêntica), indica inscrição já encerrada. `fontes/inepam.listar_processos_
pagina` continua disponível (testada contra a fixture completa de "Em
Andamento") se essa decisão for revista depois — ver docstring de
`fontes/inepam.py`.

Município: diferente da FUNDEP/Ache Concursos, aqui a listagem quase
sempre já traz "Município - UF - Prefeitura/Câmara (Municipal)" direto no
texto da linha (`fontes.inepam.extrair_municipio_uf`) — quando não traz
(consórcio, conselho, autarquia com sigla), o item é pulado, sem tentar
adivinhar pelo título do edital (diferente do fallback de sufixo da
FUNDEP): não há achado real ainda que justifique esse fallback aqui, e
"chutar" errado é pior que perder cobertura.

Uso: python scripts/rodar_inepam.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import inepam

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "INEPAM"

#: confirmado na investigação: MG e SP (Embu das Artes/SP, Baependi/MG
#: etc.), mas a banca atende outros estados também (MT, SC vistos na
#: amostra real) — filtra por segurança, mesmo padrão de
#: rodar_fundep.py/rodar_ache_concursos.py.
UFS_DO_PROJETO = ("MG", "SP")


def processar_processo(conn, fonte_id: str, item: inepam.ItemListagem) -> int:
    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    documentos = inepam.listar_documentos(resposta.text)
    edital = inepam.escolher_edital(documentos)
    if edital is None:
        print(f"  aviso: '{item.descricao}' sem documentos listados")
        return 0

    codigo_ibge = ibge.buscar_codigo_ibge(item.municipio, item.uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{item.municipio}/{item.uf}' não encontrado no IBGE, pulando")
        return 0

    inscricoes_inicio, inscricoes_fim = inepam.extrair_periodo_inscricao(resposta.text)

    pdf_resposta = requests.get(edital.url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra '{item.descricao}' ({edital.url_pdf})")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=item.municipio, uf=item.uf)
    orgao = extraido.get("orgao") or f"{item.orgao} de {item.municipio}"
    numero_edital = extraido.get("numero_edital") or item.numero_edital
    tipo_oportunidade = extraido.get("tipo_oportunidade")
    # Gemini pode achar uma data mais precisa dentro do PDF; sem fallback
    # pro período da própria página só quando o Gemini não devolver nada,
    # mesmo padrão dos scripts irmãos.
    data_publicacao = extraido.get("data_publicacao")
    inscricoes_inicio_final = extraido.get("inscricoes_inicio") or inscricoes_inicio
    inscricoes_fim_final = extraido.get("inscricoes_fim") or inscricoes_fim

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
            identificador_externo=f"inepam-{item.id_instituicao}-{item.id_concurso}-{slug_cargo}",
            orgao=orgao,
            cargo=cargo,
            salario=vaga.get("salario"),
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio_final,
            inscricoes_fim=inscricoes_fim_final,
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
    resposta = requests.get(f"{inepam.BASE_URL}/home.do", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    itens = inepam.listar_processos_home(resposta.text)

    abertos = [i for i in itens if i.status == "aberta"]
    print(f"{len(abertos)} processo(s) com inscrição aberta (de {len(itens)} listados nas 3 seções).")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=inepam.BASE_URL, tipo="oficial", uf="MG")
        conn.commit()

        total_geral = 0
        for item in abertos:
            if item.municipio is None or item.uf is None:
                print(f"  aviso: município não identificado pra '{item.descricao}', pulando")
                continue
            if item.uf not in UFS_DO_PROJETO:
                continue

            print(f"Processando {item.descricao} ({item.municipio}/{item.uf})...")
            try:
                # savepoint por item: erro num item não deixa a transação
                # inteira do lote em estado abortado pros próximos.
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.descricao}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_inepam.py"):
        main()
