"""Completude de vaga médica via Gemini com busca real na internet —
pedido do usuário (2026-09-18): pausar `completude_codex.py` (cota da
assinatura ChatGPT esgotando rápido) sem perder a capacidade de
"agir como humano" — pesquisar a vaga na internet, abrir o site/edital
de verdade, conferir os dados — usando a cota do Gemini que já está
disponível, com a ideia explícita de no futuro trocar só o cliente do
modelo pela API do Claude (mais forte), mantendo o resto do pipeline
igual.

Diferença pra `auditoria_completude.py` (só relê o link já salvo, mesmo
Gemini da coleta): esta camada chama o Gemini com as tools nativas
`google_search` (busca real no Google) e/ou `url_context` (busca e lê o
conteúdo de uma URL) — cobre tanto "pesquisar a vaga na internet" quanto
"abrir o link/site do concurso e ler o edital", sem precisar do Codex
CLI. **`google_search` só entra quando falta link pra reler ou quando
estamos atrás do link do PDF do edital** (`consultar`, achado real
2026-09-23: essa tool tem cota própria, separada e bem mais escassa que
`generateContent`/`url_context` — 429 específico dela mesmo com a cota
geral do modelo ainda de boa) — relendo um link que já existe, só
`url_context` já resolve, sem gastar essa cota à toa.

Segunda função, além de completar campo faltando: confirmar que a vaga é
de fato concurso público/PSS de emprego, não residência médica/fellowship/
estágio — achado real 2026-09-18 (edital "de fellowship" da Santa Casa de
Misericórdia de BH, 16 vagas aprovadas pela revisão normal do Gemini, que
só vê campo estruturado e não lê o edital; só percebido na conferência
manual do usuário). Toda chamada deste módulo pede esse campo, mesmo
quando não falta nenhum dado de completude — é a auditoria que a revisão
normal não faz.

Compartilha a cota diária do Gemini com `gemini_pdf`/`gemini_texto`/
`revisao_ia` via `quota_gemini.proximo_modelo()` (mesmo par
padrão/fallback, mesmo rastreador `gemini_quota_diaria`) — não tem cota
própria, por isso roda com lote pequeno (ver `scripts/completude_gemini.py`),
mesmo espírito conservador de `LIMITE_SERPER_POR_EXECUCAO` em
`auditoria_completude.py`."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from . import db, gemini_util, quota_gemini
from .completude_codex import _DESCRICOES_CAMPOS as DESCRICOES_CAMPOS
from .revisao_ia import CotaGeminiEsgotadaError

CAMPOS_PEDIVEIS = db.CAMPOS_COMPLETUDE

URL_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
TENTATIVAS_MAX = 3
BACKOFF_INICIAL_S = 5.0

#: chamada com google_search/url_context pode buscar e ler página(s)
#: inteira(s) — bem mais cara que revisao_ia (só campo estruturado, sem
#: tool), na mesma ordem de grandeza de gemini_pdf (documento inteiro).
ESTIMATIVA_TOKENS_COMPLETUDE = 20_000

TIMEOUT_S = 90

CONFIANCAS_ACEITAS = {"alta", "media"}

_CAMPOS_DATA = {"data_prova", "inscricoes_inicio", "inscricoes_fim"}


class ErroCompletudeGemini(Exception):
    pass


def campos_faltando(vaga: dict[str, Any]) -> list[str]:
    """Subconjunto de CAMPOS_PEDIVEIS que está `null` nesta vaga — mesmo
    critério de `completude_codex.campos_faltando`."""
    return [campo for campo in CAMPOS_PEDIVEIS if vaga.get(campo) is None]


def precisa_link_pdf(vaga: dict[str, Any]) -> bool:
    """True quando a única evidência salva não é PDF (notícia, página
    HTML genérica etc.) — sinal pra pedir ao Gemini o link direto do
    edital em PDF, além dos campos de completude. Pedido do usuário
    (2026-09-18): a página de detalhe da vaga já mostra 1 card por
    evidência (`app/painel/[id]/page.tsx`, repo principal) — uma vaga com
    só a notícia como evidência não tem o segundo link (o PDF do edital)
    pra quem quer conferir o documento oficial direto."""
    return vaga.get("tipo_documento") != "pdf"


def montar_schema_texto(campos: list[str], *, pedir_link_pdf: bool) -> str:
    """Descrição textual do formato de resposta esperado — não dá pra
    usar `responseSchema` estruturado da API do Gemini junto com as tools
    `google_search`/`url_context` (limitação da API: saída estruturada e
    tool nativa de busca não combinam na mesma chamada), por isso o
    formato vai só como instrução no prompt, igual `revisao_ia.py` já faz
    pra revisão normal, e a resposta é parseada com
    `gemini_util.parsear_json_resposta`."""
    linhas_campos = "\n".join(f'  "{campo}": <valor ou null>,' for campo in campos)
    linha_link_pdf = '  "link_edital_pdf": "<URL do PDF do edital, ou null se não achar>",\n' if pedir_link_pdf else ""
    return f"""{{
{linhas_campos}
{linha_link_pdf}  "eh_vaga_de_emprego": <true ou false>,
  "motivo_natureza": "<1 frase explicando a classificação anterior>",
  "confianca": "alta", "media" ou "baixa",
  "fonte_usada": "<URL do documento real onde confirmou, ou \\"não encontrado\\">"
}}"""


def montar_prompt(vaga: dict[str, Any], campos: list[str], *, link: str | None, pedir_link_pdf: bool) -> str:
    descricao_campos = "\n".join(f"- {campo}: {DESCRICOES_CAMPOS.get(campo, campo)}" for campo in campos)
    bloco_campos = (
        f"""Campos que faltam (pesquise e confirme cada um a partir do texto REAL do edital/documento \
oficial — use as ferramentas de busca e leitura de página disponíveis, não invente):
{descricao_campos}

