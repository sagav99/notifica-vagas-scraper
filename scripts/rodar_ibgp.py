#!/usr/bin/env python3
"""Entrypoint do cron pra fonte IBGP: descobre concursos com inscrição
aberta em `/rest/concurso/inscricaoAberta` (sem lista curada de
município, igual IMESO/IMAM/JCM/FUNDEP), lê a lista ESTRUTURADA de
cargos de cada concurso via `/rest/concurso/cargos/{id}` — fonte de
verdade de quantos/quais cargos existem, nenhuma especialidade
(médica ou não) é descartada — e usa Gemini só pra ler o SALÁRIO no PDF
do Anexo I (`/rest/concurso/editais/{id}`, ver `fontes/ibgp.py` pra
detalhe completo da API e do achado que motivou casar salário por
código de cargo, não por nome livre).

**`/rest/concurso/proximasInscricoes` (concursos "em breve") não é
processado aqui** — decisão de projeto, não pendência: sem inscrição
aberta ainda não existe vaga pra se candidatar de verdade; quando abrir,
o concurso passa a aparecer em `inscricaoAberta` e é pego no próximo
ciclo do cron (cadência de 3 dias).

**Município nem sempre vem limpo em `empresa.nome`** (achado real: caso
do INSTITUTO DE PREVIDÊNCIA DE ITABIRA/ITABIRAPREV, onde só o título
completo do concurso — `item.nome` — cita "/MG", `empresa.nome` não tem
barra nenhuma) — `_resolver_municipio_uf` tenta `empresa.nome` e depois
`nome`, via `ibgp.extrair_candidatos_municipio_uf`, validando cada
candidato contra o IBGE de verdade (única parte com rede desta função —
por isso mora aqui, não em `fontes/ibgp.py`). Se nada bater em MG/SP, o
concurso é pulado com aviso — sem risco de gravar município errado.

Se o concurso ainda não tem o Anexo I de vencimento publicado
(`escolher_edital_vencimento` devolve `None`), o concurso inteiro é
pulado com aviso: sem PDF não há `url_evidencia` (campo obrigatório em
`db.inserir_vaga_com_evidencia`), mesmo padrão de JCM/FUNDEP quando não
há documento nenhum listado.

Uso: python scripts/rodar_ibgp.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_pdf, ibge
from notifica_vagas_scraper.fontes import ibgp

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "IBGP"

#: só MG foi confirmado na investigação (todos os 15 concursos abertos e
#: os 23 "em breve" da amostra são de MG), mas filtra por segurança,
#: mesmo padrão de rodar_jcm.py/rodar_fundep.py.
UFS_DO_PROJETO = ("MG", "SP")


def _resolver_municipio_uf(item: ibgp.ItemListagem) -> tuple[str, str, int] | None:
    """Devolve `(municipio, uf, codigo_ibge)` ou `None` se não achar em
    MG/SP nenhum candidato válido — ver docstring do módulo."""
    candidatos = ibgp.extrair_candidatos_municipio_uf(item.empresa_nome, item.nome)
    for municipio, uf in candidatos:
        if uf not in UFS_DO_PROJETO:
            continue
        codigo_ibge = ibge.buscar_codigo_ibge(municipio, uf)
        if codigo_ibge is not None:
            return municipio, uf, codigo_ibge
    return None


def processar_concurso(conn, fonte_id: str, item: ibgp.ItemListagem) -> int:
    resolvido = _resolver_municipio_uf(item)
    if resolvido is None:
        print(f"  aviso: município não identificado pra '{item.nome}' (nem em empresa.nome, nem no título), pulando")
        return 0
    municipio, uf, codigo_ibge = resolvido

    resposta_cargos = requests.get(ibgp.url_cargos(item.concurso_id), headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta_cargos.raise_for_status()
    cargos = ibgp.listar_cargos(resposta_cargos.json())
    if not cargos:
        print(f"  aviso: '{item.nome}' sem cargos listados em /rest/concurso/cargos, pulando")
        return 0

    resposta_editais = requests.get(ibgp.url_editais(item.concurso_id), headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta_editais.raise_for_status()
    documentos = ibgp.listar_editais(resposta_editais.json())
    edital = ibgp.escolher_edital_vencimento(documentos)
    if edital is None:
        print(f"  aviso: '{item.nome}' ainda sem Anexo I de vencimento publicado, pulando")
        return 0

    url_pdf = ibgp.montar_url_download(item.concurso_id, edital.id, edital.nome_real)
    pdf_resposta = requests.get(url_pdf, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    # cargos/vagas vêm 100% de `listar_cargos` (estruturado, nenhum é
    # descartado) — o Gemini só entra pra casar salário por código, ver
    # docstring de `parear_salario_por_codigo`.
    salarios_por_codigo = ibgp.parear_salario_por_codigo(cargos, extraido.get("vagas") or [])

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=municipio, uf=uf)
    orgao = extraido.get("orgao") or f"{item.tipo.title()} de {municipio}/{uf}"
    numero_edital = extraido.get("numero_edital") or item.numero_edital or None
    inscricoes_inicio = item.inicio_inscricao.date() if item.inicio_inscricao else None
    inscricoes_fim = item.fim_inscricao.date() if item.fim_inscricao else None

    total = 0
    for cargo in cargos:
        salario = salarios_por_codigo.get(cargo.codigo)
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=ibgp.identificador_externo(item.concurso_id, cargo),
            orgao=orgao,
            cargo=cargo.nome,
            salario=salario,
            numero_edital=numero_edital,
            data_publicacao=edital.data,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=f"{item.tipo.title()} nº {numero_edital or '?'} — {cargo.nome}"
            + (f" ({cargo.total_vagas} vaga(s))." if cargo.total_vagas else " (cadastro de reserva)."),
            url_evidencia=url_pdf,
            tipo_documento="pdf",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        salario_str = f"R$ {salario:.2f}" if salario else "salário não identificado"
        print(f"    {cargo.nome} ({salario_str}): vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(
        f"{ibgp.BASE_URL}/rest/concurso/inscricaoAberta", headers={"User-Agent": USER_AGENT}, timeout=20
    )
    resposta.raise_for_status()
    itens = ibgp.listar_concursos(resposta.json())

    print(f"{len(itens)} concurso(s) com inscrição aberta na IBGP.")

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=ibgp.BASE_URL, tipo="oficial", uf="MG")
        conn.commit()

        total_geral = 0
        for item in itens:
            print(f"Processando {item.nome} ({item.numero_edital})...")
            try:
                with conn.transaction():
                    total_geral += processar_concurso(conn, fonte_id, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 concurso derrubar o lote inteiro
                print(f"  ERRO processando '{item.nome}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_ibgp.py"):
        main()
