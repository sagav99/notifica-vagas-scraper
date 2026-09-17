"""Completude de vaga médica via Codex CLI — pedido do usuário
(2026-09-16): a auditoria de completude existente
(`auditoria_completude.py`) só releitura o link já salvo (Gemini) e, se
quebrado, busca 1 substituto via Serper (saldo único, não renova). Esta
camada é diferente: usa o `codex exec` (CLI do ChatGPT, cota da
assinatura do usuário, não API paga por token) com acesso de rede
(`--sandbox workspace-write`, `network_access=true` no config do
usuário — não `danger-full-access`: escreve arquivo só dentro do
diretório de trabalho isolado, nunca no resto do disco) pra pesquisar
na internet de verdade — não só reler o link salvo, mas navegar até
achar o documento certo (ex: link salvo era só o Anexo I, Codex achou o
edital consolidado inteiro sozinho, seguindo o mesmo padrão de URL do
site — confirmado em teste real, 2026-09-16).

Roda LOCALMENTE na máquina do usuário (`scripts/completude_codex.py`),
nunca em GitHub Actions — depende de `codex login` (ChatGPT) já feito
na máquina, sem API key própria pra colocar em secret de CI. Pensado
pra rodar manualmente/diariamente até a cota da assinatura esgotar
(usuário decide quando parar, sem contador automático aqui).

Cada vaga custa ~2min e ~40-50k tokens de cota do Codex (medido em
teste real, 2026-09-16) — MUITO mais caro por chamada que Gemini/Claude,
por isso processa poucas vagas por execução (LIMITE_VAGAS_POR_EXECUCAO
pequeno) e é pensado pra rodar em várias sessões diárias, não de uma vez.

Mesma garantia de nunca sobrescrever campo já preenchido: só pede ao
Codex os campos que já estão `null` na vaga, e só aceita resposta com
`confianca` "alta"/"media" (rejeita "baixa" — não vale a pena gravar
palpite fraco)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import db

#: mesmo teto de campos de auditoria_completude.py — allowlist única de
#: coluna que este módulo pode preencher (nunca update genérico).
CAMPOS_PEDIVEIS = db.CAMPOS_COMPLETUDE

#: tipo JSON Schema de cada campo, pro --output-schema do Codex — datas
#: viram string (formato ISO no prompt), Codex não tem tipo "date" nativo.
_TIPO_SCHEMA: dict[str, str] = {
    "numero_vagas": "integer",
    "taxa_inscricao": "number",
    "carga_horaria": "string",
    "valor_hora": "number",
    "data_prova": "string",
    "requisitos": "string",
    "banca_organizadora": "string",
    "tem_prova": "boolean",
    "exige_curriculo": "boolean",
    "salario": "number",
    "salario_tipo": "string",
    "inscricoes_inicio": "string",
    "inscricoes_fim": "string",
}

_CAMPOS_DATA = {"data_prova", "inscricoes_inicio", "inscricoes_fim"}

CONFIANCAS_ACEITAS = {"alta", "media"}

TIMEOUT_CODEX_S = 480  # ~4x o tempo medido no teste real (~2min17s), margem pra caso difícil

#: trechos de mensagem de erro que indicam cota/limite de uso da
#: assinatura estourado (não erro pontual de rede/vaga específica) —
#: mesmo espírito da heurística já usada em
#: `scripts/lib/classificar-saida-claude.mjs` (repo principal) pro loop
#: do /continuar-tarefas. Casamento por substring, case-insensitive:
#: texto exato do Codex CLI nesse cenário não foi confirmado ainda (2026-09-16,
#: rodando "até acabar os tokens" pela 1ª vez) — lista ampla de propósito,
#: ajustar aqui se a mensagem real observada não bater com nenhum destes.
_SINAIS_COTA_ESGOTADA = (
    "usage limit", "rate limit", "quota", "too many requests",
    "429", "resource_exhausted", "limite de uso", "cota esgotada",
)


def eh_erro_de_cota(mensagem: str) -> bool:
    """True se a mensagem de erro parece ser cota/limite de uso da
    assinatura Codex esgotado (deve pausar bastante antes de tentar de
    novo), False se parece erro pontual (rede instável, timeout de 1
    vaga difícil, site fora do ar) — nesse caso só pula pra próxima."""
    texto = mensagem.lower()
    return any(sinal in texto for sinal in _SINAIS_COTA_ESGOTADA)

#: sandbox restrito (não danger-full-access): rede liberada
#: (network_access=true, config do usuário) mas escrita de arquivo
#: confinada ao --cd informado em rodar_codex — Codex não toca no
#: resto do disco durante a pesquisa.
SANDBOX_MODE = "workspace-write"


class ErroCompletudeCodex(Exception):
    pass


def campos_faltando(vaga: dict[str, Any]) -> list[str]:
    """Subconjunto de CAMPOS_PEDIVEIS que está `null` nesta vaga —
    o que vamos PEDIR ao Codex (nunca pede campo que já tem valor)."""
    return [campo for campo in CAMPOS_PEDIVEIS if vaga.get(campo) is None]


def montar_schema(campos: list[str]) -> dict[str, Any]:
    """JSON Schema pro --output-schema do `codex exec`, cobrindo só os
    campos pedidos desta vaga + confianca/fonte_usada (sempre exigidos,
    usados pra decidir se aceita a resposta — ver `filtrar_campos_aceitos`)."""
    propriedades = {campo: {"type": [_TIPO_SCHEMA[campo], "null"]} for campo in campos}
    propriedades["confianca"] = {"type": "string", "enum": ["alta", "media", "baixa"]}
    propriedades["fonte_usada"] = {"type": "string"}
    return {
        "type": "object",
        "properties": propriedades,
        "required": [*campos, "confianca", "fonte_usada"],
        "additionalProperties": False,
    }


def montar_prompt(vaga: dict[str, Any], campos: list[str], *, link: str | None) -> str:
    descricao_campos = "\n".join(f"- {campo}: {_descricao_campo(campo)}" for campo in campos)
    bloco_link = (
        f"Link do documento já salvo no nosso banco (pode ser só uma parte do edital, ex: só o "
        f"anexo de cargos — se for o caso, procure o edital completo/outros anexos do MESMO "
        f"concurso, geralmente no mesmo domínio/padrão de URL): {link}"
        if link
        else "Não temos nenhum link salvo pra esta vaga — procure o edital na internet."
    )
    return f"""Você está pesquisando um edital de concurso público brasileiro pra completar dados \
