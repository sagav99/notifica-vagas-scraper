"""Utilitário compartilhado por gemini_pdf.py, gemini_texto.py e
revisao_ia.py para extrair o objeto JSON de uma resposta em texto do
Gemini, e pelo rate limiter comum às chamadas de API dos 3 módulos.

Achado analisando vagas rejeitadas em produção (2026-09-01): algumas
respostas do Gemini vinham com barra invertida solta dentro de um valor
de string (ex: unidade de medida ou trecho copiado do texto original com
"\\" que não é escape JSON válido) — `json.loads` explode com "Invalid
\\uXXXX escape" e a chamada inteira virava erro tratado como "rejeitada
por padrão", descartando uma vaga real por causa de um problema de
escaping trivial, não de conteúdo genuinamente ruim.

Achado real 2026-09-09 (verificação do Vigia/Serper em produção,
`scripts/rodar_descoberta_google_search.py`): até esta data, cada um dos
3 módulos mantinha seu próprio `_ultima_chamada`/`_esperar_rate_limit`
independente. Isso é inofensivo quando só um módulo é usado por processo
(caso normal do pipeline principal), mas `rodar_descoberta_google_search.py`
chama `gemini_pdf` OU `gemini_texto` conforme cada item tem PDF ou não,
dentro do mesmo processo — como os 2 relógios não se falam, itens
alternados furavam o limite de 15 RPM (gaps reais de <1s entre chamadas
no log de produção, mesmo com os 4.5s respeitados dentro de cada módulo
isoladamente), causando 429 em cascata. Rate limiter centralizado aqui
resolve isso pra qualquer combinação de módulos chamados no mesmo processo.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any

_CERCA_MARKDOWN = re.compile(r"^```(?:json)?\s*|\s*```$")
_ESCAPE_INVALIDO = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')

#: só casa carga horária semanal simples ("40h", "20 horas", "40h
#: semanais") — rejeita de propósito escala tipo "12x36"/"24x72" e texto
#: composto ("20h, com plantões aos sábados"), onde converter pra
#: valor-hora exigiria adivinhar a jornada real (ver `calcular_valor_hora`).
_RE_CARGA_HORARIA_SEMANAL = re.compile(
    r"^\s*(\d{1,3})\s*h(?:oras)?(?:\s*semanais?)?\s*$", re.IGNORECASE
)

INTERVALO_MINIMO_ENTRE_CHAMADAS_S = 4.5  # 15 RPM = 1 a cada 4s; margem de segurança

_ultima_chamada: float = 0.0


def esperar_rate_limit() -> None:
    """Garante >=4.5s desde a última chamada real ao Gemini feita por
    QUALQUER um dos 3 módulos (gemini_pdf, gemini_texto, revisao_ia) no
    mesmo processo — estado compartilhado de propósito, ver docstring
    do módulo."""
    global _ultima_chamada
    agora = time.monotonic()
    espera = INTERVALO_MINIMO_ENTRE_CHAMADAS_S - (agora - _ultima_chamada)
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada = time.monotonic()


def parsear_json_resposta(texto: str) -> dict:
    """Remove cerca de markdown (```json ... ```), se presente, escapa
    qualquer backslash que não inicie um escape JSON válido e faz o
    parse. Levanta json.JSONDecodeError se o resultado ainda assim não
    for JSON válido (deixa o chamador decidir o que fazer)."""
    limpo = _CERCA_MARKDOWN.sub("", texto.strip())
    sanitizado = _ESCAPE_INVALIDO.sub(r"\\\\", limpo)
    return json.loads(sanitizado)


def parsear_data_iso(texto: str | None) -> date | None:
    """`"AAAA-MM-DD"` -> `date`, `None` se ausente ou mal formado — mesmo
    parse já duplicado em vários `rodar_*.py` (`_parsear_data_iso` local),
    reaproveitado aqui só pelo campo novo `data_prova` pra não criar mais
    uma cópia; os outros usos existentes não foram tocados (fora de
    escopo desta mudança)."""
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def calcular_valor_hora(
    salario: float | None, salario_tipo: str | None, carga_horaria: str | None
) -> float | None:
    """Valor-hora só quando dá pra calcular sem ambiguidade: salário
    mensal fixo + carga horária semanal simples (`_RE_CARGA_HORARIA_SEMANAL`).
    `None` em qualquer outro caso (plantão, carga composta/escala, dado
    ausente) — nunca inventa (mesma regra do prompt do Gemini, ver
    `docs/analise_produto_2026-09-10.md` seção A)."""
    if salario is None or salario_tipo != "mensal" or not carga_horaria:
        return None
    match = _RE_CARGA_HORARIA_SEMANAL.match(carga_horaria)
    if not match:
        return None
    horas_semanais = int(match.group(1))
    if horas_semanais <= 0:
        return None
    horas_mensais = horas_semanais * 52 / 12
    return round(float(salario) / horas_mensais, 2)


def campos_estruturados_extras(extraido: dict[str, Any], vaga: dict[str, Any]) -> dict[str, Any]:
    """Monta os kwargs novos de `db.inserir_vaga_com_evidencia` (migration
    018) a partir do dict `extraido` (nível de edital, de
    `gemini_pdf.extrair_vagas_de_pdf`/`gemini_texto.extrair_vagas_de_texto`)
    e do dict `vaga` (nível de cargo, dentro de `extraido["vagas"]`) —
    reaproveitado por todo chamador desses dois módulos, pra não duplicar
    o mapeamento de campo em cada `rodar_*.py`. `valor_hora` é calculado
    aqui, não pedido ao Gemini (ver `calcular_valor_hora`).

    `banca_organizadora`/`tem_prova`/`exige_curriculo` (migration 028,
    2026-09-12) vêm do nível de edital (`extraido`), igual
    `taxa_inscricao`/`data_prova` — mesmo processo seletivo, mesma banca e
    mesma forma de seleção pra todos os cargos dele."""
    carga_horaria = vaga.get("carga_horaria")
    salario = vaga.get("salario")
    salario_tipo = vaga.get("salario_tipo")
    return {
        "numero_vagas": vaga.get("vagas_qtd"),
        "taxa_inscricao": extraido.get("taxa_inscricao"),
        "carga_horaria": carga_horaria,
        "valor_hora": calcular_valor_hora(salario, salario_tipo, carga_horaria),
        "data_prova": parsear_data_iso(extraido.get("data_prova")),
        "requisitos": vaga.get("requisitos"),
        "banca_organizadora": extraido.get("banca_organizadora"),
        "tem_prova": extraido.get("tem_prova"),
        "exige_curriculo": extraido.get("exige_curriculo"),
    }
