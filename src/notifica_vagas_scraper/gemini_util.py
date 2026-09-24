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

**TPM por modelo, além de RPM (achado 2026-09-16)**: o limitador de RPM
sozinho garante <=15 chamadas/minuto, mas não impede estourar as 250k
TPM (tokens por minuto) do Gemini Flash-Lite quando várias chamadas
seguidas levam PDF grande (`gemini_pdf.py`, que lê o documento inteiro)
— RPM ok, TPM não. Sintoma real em produção: 2 execuções seguidas do
workflow `Coleta de vagas` (passo "Rodar coleta (Ache Concursos)", que
chama `gemini_pdf.extrair_vagas_de_pdf` uma vez por achado) travaram em
`503 Service Unavailable`/`Read timed out` repetidos e estouraram o
`timeout-minutes` do job — mesmo a cota diária (RPD) estando bem longe
do limite (372/500 no momento do 2º travamento). `aguardar_orcamento_tpm`
abaixo fecha essa lacuna: janela móvel de 60s por MODELO (o padrão e o
fallback têm TPM próprio e independente), baseada no uso REAL relatado
pela própria API (`usageMetadata.totalTokenCount`) depois de cada
chamada — só usa estimativa (`ESTIMATIVA_TOKENS_PDF`/`_TEXTO`) pra decidir
se vale esperar ANTES da 1ª chamada de uma rajada, quando ainda não há
uso medido na janela.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from datetime import date
from typing import Any

import requests

from . import quota_gemini

_CERCA_MARKDOWN = re.compile(r"^```(?:json)?\s*|\s*```$")
_ESCAPE_INVALIDO = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')

#: Achado real 2026-09-24 (auditoria de UX pré-lançamento, "Bocai\u00úva"
#: vazando literalmente numa tela): às vezes o Gemini emite `\u` seguido
#: de MENOS de 4 dígitos hex válidos (ex: `\u00istika`, não `\u00XX` de
#: verdade) — um escape Unicode malformado, não um backslash solto comum.
#: `_ESCAPE_INVALIDO` corretamente identifica isso como escape inválido e
#: dobra a barra (`\\u00istika`) pra `json.loads` não quebrar, mas o
#: resultado String final fica com o artefato literal `\u00` visível —
#: nunca é um valor legítimo depois do parse (qualquer `\uXXXX` válido já
#: foi decodificado de verdade pelo `json.loads`), então é seguro remover
#: sempre. 25 vagas afetadas em produção antes desta correção (3 órgãos +
#: 8 cargos distintos, ver `docs/auditoria_lancamento_2026-09-24_ux.md`).
_ARTEFATO_ESCAPE_MALFORMADO = re.compile(r"\\u00")

#: só casa carga horária semanal simples ("40h", "20 horas", "40h
#: semanais") — rejeita de propósito escala tipo "12x36"/"24x72" e texto
#: composto ("20h, com plantões aos sábados"), onde converter pra
#: valor-hora exigiria adivinhar a jornada real (ver `calcular_valor_hora`).
_RE_CARGA_HORARIA_SEMANAL = re.compile(
    r"^\s*(\d{1,3})\s*h(?:oras)?(?:\s*semanais?)?\s*$", re.IGNORECASE
)

RPM_LIMITE = 15
TPM_LIMITE = 250_000
INTERVALO_MINIMO_ENTRE_CHAMADAS_S = 4.5  # 15 RPM = 1 a cada 4s; margem de segurança, por modelo

#: estimativas conservadoras de tokens só pra decidir se vale ESPERAR
#: antes da 1ª chamada de uma janela nova (sem uso real medido ainda) —
#: o uso real relatado pela API corrige a janela logo depois de cada
#: chamada, então uma estimativa grosseira aqui é segura.
ESTIMATIVA_TOKENS_PDF = 30_000
ESTIMATIVA_TOKENS_TEXTO = 2_000

_ultima_chamada_por_modelo: dict[str, float] = {}
_janela_tokens_por_modelo: dict[str, deque[tuple[float, int]]] = {}


def esperar_rate_limit(modelo: str) -> None:
    """Garante >=4.5s desde a última chamada real ao Gemini pro MESMO
    modelo, feita por QUALQUER um dos 3 módulos (gemini_pdf, gemini_texto,
    revisao_ia) no mesmo processo — estado compartilhado de propósito
    (ver docstring do módulo), agora por modelo: padrão e fallback têm
    RPM próprio, então intercalar entre os dois (ver
    `quota_gemini.proximo_modelo`) não precisa esperar o RPM de um pelo
    do outro."""
    agora = time.monotonic()
    ultima = _ultima_chamada_por_modelo.get(modelo, 0.0)
    espera = INTERVALO_MINIMO_ENTRE_CHAMADAS_S - (agora - ultima)
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada_por_modelo[modelo] = time.monotonic()


def _purgar_janela_tpm(modelo: str) -> deque[tuple[float, int]]:
    janela = _janela_tokens_por_modelo.setdefault(modelo, deque())
    limite = time.monotonic() - 60
    while janela and janela[0][0] < limite:
        janela.popleft()
    return janela


def aguardar_orcamento_tpm(modelo: str, tokens_estimados: int) -> None:
    """Espera o quanto for preciso pra que mandar mais `tokens_estimados`
    tokens pro `modelo` não estoure as 250k TPM da janela móvel de 60s
    dele — ver achado no docstring do módulo. `tokens_estimados` só
    importa quando a janela ainda não tem uso real registrado nela
    (chamada anterior recente já corrige com o valor de verdade via
    `registrar_tokens_usados`)."""
    while True:
        janela = _purgar_janela_tpm(modelo)
        usado = sum(tokens for _, tokens in janela)
        if usado + tokens_estimados <= TPM_LIMITE:
            return
        # +0.01 pra nunca acordar bem na borda dos 60s e o item mais
        # antigo ainda contar como "dentro da janela" na próxima purga
        # (`< limite`, não `<=`) — sem essa folga, um sleep calculado
        # exatamente pro instante de expiração podia girar sem nunca
        # progredir.
        espera = (janela[0][0] + 60) - time.monotonic() + 0.01
        if espera > 0:
            time.sleep(espera)