que faltam no nosso banco de dados de vagas.

Vaga: {vaga.get("cargo")}
Órgão: {vaga.get("orgao") or "(não informado)"}
Município: {vaga.get("nome")}/{vaga.get("uf")}
Número do edital: {vaga.get("numero_edital") or "(não informado)"}

{bloco_link}

Campos que faltam (pesquise e confirme cada um a partir do texto REAL do edital/documento oficial):
{descricao_campos}

NUNCA invente valor. Se não conseguir confirmar um campo com o texto real de um documento \
oficial, devolva null nesse campo especificamente (não chute, não estime). Datas no formato \
"AAAA-MM-DD". Responda no formato JSON pedido, incluindo "confianca" (alta/media/baixa — baixa \
se você teve que inferir/estimar em vez de ler o valor direto no documento) e "fonte_usada" \
(URL do documento real onde confirmou, ou "não encontrado" se nada foi confirmado)."""


_DESCRICOES_CAMPOS = {
    "numero_vagas": "número de vagas oferecidas pra este cargo específico (inteiro)",
    "taxa_inscricao": "valor da taxa de inscrição em reais, sem 'R$' (número)",
    "carga_horaria": "carga horária semanal do cargo (texto, ex: '40h semanais')",
    "valor_hora": "valor por hora/plantão em reais, se a remuneração for por hora (número)",
    "data_prova": "data da prova objetiva/escrita principal (AAAA-MM-DD)",
    "requisitos": "requisito de escolaridade/formação exigido pro cargo (texto curto)",
    "banca_organizadora": "nome da banca/instituto organizador, ou 'própria' se for a própria prefeitura/órgão",
    "tem_prova": "o processo seletivo exige prova objetiva/escrita? (true/false)",
    "exige_curriculo": "o processo seletivo exige análise de currículo/títulos? (true/false)",
    "salario": "valor do salário/remuneração em reais, sem 'R$' (número)",
    "salario_tipo": "'mensal' se o salário for remuneração fixa mensal, 'plantao' se for por plantão/turno",
    "inscricoes_inicio": "data de início das inscrições (AAAA-MM-DD)",
    "inscricoes_fim": "data de fim das inscrições (AAAA-MM-DD)",
}


def _descricao_campo(campo: str) -> str:
    return _DESCRICOES_CAMPOS.get(campo, campo)


def filtrar_campos_aceitos(resultado: dict[str, Any], campos_pedidos: list[str]) -> dict[str, Any]:
    """Só aceita campo com valor não-null E confiança alta/media — rejeita
    confiança baixa inteira (não grava palpite fraco) e ignora qualquer
    chave fora de `campos_pedidos` (defesa contra resposta fora do
    schema, mesmo já validado por --output-schema)."""
    if resultado.get("confianca") not in CONFIANCAS_ACEITAS:
        return {}
    aceitos: dict[str, Any] = {}
    for campo in campos_pedidos:
        valor = resultado.get(campo)
        if valor is None:
            continue
        aceitos[campo] = valor
    return aceitos


def rodar_codex(prompt: str, schema: dict[str, Any], *, timeout: int = TIMEOUT_CODEX_S) -> dict[str, Any]:
    """Chama `codex exec` de verdade (subprocesso) com sandbox
    `workspace-write` (rede liberada pelo config do usuário, escrita de
    arquivo confinada ao diretório temporário isolado passado em `--cd`)
    e schema de saída forçado. Levanta ErroCompletudeCodex em qualquer
    falha (timeout, exit code != 0, JSON inválido) — quem chama decide o
    que fazer (pular a vaga, tentar de novo depois)."""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = Path(tmp) / "schema.json"
        saida_path = Path(tmp) / "saida.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False))

        try:
            processo = subprocess.run(
                [
                    "codex", "exec",
                    "--sandbox", SANDBOX_MODE,
                    "--cd", tmp,
                    "--skip-git-repo-check",
                    "--output-schema", str(schema_path),
                    "-o", str(saida_path),
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ErroCompletudeCodex(f"codex exec estourou timeout de {timeout}s") from exc

        if processo.returncode != 0:
            raise ErroCompletudeCodex(f"codex exec saiu com código {processo.returncode}: {processo.stderr[-2000:]}")

        if not saida_path.exists():
            raise ErroCompletudeCodex(f"codex exec não escreveu arquivo de saída. stdout: {processo.stdout[-2000:]}")

        try:
            return json.loads(saida_path.read_text())
        except json.JSONDecodeError as exc:
            raise ErroCompletudeCodex(f"saída do codex exec não é JSON válido: {saida_path.read_text()[:500]}") from exc


def _converter_tipos(campos: dict[str, Any]) -> dict[str, Any]:
    """`atualizar_campos_vaga` grava direto no Postgres — datas em string
    ISO do Codex precisam virar `date` real (psycopg não converte string
    solta pra coluna `date` automaticamente em todo driver/versão)."""
    from datetime import date as _date

    convertido = dict(campos)
    for campo in _CAMPOS_DATA:
        valor = convertido.get(campo)
        if isinstance(valor, str):
            try:
                convertido[campo] = _date.fromisoformat(valor)
            except ValueError:
                convertido.pop(campo)  # data mal formada — não grava, não quebra o resto
    return convertido


def processar_vaga(conn, vaga: dict[str, Any]) -> str:
    """Pipeline completo pra 1 vaga: monta prompt/schema só com os campos
    faltando, roda o Codex, filtra por confiança, grava o que foi aceito
    e registra o evento em `vagas_conferencias`. Devolve o `resultado`
    gravado (mesmo vocabulário livre já usado por auditoria_completude.py:
    'campo_preenchido'/'sem_alteracao'/'erro_codex')."""
    campos = campos_faltando(vaga)
    if not campos:
        return "sem_alteracao"

    link = vaga.get("url")
    prompt = montar_prompt(vaga, campos, link=link)
    schema = montar_schema(campos)

    try:
        resultado_bruto = rodar_codex(prompt, schema)
    except ErroCompletudeCodex as exc:
        # achado real (2026-09-16): cota/limite de uso esgotado ("You've
        # hit your usage limit") vinha do Codex como um ErroCompletudeCodex
        # normal, era engolido AQUI (virava "erro_codex" no log e seguia
        # pra próxima vaga) antes de `scripts/completude_codex.py` (o
        # loop principal) conseguir detectar e pausar — resultado: 80
        # tentativas seguidas todas falhando pela mesma cota esgotada, sem
        # nunca pausar. Cota esgotada tem que propagar pro chamador, não
        # ser tratada como falha isolada desta vaga.
        if eh_erro_de_cota(str(exc)):
            raise
        db.registrar_conferencia(
            conn, vaga_id=vaga["id"], conferido_por="completude_codex",
            resultado="erro_codex", detalhe=str(exc)[:1000],
        )
        return "erro_codex"

    aceitos = filtrar_campos_aceitos(resultado_bruto, campos)
    if not aceitos:
        db.registrar_conferencia(
            conn, vaga_id=vaga["id"], conferido_por="completude_codex",
            resultado="sem_alteracao", detalhe=json.dumps(resultado_bruto, ensure_ascii=False)[:1000],
        )
        return "sem_alteracao"

    db.atualizar_campos_vaga(conn, vaga_id=vaga["id"], campos=_converter_tipos(aceitos))
    db.registrar_conferencia(
        conn, vaga_id=vaga["id"], conferido_por="completude_codex",
        resultado="campo_preenchido",
        detalhe=f"Campos: {sorted(aceitos)}. Fonte: {resultado_bruto.get('fonte_usada', '')}"[:1000],
    )
    return "campo_preenchido"
