"""Revisão administrativa automática via Gemini — decide aprovar/rejeitar
uma vaga, 1 chamada por vaga, sem humano no loop (decisão do usuário,
2026-09-01 — cancelou o fluxo anterior de aprovar/rejeitar manual no
painel /admin do repo principal). Ver
docs/revisao_automatica_gemini.md no repo principal para o desenho
completo e como auditar uma decisão.

Limitação aceita nesta v1: nenhuma fonte grava `texto_extraido` hoje (ver
scripts/rodar*.py, sempre `None`) — o Gemini avalia só os campos
estruturados já extraídos (cargo/salário/edital/datas/município/resumo),
não lê o documento original de novo. Reavaliar se aprovação/rejeição
errada virar problema real na prática.

Em caso de qualquer erro ou resposta ambígua do Gemini, a decisão cai
para "rejeitada" (decisão explícita do usuário: nunca aprovar no escuro)
— mas só depois de tentar de novo em falha transitória (erro 5xx,
timeout, erro de conexão): rodando o backfill das 40 vagas reais em
produção (2026-09-01), 11 delas foram rejeitadas só por causa de
instabilidade momentânea da API (503 Service Unavailable, timeout), não
por problema real no dado — sem retry isso vira falso negativo (vaga boa
descartada por sorte de timing, não por conteúdo). Erro 4xx (chave
inválida, request malformado) não tenta de novo, não adianta.

Segundo achado do mesmo backfill: as 8 vagas da FGV (São José dos
Campos/SP) foram rejeitadas por um bug real de extração (`data_publicacao`
sempre `null`, corrigido em `gemini_pdf.py`/`rodar_fgv.py`), mas depois de
corrigir o dado o Gemini rejeitou de novo — dessa vez porque
`data_publicacao` (a retificação) é posterior ao início das inscrições
(do edital original), um padrão normal em edital retificado que o prompt
não explicava. PROMPT_TEMPLATE agora orienta explicitamente que isso não
é inconsistência sozinha.
"""

from __future__ import annotations

import json
import os
import time

import requests

from . import gemini_util, quota_gemini

MODELO_PADRAO = quota_gemini.MODELO_PADRAO
URL_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
INTERVALO_MINIMO_ENTRE_CHAMADAS_S = 4.5  # 15 RPM = 1 a cada 4s; margem de segurança
TENTATIVAS_MAX = 3
BACKOFF_INICIAL_S = 5.0

_ultima_chamada: float = 0.0


def _esperar_rate_limit() -> None:
    global _ultima_chamada
    agora = time.monotonic()
    espera = INTERVALO_MINIMO_ENTRE_CHAMADAS_S - (agora - _ultima_chamada)
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada = time.monotonic()


def _chamar_gemini(body: dict, *, chave: str, modelo: str) -> dict:
    """Chama a API do Gemini com retry em falha transitória (5xx, timeout,
    erro de conexão) — até TENTATIVAS_MAX vezes, com backoff crescente
    (BACKOFF_INICIAL_S * tentativa). Erro 4xx (chave inválida, request
    malformado) não tenta de novo — não adianta, o request não vai mudar.
    Levanta a última exceção se todas as tentativas falharem."""
    ultimo_erro: Exception | None = None
    for tentativa in range(TENTATIVAS_MAX):
        if tentativa > 0:
            time.sleep(BACKOFF_INICIAL_S * tentativa)
        _esperar_rate_limit()
        try:
            resposta = requests.post(
                URL_API.format(modelo=modelo), params={"key": chave}, json=body, timeout=60
            )
            if modelo == quota_gemini.MODELO_PADRAO:
                quota_gemini.registrar_chamada()
            resposta.raise_for_status()
            return resposta.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            ultimo_erro = exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is None or status < 500:
                raise
            ultimo_erro = exc
    assert ultimo_erro is not None
    raise ultimo_erro