"""
        if campos
        else ""
    )
    bloco_link = (
        f"Link do documento já salvo no nosso banco (pode ser só uma parte do edital, ex: só o "
        f"anexo de cargos — se for o caso, procure o edital completo/outros anexos do MESMO "
        f"concurso, geralmente no mesmo domínio/padrão de URL): {link}"
        if link
        else "Não temos nenhum link salvo pra esta vaga — pesquise o edital na internet."
    )
    bloco_pdf = (
        """A única evidência que temos salva pra esta vaga NÃO é o PDF do edital (é notícia/página \
HTML) — procure o link direto do PDF do edital completo (mesmo concurso/processo seletivo, pode \
estar no site oficial da prefeitura/banca organizadora), pra preencher "link_edital_pdf". Só \
preencha se tiver certeza de que é o mesmo processo seletivo (mesmo órgão/cargo/edital) — null se \
não achar com confiança.

"""
        if pedir_link_pdf
        else ""
    )
    return f"""Você está conferindo uma vaga de concurso público brasileiro que já está publicada \
no nosso site, usando busca real na internet e leitura de página pra confirmar os dados — faça o \
mesmo trabalho que um humano faria: pesquise a vaga, abra o site do concurso, leia o edital.

Vaga: {vaga.get("cargo")}
Órgão: {vaga.get("orgao") or "(não informado)"}
Município: {vaga.get("nome")}/{vaga.get("uf")}
Número do edital: {vaga.get("numero_edital") or "(não informado)"}

{bloco_link}

{bloco_pdf}{bloco_campos}IMPORTANTE — antes de mais nada, confirme a NATUREZA da oportunidade: é uma vaga de \
EMPREGO (concurso público efetivo, processo seletivo simplificado/temporário, contratação \
emergencial ou credenciamento)? Ou é, na verdade, um processo de FORMAÇÃO — residência médica, \
fellowship, especialização, estágio, bolsa de estudo, curso — que NÃO é vaga de emprego, mesmo \
quando o cargo/área parece profissão médica normal? Preencha "eh_vaga_de_emprego" com essa \
distinção; "motivo_natureza" explica em 1 frase o que confirmou isso (cite o termo real do edital, \
ex: "edital fala em 'processo seletivo para residência médica'").

NUNCA invente valor pra nenhum campo pedido. Se não conseguir confirmar um campo com o texto real \
de um documento oficial, devolva null nesse campo especificamente (não chute, não estime). Datas \
no formato "AAAA-MM-DD". "confianca": alta/media/baixa (baixa se você teve que inferir/estimar em \
vez de ler o valor direto no documento — nesse caso não afirme "eh_vaga_de_emprego" com certeza \
alta também).

