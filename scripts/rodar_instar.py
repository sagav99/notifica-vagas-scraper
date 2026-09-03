#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Instar Tecnologia: para cada município com
essa plataforma confirmada (`notifica_vagas_scraper.fontes.instar`), busca
o endpoint de dados abertos de concursos do ano corrente, filtra os itens
"Aberto" e usa Gemini pra extrair vaga(s) de cada `descricao` (texto/HTML
livre, sem estrutura fixa confiável — diferente de dom_amm_mg/IMESO).

Uso: python scripts/rodar_instar.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_texto
from notifica_vagas_scraper.fontes import instar

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_TIPO = "oficial"
DESCRICAO_MAX_CHARS = 8000

#: prioridade do produto é saúde/médicos (ver TAREFAS.md, decisão do
#: usuário 2026-09-01) — achado real na mesma data: a cota diária do
#: Gemini estourou no meio do lote (ordem alfabética simples) e municípios
#: com vaga de médico real de verdade (Carmo do Rio Claro, Guaraciama,
#: Varginha) ficaram de fora até o próximo ciclo. Processar município com
#: sinal de saúde primeiro garante que, se a cota estourar nesta execução,
#: o prejuízo caia nos itens de menor prioridade, não nos de médico.
PALAVRAS_SAUDE = (
    "medic",
    "médic",
    "enfermeir",
    "saude",
    "saúde",
    "odont",
    "fisioterap",
    "psicolog",
    "nutricion",
    "farmaceut",
    "farmac",
    "fonoaudiolog",
)


def _tem_sinal_saude(itens: list[dict]) -> bool:
    for item in itens:
        texto = ((item.get("titulo") or "") + " " + (item.get("descricao") or "")).lower()
        if any(palavra in texto for palavra in PALAVRAS_SAUDE):
            return True
    return False


def _parsear_data_iso(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def buscar_json_concursos(url_prefeitura: str, ano: int) -> dict | None:
    url = instar.url_dados_abertos(url_prefeitura, ano)
    try:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, verify=True)
    except requests.exceptions.SSLError:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, verify=False)
    except requests.exceptions.RequestException as exc:
        print(f"  aviso: erro de rede em {url}: {exc}")
        return None

    if resposta.status_code != 200:
        print(f"  aviso: HTTP {resposta.status_code} em {url}")
        return None
    try:
        return resposta.json()
    except ValueError:
        print(f"  aviso: resposta não é JSON em {url}")
        return None


def processar_municipio(conn, municipio: instar.MunicipioInstar, itens: list[dict]) -> int:
    codigo_ibge = municipio.codigo_ibge
    fonte_id = db.upsert_fonte(
        conn,
        nome=f"Portal {municipio.nome}/{municipio.uf} (Instar)",
        url=municipio.url_prefeitura,
        tipo=FONTE_TIPO,
        uf=municipio.uf,
    )
    url_evidencia = municipio.url_prefeitura.rstrip("/") + "/portal/editais"

    total = 0
    for item in itens:
        titulo = item.get("titulo") or ""
        descricao = (item.get("descricao") or "")[:DESCRICAO_MAX_CHARS]
        if not descricao:
            continue

        try:
            extraido = gemini_texto.extrair_vagas_de_texto(titulo, descricao)
        except gemini_texto.ErroExtracaoGemini as exc:
            print(f"  aviso: falha ao extrair '{titulo[:60]}': {exc}")
            continue

        if not extraido.get("vagas"):
            continue

        data_publicacao = _parsear_data_iso(extraido.get("data_publicacao"))
        inscricoes_inicio = _parsear_data_iso(extraido.get("inscricoes_inicio"))
        inscricoes_fim = _parsear_data_iso(extraido.get("inscricoes_fim"))
        # Quando o resumo publicado não nomeia a secretaria/órgão específico,
        # cai pro nome da prefeitura do município — não é dado inventado (o
        # item já vem do portal oficial daquele município), e evita rejeitar
        # vaga real só por causa de um campo que o Gemini não conseguiu
        # extrair do texto truncado (achado analisando vagas rejeitadas em
        # produção, 2026-09-01: maioria das rejeições da fonte Instar era
        # só isso).
        orgao = extraido.get("orgao") or f"Prefeitura Municipal de {municipio.nome}/{municipio.uf}"
        numero_edital = extraido.get("numero_edital") or item.get("numeroEdital")
        numero_processo = item.get("numeroProcesso")
        tipo_oportunidade = extraido.get("tipo_oportunidade")

        for vaga in extraido["vagas"]:
            cargo = vaga.get("cargo")
            if not cargo:
                continue
            identificador_externo = f"{numero_processo}-{_slug(titulo)}-{_slug(cargo)}"
            resultado = db.inserir_vaga_com_evidencia(
                conn,
                fonte_id=fonte_id,
                municipio_id=codigo_ibge,
                identificador_externo=identificador_externo,
                orgao=orgao,
                cargo=cargo,
                salario=Decimal(str(vaga["salario"])) if vaga.get("salario") is not None else None,
                salario_tipo=vaga.get("salario_tipo"),
                tipo_oportunidade=tipo_oportunidade,
                numero_edital=numero_edital,
                data_publicacao=data_publicacao,
                inscricoes_inicio=inscricoes_inicio,
                inscricoes_fim=inscricoes_fim,
                status="aberta",
                resumo=f"{item.get('modalidade') or 'Processo seletivo'} — {titulo}",
                url_evidencia=url_evidencia,
                tipo_documento="pagina_html",
                texto_extraido=None,
            )
            novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
            print(f"  {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
            total += 1

    return total


def main() -> None:
    conn = db.conectar()
    try:
        municipios = instar.listar_municipios_instar()
        print(f"{len(municipios)} município(s) com plataforma Instar confirmada.")

        # 1ª passada: só busca o JSON público (sem chamar Gemini) pra poder
        # priorizar quem tem sinal de saúde antes de gastar cota — ver
        # PALAVRAS_SAUDE acima. try/except por município aqui também
        # (achado de code review, 2026-09-02): payload malformado de 1
        # entre 231 municípios (ex: JSON que não é um dict) não pode
        # derrubar a passada inteira antes de qualquer Gemini rodar — isso
        # anularia justo a proteção que essa priorização existe pra dar.
        trabalho: list[tuple[instar.MunicipioInstar, list[dict]]] = []
        for municipio in municipios:
            try:
                payload = buscar_json_concursos(municipio.url_prefeitura, datetime.now().year)
                if payload is None:
                    continue
                itens = instar.listar_itens_abertos(payload)
            except Exception as exc:
                print(f"  aviso: falha buscando {municipio.nome}/{municipio.uf}: {exc}")
                continue
            if itens:
                trabalho.append((municipio, itens))

        trabalho.sort(key=lambda par: not _tem_sinal_saude(par[1]))
        com_sinal_saude = sum(1 for _, itens in trabalho if _tem_sinal_saude(itens))
        print(
            f"{len(trabalho)} município(s) com processo aberto "
            f"({com_sinal_saude} com sinal de saúde, processados primeiro)."
        )

        total_geral = 0
        for municipio, itens in trabalho:
            print(f"Processando {municipio.nome}/{municipio.uf}...")
            try:
                # savepoint por município: erro num município não deixa a
                # transação inteira do lote em estado abortado pros próximos.
                with conn.transaction():
                    total_geral += processar_municipio(conn, municipio, itens)
            except Exception as exc:  # nunca deixar 1 município derrubar o lote inteiro
                print(f"  ERRO processando {municipio.nome}/{municipio.uf}: {exc}")

        conn.commit()
        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_instar.py"):
        main()
