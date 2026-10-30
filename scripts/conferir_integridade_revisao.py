#!/usr/bin/env python3
"""Conferência de integridade da revisão (sem Gemini, só leitura no banco).

Pedido do usuário (2026-10-14): vaga real nunca pode se perder por falha técnica
(cota, rede) e o sistema tem que conferir isso sozinho. Roda ao fim de
`revisar-vagas.yml` (`if: always()`); sai com código 1 se achar problema, o que
deixa o workflow vermelho e o GitHub avisa por e-mail.

Checagens (só vaga médica ainda aberta ou sem prazo):
  1. rejeitada por ERRO TÉCNICO ("[revisão automática] Erro...", "Teto de folga",
     "rejeitada por padrão"), isto é, sem decisão real do Gemini;
  2. pendente há mais de 3 dias (revisão travada);
  3. órgão aponta para um município diferente do cadastrado (ex.: Prefeitura de
     São Joaquim da Barra gravada em Franca e em Ribeirão Preto).
Uso: python scripts/conferir_integridade_revisao.py
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from notifica_vagas_scraper import db


def _norm(texto: str | None) -> str:
    sem_acento = unicodedata.normalize("NFKD", (texto or "").lower()).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", sem_acento).split())


def municipio_do_orgao(orgao: str | None) -> str | None:
    """'Prefeitura de São Joaquim da Barra' -> 'sao joaquim da barra'; None se o órgão
    não for do tipo 'Prefeitura/Município de X' (hospital, fundação etc. não entram)."""
    if not orgao or not re.match(r"^\s*(prefeitura|munic[ií]pio)", orgao, re.I):
        return None
    resto = re.sub(r"^\s*(prefeitura|munic[ií]pio)( municipal)?( de| do| da)?\s*", "", orgao, flags=re.I)
    resto = _norm(re.split(r"\s+-\s+|/|\bsecretaria\b|\bfundo\b|\bhospital\b", resto, flags=re.I)[0])
    return resto or None


def main() -> int:
    conn = db.conectar()
    problemas = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select left(v.id::text, 8), m.nome, m.uf, v.cargo, v.inscricoes_fim, left(v.revisao_motivo, 90)
                from vagas v join municipios m on m.codigo_ibge = v.municipio_id
                where v.categoria_saude = 'medico' and v.revisao_status = 'rejeitada'
                  and (v.inscricoes_fim is null or v.inscricoes_fim >= current_date)
                  and (v.revisao_motivo ilike '%[revisão automática] Erro%'
                       or v.revisao_motivo ilike '%Teto de folga%'
                       or v.revisao_motivo ilike '%rejeitada por padrão%')
                order by v.inscricoes_fim nulls last
                """
            )
            erro_tecnico = cur.fetchall()
            cur.execute(
                """
                select left(v.id::text, 8), m.nome, m.uf, v.cargo, v.detectada_em::date
                from vagas v join municipios m on m.codigo_ibge = v.municipio_id
                where v.categoria_saude = 'medico' and v.revisao_status = 'pendente'
                  and v.detectada_em < now() - interval '3 days'
                order by v.detectada_em
                """
            )
            travadas = cur.fetchall()
            cur.execute(
                """
                select left(v.id::text, 8), m.nome, m.uf, v.orgao, v.cargo, v.revisao_status
                from vagas v join municipios m on m.codigo_ibge = v.municipio_id
                where v.categoria_saude = 'medico' and v.inscricoes_fim >= current_date
                  and v.revisao_status in ('aprovada', 'pendente', 'rejeitada')
                """
            )
            municipio_errado = []
            for vid, nome, uf, orgao, cargo, status in cur.fetchall():
                alvo = municipio_do_orgao(orgao)
                mun = _norm(nome)
                if alvo and alvo not in mun and mun not in alvo:
                    municipio_errado.append((vid, f"{nome}/{uf}", orgao, cargo, status))
    finally:
        conn.close()

    for titulo, linhas in (
        ("REJEITADA POR ERRO TÉCNICO (sem decisão real do Gemini) — voltar para 'pendente'", erro_tecnico),
        ("PENDENTE HÁ MAIS DE 3 DIAS (revisão travada)", travadas),
        ("ÓRGÃO ≠ MUNICÍPIO CADASTRADO (município provavelmente errado)", municipio_errado),
    ):
        if linhas:
            problemas += len(linhas)
            print(f"\n!! {titulo}: {len(linhas)}")
            for linha in linhas[:25]:
                print("   ", linha)
    if problemas:
        print(f"\n{problemas} problema(s) de integridade na revisão.")
        return 1
    print("Integridade da revisão OK: nenhuma rejeição técnica, pendência travada ou município divergente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
