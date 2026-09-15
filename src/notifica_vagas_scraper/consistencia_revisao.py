"""2ª passada de verificação da revisão automática — achado da auditoria
de 2026-09-11 (`docs/auditoria_revisao_gemini_2026-09-11.md` no repo
principal): a revisão automática do Gemini não é determinística — pra
vagas com dado de entrada idêntico/quase idêntico do MESMO edital
(mesmo salário, mesmo padrão de resumo), o modelo às vezes aprova e às
vezes rejeita/marca incompleta. Causa provável: 1 chamada de Gemini por
vaga, sem contexto compartilhado entre vagas irmãs.

Decisão do usuário (2026-09-11), opção (b): quando a decisão de 1 vaga
diverge da maioria das irmãs do mesmo edital, reavaliar com uma 2ª
chamada ao Gemini que inclui o consenso das outras como contexto — mais
barato e mais simples que reprocessar TODO edital em lote (opção (a),
não escolhida).

"Vagas irmãs" = mesma fonte + mesmo município + mesmo `numero_edital`
(chave que identifica o documento real, não o texto do cargo). Quando
`numero_edital` é nulo — caso comum de vaga descoberta pelo Vigia via
Serper/agregador, que raramente extrai o número do edital — cai num
fallback por `orgao` normalizado (achado da checagem externa de
2026-09-15, `TAREFAS.md`: 3 vagas irmãs de Vargem Grande Paulista/SP,
mesma fonte/município/órgão, decisão divergente do Gemini, nunca
avaliadas juntas porque `numero_edital` era null nas 3 — ponto cego
estrutural). Vaga sem `numero_edital` E sem `orgao` preenchido nunca
agrupa com nada (chave única por vaga) — órgão vazio lumped junto
arriscaria juntar concursos genuinamente diferentes da mesma fonte.
Só grupos com pelo menos `TAMANHO_MINIMO_GRUPO` vagas revisadas contam —
maioria de 2 vagas não é sinal forte o bastante, e "maioria" só é usada
quando há uma decisão estritamente mais comum que qualquer outra (empate
não conta, não dá pra saber qual lado está certo).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

TAMANHO_MINIMO_GRUPO = 3


def _chave_grupo(vaga: dict[str, Any]) -> tuple[Any, ...]:
    numero_edital = vaga.get("numero_edital")
    if numero_edital:
        return (vaga["fonte_id"], vaga["municipio_id"], "edital", numero_edital)
    orgao_normalizado = " ".join((vaga.get("orgao") or "").split()).casefold()
    if not orgao_normalizado:
        return (vaga["fonte_id"], vaga["municipio_id"], "isolada", vaga["id"])
    return (vaga["fonte_id"], vaga["municipio_id"], "orgao", orgao_normalizado)


def agrupar_por_edital(vagas: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grupos: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for vaga in vagas:
        grupos[_chave_grupo(vaga)].append(vaga)
    return grupos


def achar_decisoes_divergentes(vagas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devolve as vagas cuja `revisao_status` diverge da maioria clara do
    próprio grupo de irmãs — cada item devolvido é uma cópia da vaga
    original com 2 chaves extras: `decisao_majoritaria` (str) e
    `contagem_grupo` (dict decisao -> total de vagas do grupo com essa
    decisão)."""
    divergentes: list[dict[str, Any]] = []
    for grupo in agrupar_por_edital(vagas).values():
        if len(grupo) < TAMANHO_MINIMO_GRUPO:
            continue

        contagem = Counter(v["revisao_status"] for v in grupo)
        mais_comuns = contagem.most_common()
        decisao_majoritaria, total_majoritario = mais_comuns[0]
        empatada = any(total == total_majoritario for _, total in mais_comuns[1:])
        if empatada:
            continue

        for vaga in grupo:
            if vaga["revisao_status"] != decisao_majoritaria:
                divergentes.append({
                    **vaga,
                    "decisao_majoritaria": decisao_majoritaria,
                    "contagem_grupo": dict(contagem),
                })
    return divergentes


def montar_contexto_irmas(vaga: dict[str, Any]) -> str:
    contagem: dict[str, int] = vaga["contagem_grupo"]
    total = sum(contagem.values())
    partes = ", ".join(f"{n} {d}" for d, n in contagem.items())
    local = f"{vaga['municipio_nome']}/{vaga['municipio_uf']}"
    identificador = (
        f"nº {vaga['numero_edital']}" if vaga.get("numero_edital") else f"órgão {vaga.get('orgao') or 'desconhecido'}"
    )
    return (
        f"Este edital ({identificador}, {local}) tem {total} vagas já "
        f"revisadas nesta mesma fonte: {partes}. A maioria foi decidida como "
        f"'{vaga['decisao_majoritaria']}'. Esta vaga específica foi decidida como "
        f"'{vaga['revisao_status']}' numa chamada anterior, isolada — reavalie se o "
        f"dado dela realmente justifica uma decisão diferente das irmãs, ou se segue "
        f"o mesmo padrão que já foi aprovado/rejeitado pra elas."
    )
