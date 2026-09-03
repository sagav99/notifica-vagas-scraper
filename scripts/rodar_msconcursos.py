#!/usr/bin/env python3
"""Entrypoint do cron pra fonte MSConcursos: lê a homepage
(`msconcursos.com.br/`), que lista TODOS os concursos numa página só sem
paginação, filtra só a seção "INSCRIÇÕES ABERTAS" e extrai cargo/salário/
quantidade de vagas/carga horária **direto da tabela estruturada em HTML**
da página de cada concurso — igual à Avança SP (`rodar_avancasp.py`), o
Gemini NÃO é necessário pro caso comum (fica reservado pra auditoria,
padrão já usado no projeto).

MSConcursos é banca NACIONAL (clientes históricos em MG e SP confirmados
na investigação, mas também PR/RJ/BA/SC na mesma amostra) — diferente da
Avança SP (UF fixa), aqui `fontes/msconcursos.py` extrai a UF
dinamicamente do título de cada concurso; o filtro MG/SP do projeto (ver
CLAUDE.md) é aplicado aqui no script, mesmo padrão de
`rodar_access.py`/`rodar_jcm.py`.

Concurso sem "PREFEITURA (MUNICIPAL )?DE <município>, UF" reconhecível no
título (ex: consórcio intermunicipal atendendo vários municípios ao mesmo
tempo, corpo de bombeiros estadual) é pulado — sem risco de gravar
município errado, só de perder cobertura até reinvestigar (ver docstring
de `fontes/msconcursos.py`).

Cargo com "Quantidade de vagas: 0" no HTML **ainda é gravado** — mesmo
precedente de `rodar_ibgp.py` (0/None não é motivo pra descartar um
cargo, só muda o texto do resumo); filtrar por quantidade escondendo o
cargo inteiro do banco é risco maior (perder de vista um cargo médico que
passa a ter vaga numa retificação futura) do que gravar uma vaga com 0
posição no momento.

Remuneração já vem em valor mensal fixo no HTML da amostra investigada
(nenhum cargo com sufixo "por hora"/"por plantão", inclusive nos
plantonistas — valores na faixa de R$ 13-15 mil/mês) — por isso
`salario_tipo="mensal"` é aplicado sempre que `vaga.salario` não é `None`,
mesmo padrão de `rodar_ibgp.py`.

Uso: python scripts/rodar_msconcursos.py
Requer DATABASE_URL no ambiente (não precisa de GEMINI_API_KEY pro caso
comum).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, ibge
from notifica_vagas_scraper.fontes import msconcursos

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_NOME = "MSConcursos"

#: banca atua em vários estados (MG/SP confirmados na amostra, também
#: PR/RJ/BA/SC vistos na mesma homepage) — o projeto cobre só MG/SP (ver
#: CLAUDE.md), filtra aqui, não em `fontes/msconcursos.py` (genérico pra
#: qualquer UF), mesmo padrão de `rodar_access.py`/`rodar_jcm.py`.
UFS_DO_PROJETO = {"MG", "SP"}

#: remuneração da amostra investigada vem sempre em valor mensal fixo
#: (nenhum cargo com "por hora"/"por plantão", ver docstring do módulo) —
#: mesmo precedente de `rodar_ibgp.py`.
SALARIO_TIPO_PADRAO = "mensal"


def _montar_resumo(item: msconcursos.ItemListagem, vaga: msconcursos.VagaMSConcursos) -> str:
    resumo = f"{item.tipo_processo} nº {item.numero_edital or '?'} — {vaga.cargo}"
    detalhes: list[str] = []
    if vaga.escolaridade:
        detalhes.append(vaga.escolaridade)
    if vaga.carga_horaria:
        detalhes.append(vaga.carga_horaria)
    if vaga.quantidade is not None:
        detalhes.append(f"{vaga.quantidade} vaga(s) no momento" if vaga.quantidade else "0 vaga(s) no momento")
    if vaga.salario is None and vaga.salario_texto:
        # remuneração por hora/plantão — não convertida pra mensal (ver
        # fontes/msconcursos.py), mas o texto original não pode se perder.
        detalhes.append(vaga.salario_texto)
    if detalhes:
        resumo += f" ({', '.join(detalhes)})"
    return resumo


def processar_concurso(conn, item: msconcursos.ItemListagem) -> int:
    codigo_ibge = ibge.buscar_codigo_ibge(item.municipio, item.uf)
    if codigo_ibge is None:
        print(f"  aviso: município '{item.municipio}/{item.uf}' não encontrado no IBGE, pulando")
        return 0

    # banca nacional (MG e SP confirmados na amostra, ver docstring do
    # módulo) — `upsert_fonte` só casa por nome+url, então chamar por item
    # (com o uf de cada um) é seguro: só grava o `uf` na 1ª vez, mesmo
    # padrão de `rodar_access.py`.
    fonte_id = db.upsert_fonte(conn, nome=FONTE_NOME, url=msconcursos.BASE_URL, tipo="oficial", uf=item.uf)

    resposta = requests.get(item.url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()

    vagas_extraidas = msconcursos.listar_vagas_html(resposta.text)
    if not vagas_extraidas:
        print(f"  aviso: nenhum cargo extraído de {item.url} (seção de cargos ausente ou formato inesperado)")
        return 0

    db.upsert_municipio(conn, codigo_ibge=codigo_ibge, nome=item.municipio, uf=item.uf)
    orgao = f"Prefeitura de {item.municipio}/{item.uf}"

    total = 0
    for vaga in vagas_extraidas:
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=codigo_ibge,
            identificador_externo=msconcursos.identificador_externo(item.concurso_id, vaga),
            orgao=orgao,
            cargo=vaga.cargo,
            salario=vaga.salario,
            salario_tipo=SALARIO_TIPO_PADRAO if vaga.salario is not None else None,
            tipo_oportunidade=None,
            numero_edital=item.numero_edital,
            # não abrimos o PDF do edital pro caso comum (cargo/salário já
            # vêm estruturados em HTML, ver fontes/msconcursos.py) — usa a
            # data de início das inscrições como proxy de data_publicacao,
            # que bateu com a data real do "EDITAL DE ABERTURA" na amostra
            # investigada (19/08/2026 nos dois lugares).
            data_publicacao=item.inscricoes_inicio,
            inscricoes_inicio=item.inscricoes_inicio,
            inscricoes_fim=item.inscricoes_fim,
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
    resposta = requests.get(f"{msconcursos.BASE_URL}/", headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    todos = msconcursos.listar_concursos_abertos(resposta.text)
    itens = [i for i in todos if i.uf in UFS_DO_PROJETO]

    print(f"{len(itens)} concurso(s) com inscrição aberta em MG/SP (de {len(todos)} no Brasil todo).")

    conn = db.conectar()
    try:
        total_geral = 0
        for item in itens:
            print(f"Processando {item.municipio}/{item.uf} — {item.tipo_processo} {item.numero_edital}...")
            try:
                with conn.transaction():
                    total_geral += processar_concurso(conn, item)
                conn.commit()
            except Exception as exc:  # nunca deixar 1 concurso derrubar o lote inteiro
                print(f"  ERRO processando '{item.tipo_processo} {item.numero_edital}': {exc}")

        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_msconcursos.py"):
        main()