Responda APENAS com um objeto JSON, sem markdown, sem texto adicional, no formato:
{montar_schema_texto(campos, pedir_link_pdf=pedir_link_pdf)}"""


def _chamar_gemini(body: dict, *, chave: str, modelo: str) -> dict:
    """Mesmo padrão de retry de `revisao_ia._chamar_gemini`: até
    TENTATIVAS_MAX em falha transitória (5xx, timeout, conexão), 4xx não
    tenta de novo. Duplicado em vez de reaproveitado porque
    `revisao_ia._chamar_gemini` é privado do módulo — mesmo espírito já
    aceito no resto do repo (cada módulo Gemini tem seu próprio retry
    pequeno)."""
    ultimo_erro: Exception | None = None
    for tentativa in range(TENTATIVAS_MAX):
        if tentativa > 0:
            time.sleep(BACKOFF_INICIAL_S * tentativa)
        try:
            resposta = gemini_util.chamar_api(
                URL_API.format(modelo=modelo),
                body,
                chave=chave,
                modelo=modelo,
                timeout=TIMEOUT_S,
                tokens_estimados=ESTIMATIVA_TOKENS_COMPLETUDE,
            )
            resposta.raise_for_status()
            return resposta.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            ultimo_erro = exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                raise CotaGeminiEsgotadaError(str(exc)) from exc
            if status is None or status < 500:
                raise
            ultimo_erro = exc
    assert ultimo_erro is not None
    raise ultimo_erro


def consultar(vaga: dict[str, Any], campos: list[str], *, api_key: str | None, modelo: str | None) -> dict[str, Any]:
    """1 chamada ao Gemini com as tools `google_search`+`url_context`
    ligadas, devolve o dict já parseado (campos pedidos + eh_vaga_de_emprego
    + motivo_natureza + confianca + fonte_usada). Levanta
    `ErroCompletudeGemini` em qualquer falha de rede/parsing;
    `CotaGeminiEsgotadaError` propaga direto (mesmo contrato de
    `revisao_ia.decidir_revisao`/`completude_codex.rodar_codex`)."""
    chave = api_key or os.environ.get("GEMINI_API_KEY")
    if not chave:
        raise ErroCompletudeGemini("GEMINI_API_KEY não configurada.")
    modelo = modelo or quota_gemini.proximo_modelo()
    link = vaga.get("url")
    pedir_pdf = precisa_link_pdf(vaga)
    prompt = montar_prompt(vaga, campos, link=link, pedir_link_pdf=pedir_pdf)

    # `google_search` (busca real no Google) tem cota própria, separada e
    # bem mais apertada que `generateContent`/`url_context` (achado real,
    # 2026-09-23: 429 "RESOURCE_EXHAUSTED" específico dessa tool com a
    # cota geral do modelo ainda de boa) — só vale o custo quando não temos
    # link pra reler (`link` nulo) ou quando estamos atrás de um link novo
    # (PDF do edital, `pedir_pdf`); relendo um link que já existe,
    # `url_context` sozinho já cobre ("abrir e ler a página"), sem tocar
    # na cota escassa de busca.
    tools: list[dict[str, dict]] = []
    if link is None or pedir_pdf:
        tools.append({"google_search": {}})
    if link is not None:
        tools.append({"url_context": {}})

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": tools,
        "generationConfig": {"temperature": 0},
    }

    try:
        corpo = _chamar_gemini(body, chave=chave, modelo=modelo)
        texto = corpo["candidates"][0]["content"]["parts"][0]["text"]
        return gemini_util.parsear_json_resposta(texto)
    except CotaGeminiEsgotadaError:
        raise
    except Exception as exc:
        raise ErroCompletudeGemini(str(exc)) from exc


def filtrar_campos_aceitos(resultado: dict[str, Any], campos_pedidos: list[str]) -> dict[str, Any]:
    """Mesmo critério de `completude_codex.filtrar_campos_aceitos`: só
    aceita com confiança alta/media, ignora chave fora de
    `campos_pedidos`."""
    if resultado.get("confianca") not in CONFIANCAS_ACEITAS:
        return {}
    aceitos: dict[str, Any] = {}
    for campo in campos_pedidos:
        valor = resultado.get(campo)
        if valor is None:
            continue
        aceitos[campo] = valor
    return aceitos


def _converter_tipos(campos: dict[str, Any]) -> dict[str, Any]:
    """Mesma conversão de `completude_codex._converter_tipos` — data em
    string ISO vira `date` real antes de gravar no Postgres."""
    from datetime import date as _date

    convertido = dict(campos)
    for campo in _CAMPOS_DATA:
        valor = convertido.get(campo)
        if isinstance(valor, str):
            try:
                convertido[campo] = _date.fromisoformat(valor)
            except ValueError:
                convertido.pop(campo)
    return convertido


def processar_vaga(
    conn, vaga: dict[str, Any], *, api_key: str | None = None, modelo: str | None = None
) -> str:
    """Pipeline completo pra 1 vaga: sempre confere a natureza (emprego vs.
    formação), além de completar o que estiver faltando. Devolve o
    `resultado` gravado: 'rejeitada_nao_e_concurso' (achou que não é vaga
    de emprego, reverteu pra rejeitada), 'campo_preenchido',
    'sem_alteracao' ou 'erro_gemini'. `CotaGeminiEsgotadaError` propaga
    pro chamador decidir pausar o lote (mesmo contrato dos outros 2
    módulos de completude/revisão)."""
    campos = campos_faltando(vaga)

    try:
        resultado_bruto = consultar(vaga, campos, api_key=api_key, modelo=modelo)
    except ErroCompletudeGemini as exc:
        db.registrar_conferencia(
            conn, vaga_id=vaga["id"], conferido_por="completude_gemini",
            resultado="erro_gemini", detalhe=str(exc)[:1000],
        )
        return "erro_gemini"

    confianca_ok = resultado_bruto.get("confianca") in CONFIANCAS_ACEITAS
    eh_vaga_de_emprego = resultado_bruto.get("eh_vaga_de_emprego")

    if confianca_ok and eh_vaga_de_emprego is False:
        motivo = resultado_bruto.get("motivo_natureza") or "Confirmado via pesquisa que não é vaga de emprego (concurso/PSS)."
        db.marcar_nao_e_vaga_de_emprego(conn, vaga_id=vaga["id"], motivo=motivo)
        return "rejeitada_nao_e_concurso"

    link_pdf_achado = _registrar_link_pdf_se_achado(conn, vaga, resultado_bruto, confianca_ok=confianca_ok)

    aceitos = filtrar_campos_aceitos(resultado_bruto, campos)
    if not aceitos:
        detalhe = json.dumps(resultado_bruto, ensure_ascii=False)[:1000]
        if link_pdf_achado:
            detalhe = f"Evidência PDF adicionada: {link_pdf_achado}. {detalhe}"
        db.registrar_conferencia(
            conn, vaga_id=vaga["id"], conferido_por="completude_gemini",
            resultado="sem_alteracao", detalhe=detalhe,
        )
        return "sem_alteracao"

    db.atualizar_campos_vaga(conn, vaga_id=vaga["id"], campos=_converter_tipos(aceitos))
    detalhe = f"Campos: {sorted(aceitos)}. Fonte: {resultado_bruto.get('fonte_usada', '')}"
    if link_pdf_achado:
        detalhe = f"Evidência PDF adicionada: {link_pdf_achado}. {detalhe}"
    db.registrar_conferencia(
        conn, vaga_id=vaga["id"], conferido_por="completude_gemini",
        resultado="campo_preenchido", detalhe=detalhe[:1000],
    )
    return "campo_preenchido"


def _registrar_link_pdf_se_achado(
    conn, vaga: dict[str, Any], resultado_bruto: dict[str, Any], *, confianca_ok: bool
) -> str | None:
    """Se o Gemini achou (com confiança alta/media) o link do PDF do
    edital pra uma vaga cuja única evidência salva não era PDF, soma essa
    2ª evidência (nunca troca a original) — pedido do usuário
    (2026-09-18): a página de detalhe já renderiza 1 card por evidência,
    então uma vaga só precisa ganhar a 2ª linha em `vaga_evidencias` pra
    aparecer com os 2 links (fonte original + edital em PDF). Devolve a
    URL registrada, ou `None` se não achou/não registrou nada (já existe
    igual, sem confiança, ou vaga já tinha PDF)."""
    if not precisa_link_pdf(vaga):
        return None
    link_pdf = resultado_bruto.get("link_edital_pdf")
    if not confianca_ok or not link_pdf or not isinstance(link_pdf, str):
        return None
    if link_pdf == vaga.get("url"):
        return None
    db.registrar_evidencia_adicional(
        conn,
        vaga_id=vaga["id"],
        fonte_id=vaga["fonte_id"],
        identificador_externo=f"completude-gemini-pdf-{vaga['id']}",
        url=link_pdf,
        tipo_documento="pdf",
    )
    return link_pdf