def registrar_tokens_usados(modelo: str, tokens: int) -> None:
    """Registra tokens realmente consumidos por uma chamada (ou a
    estimativa, se a resposta não trouxe `usageMetadata` — ex: erro
    antes de gerar conteúdo) na janela de TPM do modelo."""
    janela = _janela_tokens_por_modelo.setdefault(modelo, deque())
    janela.append((time.monotonic(), tokens))


def chamar_api(
    url: str,
    body: dict,
    *,
    chave: str,
    modelo: str,
    timeout: int,
    tokens_estimados: int,
) -> requests.Response:
    """Ponto único de chamada HTTP à API do Gemini, usado por
    gemini_pdf.py/gemini_texto.py/revisao_ia.py — respeita RPM (15) e
    TPM (250k, por modelo, ver `aguardar_orcamento_tpm`) antes de
    mandar, registra a chamada na cota diária por modelo
    (`quota_gemini.registrar_chamada`, migration 042) e registra o uso
    real de token (do `usageMetadata` da resposta, com fallback pra
    `tokens_estimados` se a resposta não vier em JSON) na janela de TPM
    depois. Não faz retry nem `raise_for_status` — isso fica com quem
    chama (ver `revisao_ia._chamar_gemini` pro retry com backoff)."""
    esperar_rate_limit(modelo)
    aguardar_orcamento_tpm(modelo, tokens_estimados)
    resposta = requests.post(url, params={"key": chave}, json=body, timeout=timeout)
    quota_gemini.registrar_chamada(modelo)
    tokens_usados = tokens_estimados
    try:
        tokens_usados = resposta.json().get("usageMetadata", {}).get("totalTokenCount", tokens_estimados)
    except ValueError:
        pass
    registrar_tokens_usados(modelo, tokens_usados)
    return resposta


def _limpar_artefatos_de_escape(valor: Any) -> Any:
    """Remove `\\u00` literal que sobrou de escape Unicode malformado do
    Gemini (ver `_ARTEFATO_ESCAPE_MALFORMADO`) — percorre dict/list
    recursivamente, só mexe em `str`."""
    if isinstance(valor, str):
        return _ARTEFATO_ESCAPE_MALFORMADO.sub("", valor)
    if isinstance(valor, dict):
        return {chave: _limpar_artefatos_de_escape(v) for chave, v in valor.items()}
    if isinstance(valor, list):
        return [_limpar_artefatos_de_escape(v) for v in valor]
    return valor


def parsear_json_resposta(texto: str) -> dict:
    """Remove cerca de markdown (```json ... ```), se presente, escapa
    qualquer backslash que não inicie um escape JSON válido e faz o
    parse. Levanta json.JSONDecodeError se o resultado ainda assim não
    for JSON válido (deixa o chamador decidir o que fazer). Limpa
    `\\u00` residual de escape malformado (ver
    `_limpar_artefatos_de_escape`) antes de devolver."""
    limpo = _CERCA_MARKDOWN.sub("", texto.strip())
    sanitizado = _ESCAPE_INVALIDO.sub(r"\\\\", limpo)
    return _limpar_artefatos_de_escape(json.loads(sanitizado))


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


#: casa "CR", "C.R.", "cadastro de reserva" (case-insensitive, com ou sem
#: pontuação) — abreviação comum em edital brasileiro pra vaga sem
#: quantidade fixa. Usado por `parsear_numero_vagas`.
_PADRAO_CADASTRO_RESERVA = re.compile(r"^\s*c\.?\s*r\.?\s*$|cadastro\s+de\s+reserva", re.IGNORECASE)


def parsear_numero_vagas(valor: Any) -> int | None:
    """Converte o `vagas_qtd` extraído pelo Gemini pra `int`, tratando o
    caso de "cadastro de reserva" — edital sem quantidade fixa de vaga,
    abreviado como "CR"/"C.R."/"cadastro de reserva" no texto original.
    Achado real em produção (`scripts/rodar_descoberta_google_search.py`,
    2 dias seguidos, "Concurso Prefeitura de Santa Mercedes/SP"): o
    Gemini às vezes devolve o texto literal "CR" nesse campo em vez de
    `null`, e o valor ia direto pro insert sem tratamento — Postgres
    rejeitava com `invalid input syntax for type integer: "CR"` e
    derrubava o processamento da vaga inteira. `None`/`NULL` é a leitura
    correta aqui (não existe quantidade fixa pra registrar), não um erro
    a propagar."""
    if valor is None:
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(valor)
    texto = str(valor).strip()
    if not texto:
        return None
    if _PADRAO_CADASTRO_RESERVA.search(texto):
        return None
    try:
        return int(texto)
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
        "numero_vagas": parsear_numero_vagas(vaga.get("vagas_qtd")),
        "taxa_inscricao": extraido.get("taxa_inscricao"),
        "carga_horaria": carga_horaria,
        "valor_hora": calcular_valor_hora(salario, salario_tipo, carga_horaria),
        "data_prova": parsear_data_iso(extraido.get("data_prova")),
        "requisitos": vaga.get("requisitos"),
        "banca_organizadora": extraido.get("banca_organizadora"),
        "tem_prova": extraido.get("tem_prova"),
        "exige_curriculo": extraido.get("exige_curriculo"),
    }