PROMPT_TEMPLATE = """Você audita dados extraídos automaticamente sobre uma vaga de concurso \
público brasileiro, antes dela ficar visível para o usuário final de um site de \
notificação de vagas.

Você NÃO tem acesso ao documento original — avalie só a qualidade e \
consistência interna dos dados abaixo: campos essenciais vazios (cargo \
ausente ou genérico demais, tipo "vaga" sem nome real de cargo), valores \
implausíveis (salário absurdo para cargo público, inscrição terminando \
antes de começar), ou cargo sem nenhuma relação plausível com concurso \
público (indício de erro de extração). Não afirme ter consultado a fonte \
original — você não tem esse acesso.

Três padrões legítimos e comuns em fonte oficial brasileira que NÃO são \
inconsistência, não rejeite só por causa deles: (1) `data_publicacao` \
posterior ao início das inscrições — é normal quando o documento é uma \
retificação publicada depois que as inscrições já abriram (corrige um \
detalhe do edital original, não reabre nem invalida o prazo já em curso); \
(2) `salario` nulo quando a remuneração é por hora/aula ou outra unidade \
que não converte num valor mensal fixo sem informação adicional — nulo \
aqui é a extração correta, não uma falha; (3) `inscricoes_inicio`/\
`inscricoes_fim` nulos quando `status` já é "aberta" — a plataforma é um \
agregador que aponta pra fonte oficial (o usuário sempre vê o link e \
confirma o prazo exato lá, isso já fica explícito na tela), o resumo \
publicado pela prefeitura às vezes não inclui o cronograma completo de \
inscrição, e isso sozinho não significa que a vaga é falsa ou que o \
processo não está aberto de verdade — só rejeite por causa de datas \
ausentes se cargo, órgão e edital TAMBÉM estiverem ausentes ou vagos \
demais para identificar a oportunidade.

Rejeite de verdade quando: cargo ausente ou genérico demais pra \
identificar a vaga (ex: "vaga", "diversos"), valor implausível pro \
contexto (salário público absurdamente alto/baixo, inscrição terminando \
antes de começar), ou qualquer sinal concreto de erro de extração \
(cargo sem relação nenhuma com concurso público, texto claramente \
cortado no meio de uma frase relevante). Ausência isolada de campo \
opcional (datas de inscrição com status já aberta, órgão quando o \
município e o cargo já identificam a vaga) não é, sozinha, motivo de \
rejeição.

Dados extraídos:
{dados_json}

Responda APENAS com um objeto JSON, sem markdown, sem texto adicional:
{{"decisao": "aprovada" ou "rejeitada", "motivo": "uma frase curta em \
português explicando a decisão"}}.

Se tiver qualquer dúvida real sobre a qualidade dos dados, responda \
"rejeitada" — nunca aprove no escuro."""


class ErroRevisaoGemini(Exception):
    pass


def decidir_revisao(
    dados: dict, *, api_key: str | None = None, modelo: str | None = None
) -> dict:
    """dados: campos estruturados da vaga (ver
    scripts/revisar_vagas.py:montar_dados_para_revisao).

    Retorna {"decisao": "aprovada"|"rejeitada", "motivo": str}. Nunca
    levanta por resposta ambígua/erro de rede — nesses casos a decisão é
    "rejeitada" com o motivo explicando a falha, para nunca aprovar uma
    vaga sem uma decisão real do Gemini por trás.

    `modelo=None` (padrão) resolve dinamicamente via `quota_gemini`: ver
    docstring de `gemini_pdf.extrair_vagas_de_pdf`.
    """
    chave = api_key or os.environ.get("GEMINI_API_KEY")
    if not chave:
        return {
            "decisao": "rejeitada",
            "motivo": "[revisão automática] GEMINI_API_KEY não configurada.",
        }
    modelo = modelo or quota_gemini.proximo_modelo()

    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": PROMPT_TEMPLATE.format(
                            dados_json=json.dumps(dados, ensure_ascii=False, default=str)
                        )
                    }
                ]
            }
        ],
        "generationConfig": {"temperature": 0},
    }

    try:
        corpo = _chamar_gemini(body, chave=chave, modelo=modelo)
        texto = corpo["candidates"][0]["content"]["parts"][0]["text"]
        resultado = gemini_util.parsear_json_resposta(texto)
    except Exception as exc:
        # Captura ampla e intencional: qualquer falha (rede, HTTP, formato
        # de resposta, JSON inválido) tem que virar rejeição automática,
        # nunca uma exceção que aborta o lote inteiro de revisão.
        return {
            "decisao": "rejeitada",
            "motivo": f"[revisão automática] Erro ao consultar o Gemini, rejeitada por padrão: {exc}",
        }

    decisao = resultado.get("decisao")
    motivo = resultado.get("motivo") or "Sem motivo informado pelo Gemini."
    if decisao not in ("aprovada", "rejeitada"):
        return {
            "decisao": "rejeitada",
            "motivo": (
                "[revisão automática] Resposta do Gemini fora do formato esperado, "
                f"rejeitada por padrão: {resultado!r}"
            ),
        }

    return {"decisao": decisao, "motivo": motivo}
