"""Extração de cargo/salário/vagas de texto/HTML de publicação de
concurso público — usado pela fonte Instar (campo `descricao` do JSON de
dados abertos), onde o formato varia demais entre município pra um
parser HTML genérico e confiável (diferente de dom_amm_mg/IMESO, que têm
tabela com cabeçalho fixo). Mesmo padrão de uso do Gemini como camada de
extração sobre conteúdo não estruturado já usado em gemini_pdf.py pra
PDF — aqui é texto/HTML direto, sem PDF envolvido.

Cobre também o caso de o item não ser vaga de verdade (ex: eleição de
conselho tutelar, resultado de concurso antigo) — o prompt instrui o
Gemini a devolver `vagas: []` nesse caso, igual gemini_pdf.py já faz
pra PDF sem cargo/vaga real.
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

_ultima_chamada: float = 0.0


def _esperar_rate_limit() -> None:
    global _ultima_chamada
    agora = time.monotonic()
    espera = INTERVALO_MINIMO_ENTRE_CHAMADAS_S - (agora - _ultima_chamada)
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada = time.monotonic()


PROMPT_TEMPLATE = """Você está lendo o texto de uma publicação de prefeitura brasileira, que \
pode ou não ser sobre um processo seletivo/concurso público para preenchimento de \
vaga (pode ser, por exemplo, eleição de conselho, resultado de concurso antigo, ou \
outro assunto administrativo sem vaga nenhuma — nesses casos devolva "vagas": []).

Título: {titulo}

Texto (HTML ou texto corrido, pode ter tabela):
{texto}

Extraia um objeto JSON com:
- numero_edital (string, ex: "01/2026", ou null)
- orgao (nome do órgão/secretaria responsável, string ou null)
- data_publicacao (data "AAAA-MM-DD" de publicação/abertura, ou null)
- inscricoes_inicio (data "AAAA-MM-DD" de início das inscrições, ou null)
- inscricoes_fim (data "AAAA-MM-DD" de fim das inscrições, ou null)
- tipo_oportunidade (classifique o processo como um destes valores fixos, \
baseado no que o texto diz sobre o vínculo — use null só se genuinamente não \
der pra determinar):
  - "concurso_efetivo": concurso público pra cargo efetivo/estatutário
  - "processo_seletivo_temporario": processo seletivo simplificado (PSS) pra \
contrato temporário
  - "credenciamento": credenciamento de prestador de serviço, sem vínculo \
empregatício
  - "contratacao_emergencial": contratação emergencial/urgente
  - "selecao_plantao": seleção específica pra plantonista/escala de plantão \
(só use esta se TODAS as vagas forem de plantão — se for um processo normal \
com só ALGUMAS vagas de plantão, use "concurso_efetivo"/"processo_seletivo_\
temporario" conforme o caso e marque salario_tipo="plantao" nas vagas \
específicas)
- vagas: lista de vagas REAIS de preenchimento de cargo (vazia se o texto não for \
sobre isso), cada uma com:
  - cargo (string)
  - vagas_qtd (int ou null)
  - salario (number, o valor numérico em reais, sem "R$", ou null — se a \
remuneração for por hora/unidade que não converte num valor fixo, deixe null, \
não invente)
  - salario_tipo ("mensal" se `salario` for remuneração fixa mensal, "plantao" \
se `salario` for valor por plantão/turno/escala, ou null se `salario` for null \
ou não der pra determinar qual dos dois é)
  - requisitos (string curta ou null)
  - carga_horaria (string ou null)
Responda APENAS com o objeto JSON, sem markdown, sem texto adicional."""


class ErroExtracaoGemini(Exception):
    pass


def extrair_vagas_de_texto(
    titulo: str, texto: str, *, api_key: str | None = None, modelo: str | None = None
) -> dict:
    """Retorna {"numero_edital", "orgao", "data_publicacao",
    "inscricoes_inicio", "inscricoes_fim", "tipo_oportunidade",
    "vagas": [{"cargo", "vagas_qtd", "salario", "salario_tipo",
    "requisitos", "carga_horaria"}, ...]} — ver PROMPT_TEMPLATE.

    `modelo=None` (padrão) resolve dinamicamente via `quota_gemini`: ver
    docstring de `gemini_pdf.extrair_vagas_de_pdf`."""
    chave = api_key or os.environ.get("GEMINI_API_KEY")
    if not chave:
        raise ErroExtracaoGemini("GEMINI_API_KEY não definida.")
    modelo = modelo or quota_gemini.proximo_modelo()

    body = {
        "contents": [{"parts": [{"text": PROMPT_TEMPLATE.format(titulo=titulo, texto=texto)}]}],
        "generationConfig": {"temperature": 0},
    }

    _esperar_rate_limit()
    resposta = requests.post(
        URL_API.format(modelo=modelo), params={"key": chave}, json=body, timeout=60
    )
    if modelo == quota_gemini.MODELO_PADRAO:
        quota_gemini.registrar_chamada()
    resposta.raise_for_status()
    dados = resposta.json()

    try:
        texto_resposta = dados["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ErroExtracaoGemini(f"Resposta inesperada do Gemini: {dados}") from exc

    try:
        return gemini_util.parsear_json_resposta(texto_resposta)
    except json.JSONDecodeError as exc:
        raise ErroExtracaoGemini(f"JSON inválido do Gemini: {texto_resposta[:500]}") from exc
