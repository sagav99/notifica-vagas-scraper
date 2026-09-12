"""Auditoria de completude de vaga médica já aprovada — motivação
(usuário, 2026-09-12): vaga aparece no Med Vagas com informação
incompleta (falta número de vagas, banca, se tem prova, taxa de
inscrição etc.), obrigando quem usa o site a abrir o edital original pra
descobrir o básico — e às vezes o link salvo nem abre a página certa.

Rodado por `scripts/auditar_completude_vagas.py`: relê o mesmo link já
salvo como evidência (mesma extração via Gemini já usada na coleta —
`gemini_pdf`/`gemini_texto`) e preenche só o que a vaga tinha `null`,
nunca sobrescrevendo dado já existente. Se o link estiver quebrado,
tenta achar um substituto via busca (Serper, mesmo cliente da
descoberta ampla) — sem NUNCA trocar/apagar a evidência original, só
soma uma evidência extra (`db.registrar_evidencia_adicional`).

Camada de auditoria/reparo sobre o que o scraper já coletou, não
substitui a coleta em si (mesmo princípio de CLAUDE.md do repo
principal pra uso do Gemini)."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import requests

from . import db, gemini_pdf, gemini_texto
from .fontes import google_search
from .revisao_ia import CotaGeminiEsgotadaError

USER_AGENT = "Mozilla/5.0 (compatible; MedVagasAuditoriaCompletude/1.0)"
TIMEOUT_HTTP_S = 20

#: Saldo de crédito da Serper é único e NÃO renova (ver
#: `fontes/google_search.py`) — a descoberta ampla (Vigia) já orça 20
#: consultas/dia pra esticar esse saldo por meses. Esta rotina só recorre
#: à Serper quando o link salvo está genuinamente quebrado (fallback, não
#: uso normal), por isso um teto bem mais conservador.
LIMITE_SERPER_POR_EXECUCAO = 5


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto)
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


@dataclass
class ResultadoLink:
    acessivel: bool
    motivo: str


def checar_link(url: str) -> ResultadoLink:
    """`HEAD` primeiro (mais barato); cai pra `GET` se o site não
    implementar `HEAD` direito (alguns retornam 403/405 só em HEAD).
    Considera acessível só status < 400 — não tenta detectar redirect pra
    home genérica (heurística frágil por site, ver TAREFAS.md pra
    revisitar se virar problema real na prática)."""
    try:
        resposta = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_HTTP_S, allow_redirects=True)
        if resposta.status_code >= 400:
            resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_HTTP_S, allow_redirects=True)
        if resposta.status_code >= 400:
            return ResultadoLink(False, f"HTTP {resposta.status_code}")
        return ResultadoLink(True, "ok")
    except requests.RequestException as exc:
        return ResultadoLink(False, f"erro de conexão: {type(exc).__name__}: {exc}")


def _tratar_erro_gemini(exc: Exception) -> None:
    """Mesmo critério de `revisao_ia.decidir_revisao`: HTTP 429 é cota
    esgotada, não erro desta vaga específica — propaga pra quem chama
    parar o lote em vez de continuar gastando chamada que vai falhar
    igual em toda vaga seguinte."""
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None and exc.response.status_code == 429:
        raise CotaGeminiEsgotadaError(str(exc)) from exc


def extrair_dados_do_link(url: str, tipo_documento: str) -> dict[str, Any] | None:
    """Rebaixa o link já salvo como evidência e roda a extração via
    Gemini de novo (mesmo módulo usado na coleta original) — devolve o
    dict bruto (campos de edital + lista `vagas`) ou `None` em falha de
    rede/parsing (não propaga, só cota esgotada propaga)."""
    try:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_HTTP_S)
        resposta.raise_for_status()
    except requests.RequestException:
        return None

    try:
        if tipo_documento == "pdf":
            return gemini_pdf.extrair_vagas_de_pdf(resposta.content)
        return gemini_texto.extrair_vagas_de_texto(titulo=url, texto=resposta.text)
    except requests.exceptions.HTTPError as exc:
        _tratar_erro_gemini(exc)
        return None
    except Exception:
        return None


def encontrar_dados_cargo(extraido: dict[str, Any], cargo: str) -> dict[str, Any] | None:
    """Acha, dentro de `extraido["vagas"]`, a entrada cujo `cargo` bate
    (comparação normalizada, exata) com o cargo da vaga que está sendo
    auditada — `None` se o edital reprocessado não listar mais esse cargo
    com o mesmo texto (reformatação, erro de digitação novo etc.): nesse
    caso só os campos de nível de edital (taxa/data_prova/banca/...)
    ainda são aproveitáveis."""
    alvo = _normalizar(cargo)
    for vaga in extraido.get("vagas", []):
        nome = vaga.get("cargo")
        if isinstance(nome, str) and _normalizar(nome) == alvo:
            return vaga
    return None


#: Campos de nível de EDITAL (mesmos pra todo cargo do mesmo processo).
_CAMPOS_EDITAL = {
    "taxa_inscricao": "taxa_inscricao",
    "data_prova": "data_prova",
    "inscricoes_inicio": "inscricoes_inicio",
    "inscricoes_fim": "inscricoes_fim",
    "banca_organizadora": "banca_organizadora",
    "tem_prova": "tem_prova",
    "exige_curriculo": "exige_curriculo",
}

#: Campos de nível de CARGO (dentro de `extraido["vagas"][i]`).
_CAMPOS_CARGO = {
    "numero_vagas": "vagas_qtd",
    "salario": "salario",
    "salario_tipo": "salario_tipo",
    "requisitos": "requisitos",
    "carga_horaria": "carga_horaria",
}


def montar_campos_a_atualizar(
    vaga_atual: dict[str, Any], extraido: dict[str, Any] | None, dados_cargo: dict[str, Any] | None
) -> dict[str, Any]:
    """Só inclui campo que (a) está `null` na vaga atual E (b) o
    reprocessamento achou um valor não-null pra ele — nunca sobrescreve
    campo que já tinha valor (checagem `vaga_atual.get(campo) is None` é
    a garantia disso)."""
    campos: dict[str, Any] = {}
    if extraido:
        for campo_vaga, campo_extraido in _CAMPOS_EDITAL.items():
            valor = extraido.get(campo_extraido)
            if valor is not None and vaga_atual.get(campo_vaga) is None:
                campos[campo_vaga] = valor
    if dados_cargo:
        for campo_vaga, campo_extraido in _CAMPOS_CARGO.items():
            valor = dados_cargo.get(campo_extraido)
            if valor is not None and vaga_atual.get(campo_vaga) is None:
                campos[campo_vaga] = valor
    return campos


def buscar_link_alternativo(
    *, cargo: str, orgao: str | None, municipio: str, uf: str, api_key: str | None = None
) -> "google_search.ItemBusca | None":
    """Usa a Serper (mesmo cliente da descoberta ampla) pra tentar achar
    um link atual do mesmo edital quando o salvo está quebrado. `None` se
    a chave não estiver configurada, a busca falhar, ou não achar nada —
    nunca levanta, essa busca é best-effort."""
    chave = api_key or os.environ.get("SERPER_API_KEY")
    if not chave:
        return None
    partes = [p for p in [cargo, orgao, municipio, uf, "edital concurso"] if p]
    query = " ".join(partes)
    parametros = google_search.montar_parametros(query, backfill=False)
    try:
        resposta = requests.post(
            google_search.BASE_URL,
            json=parametros,
            headers={"User-Agent": USER_AGENT, "X-API-KEY": chave, "Content-Type": "application/json"},
            timeout=TIMEOUT_HTTP_S,
        )
        resposta.raise_for_status()
    except requests.RequestException:
        return None
    itens = google_search.listar_itens(resposta.json())
    return itens[0] if itens else None
