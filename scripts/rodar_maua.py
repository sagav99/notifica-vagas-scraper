#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Mauá/SP: processo seletivo simplificado
(PSS) de médicos especialistas por tempo determinado, publicado no
subdomínio dedicado `processoseletivo.maua.sp.gov.br`. A home do
subdomínio já mostra o edital VIGENTE — sem necessidade de navegar a API
de notícias do site principal (`www.maua.sp.gov.br/Noticias/ListarAjax`)
pra descobrir qual é o mais recente (ver docstring de `fontes/maua.py`).

Cargo/vagas/requisitos/carga horária vêm de `gemini_pdf.extrair_vagas_de_pdf`
(a tabela do PDF sai embaralhada em extração de texto linear, precisa
leitura visual); salário (remuneração uniforme "R$ .../hora") vem de
`fontes.maua.extrair_salario_por_hora_uniforme` (regex determinístico
contra o texto puro do PDF), não do Gemini — o prompt compartilhado de
`gemini_pdf` deixa `salario` null de propósito pra remuneração por hora
(ver docstring do módulo pro motivo completo).

Uso: python scripts/rodar_maua.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pdfplumber
import requests

from notifica_vagas_scraper import db, gemini_pdf, gemini_util, ibge
from notifica_vagas_scraper.fontes import maua

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Mauá/SP - Processo Seletivo Saúde"
ORGAO_PADRAO = "Secretaria de Saúde de Mauá"


def _extrair_texto_pdf(conteudo_pdf: bytes) -> str:
    with pdfplumber.open(BytesIO(conteudo_pdf)) as pdf:
        return "\n".join((pagina.extract_text() or "") for pagina in pdf.pages)


def _montar_resumo(edital: maua.EditalVigente, vaga: dict[str, Any]) -> str:
    resumo = f"Edital nº {edital.numero_edital} — {vaga['cargo']}"
    detalhes: list[str] = []
    if vaga.get("carga_horaria"):
        detalhes.append(vaga["carga_horaria"])
    if vaga.get("requisitos"):
        detalhes.append(vaga["requisitos"])
    # todo PSS de médicos desta fonte é formação de cadastro reserva —
    # confirmado tanto no PDF real do Edital 57/2026 quanto nas notícias
    # de ciclos anteriores (ver docstring de fontes/maua.py).
    detalhes.append("cadastro reserva")
    return resumo + f" ({'; '.join(detalhes)})"


def processar_edital(conn, fonte_id: str, codigo_ibge: int, edital: maua.EditalVigente) -> int:
    pdf_resposta = requests.get(edital.pdf_url, headers={"User-Agent": USER_AGENT}, timeout=60)
    pdf_resposta.raise_for_status()

    extraido = gemini_pdf.extrair_vagas_de_pdf(pdf_resposta.content)
    if not extraido.get("vagas"):
        print(f"  aviso: Gemini não retornou vagas pra {edital.pdf_url}")
        return 0

    texto_pdf = _extrair_texto_pdf(pdf_resposta.content)
    valor_hora = maua.extrair_salario_por_hora_uniforme(texto_pdf)

    numero_edital = extraido.get("numero_edital") or edital.numero_edital
    tipo_oportunidade = extraido.get("tipo_oportunidade") or "processo_seletivo_temporario"
    orgao = extraido.get("orgao") or ORGAO_PADRAO
    inscricoes_inicio = edital.inscricoes_inicio or gemini_util.parsear_data_iso(extraido.get("inscricoes_inicio"))
    inscricoes_fim = edital.inscricoes_fim or gemini_util.parsear_data_iso(extraido.get("inscricoes_fim"))
    data_publicacao = gemini_util.parsear_data_iso(extraido.get("data_publicacao"))

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=maua.MUNICIPIO, uf=maua.UF)

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue

        salario = vaga.get("salario")
        salario_tipo = vaga.get("salario_tipo")
        if salario is None and valor_hora is not None:
            # remuneração "R$ .../hora" não é capturada pelo prompt
            # compartilhado de gemini_pdf (só monta número pra mensal/
            # plantão fixo) — ver docstring de fontes/maua.py.
            salario = valor_hora
            salario_tipo = "plantao"

        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=maua.identificador_externo(numero_edital, cargo),
            orgao=orgao,
            cargo=cargo,
            salario=salario,
            salario_tipo=salario_tipo,
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=_montar_resumo(edital, vaga),
            url_evidencia=edital.pdf_url,
            tipo_documento="pdf",
            texto_extraido=None,
            **gemini_util.campos_estruturados_extras(extraido, vaga),
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(maua.URL_HOME, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    edital = maua.extrair_edital_vigente(resposta.text)
    if edital is None:
        print("Nenhum edital de PSS vigente encontrado na home do processo seletivo.")
        return

    print(f"Edital vigente: nº {edital.numero_edital} ({edital.pdf_url})")

    conn = db.conectar()
    try:
        codigo_ibge = db.buscar_codigo_ibge_local(conn, maua.MUNICIPIO, maua.UF) or ibge.buscar_codigo_ibge(
            maua.MUNICIPIO, maua.UF
        )
        if codigo_ibge is None:
            print(f"aviso: município '{maua.MUNICIPIO}/{maua.UF}' não encontrado no IBGE, abortando")
            return

        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=maua.URL_HOME, tipo="oficial", uf=maua.UF)
        conn.commit()

        total = 0
        try:
            # savepoint: erro processando o edital não deixa a transação
            # inteira em estado abortado (mesmo padrão de outros
            # rodar_*.py, mesmo com só 1 edital por execução aqui).
            with conn.transaction():
                total = processar_edital(conn, fonte_id, codigo_ibge, edital)
            conn.commit()
        except Exception as exc:  # nunca deixar erro no PDF derrubar sem registro
            print(f"  ERRO processando edital nº {edital.numero_edital}: {exc}")

        print(f"\nOk. {total} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_maua.py"):
        main()
