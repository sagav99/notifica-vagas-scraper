#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Avança SP: descobre processos com
inscrição aberta em `/index/abertos/` (sem lista curada de município,
igual IMESO/IMAM/JCM/ACCESS) e extrai cargo/escolaridade/salário/carga
horária/quantidade **direto da tabela "Vagas" em HTML** da página de
detalhe (`/informacoes/<id>/`) — diferente da JCM/ACCESS (mesma
plataforma ProSeleta, mas lá o salário só existe no PDF do edital e
precisa do Gemini). Aqui o Gemini NÃO é necessário pro caso comum; fica
reservado pra auditoria/verificação (padrão já usado no projeto), não
para a coleta em si.

Salário por hora (comum em cargo médico) não é gravado como se fosse
mensal — `vagas.salario` fica `None` nesse caso, mas o texto original vai
no resumo. Ver `fontes/avancasp.py` pra detalhe completo do parsing e das
diferenças em relação à JCM/ACCESS.

Uso: python scripts/rodar_avancasp.py
Requer DATABASE_URL no ambiente (não precisa de GEMINI_API_KEY pro caso
comum).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import avancasp

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "Avança SP"


def _montar_resumo(item: avancasp.ItemListagem, vaga: avancasp.VagaAvancaSp) -> str:
    resumo = f"{item.tipo_processo} nº {item.numero_edital} — {vaga.cargo}"
    detalhes: list[str] = []
    if vaga.escolaridade:
        detalhes.append(vaga.escolaridade)
    if vaga.carga_horaria:
        detalhes.append(vaga.carga_horaria)
    if vaga.salario is None and vaga.salario_texto:
        # remuneração por hora/aula — não convertida pra mensal (ver
        # fontes/avancasp.py), mas o texto original não pode se perder.
        detalhes.append(vaga.salario_texto)
    if vaga.cadastro_reserva:
        detalhes.append("cadastro de reserva")
    if detalhes:
        resumo += f" ({', '.join(detalhes)})"
    return resumo


def processar_processo(conn, fonte_id: str, item: avancasp.ItemListagem) -> int:
    codigo_ibge = ibge.buscar_codigo_ibge(item.municipio, item.uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{item.municipio}/{item.uf}' não encontrado no IBGE, pulando")
        return 0

    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    vagas_extraidas = avancasp.listar_vagas_html(resposta.text)
    if not vagas_extraidas:
        print(f"  aviso: nenhuma vaga extraída de {item.url} (tabela ausente ou formato inesperado)")
        return 0

    # `data_publicacao` vem da mesma lógica de escolher o edital mais
    # recente da JCM/ACCESS (proseleta.escolher_edital) — usada só como
    # metadado, não pra extrair cargo/salário (isso já veio da tabela
    # HTML acima, sem precisar abrir o PDF).
    documentos = avancasp.listar_documentos(resposta.text)
    edital = avancasp.escolher_edital(documentos)
    data_publicacao = edital.data if edital else None

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=item.municipio, uf=item.uf)
    orgao = f"{item.orgao} de {item.municipio}/{item.uf}"

    total = 0
    for vaga in vagas_extraidas:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=avancasp.identificador_externo(item.processo_id, vaga),
            orgao=orgao,
            cargo=vaga.cargo,
            salario=vaga.salario,
            numero_edital=item.numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=None,
            inscricoes_fim=None,
            status="aberta",
            resumo=_montar_resumo(item, vaga),
            url_evidencia=item.url,
            tipo_documento="pagina_html",
            texto_extraido=None,
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"    {vaga.cargo}: vaga_id={resultado['vaga_id']} ({novo})")
        total += 1

    return total


def main() -> None:
    resposta = requests.get(f"{avancasp.BASE_URL}/index/abertos/", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    itens = avancasp.listar_processos_abertos(resposta.text)

    print(
        f"{len(itens)} processo(s) com inscrição aberta em {avancasp.UF} "
        "(Avança SP atua só nesse estado na amostra investigada, ver fontes/avancasp.py)."
    )

    conn = db.conectar()
    try:
        fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=avancasp.BASE_URL, tipo="oficial", uf=avancasp.UF)
        conn.commit()

        total_geral = 0
        for item in itens:
            print(f"Processando {item.municipio}/{item.uf} — {item.tipo_processo} {item.numero_edital}...")
            try:
                with conn.transaction():
                    total_geral += processar_processo(conn, fonte_id, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 processo derrubar o lote inteiro
                print(f"  ERRO processando '{item.tipo_processo} {item.numero_edital}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_avancasp.py"):
        main()
