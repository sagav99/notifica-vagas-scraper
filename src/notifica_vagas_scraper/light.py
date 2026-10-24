"""Descoberta "leve" de vagas de enfermagem, engenharia e TI (2026-10-24).

Só alimenta posts de Instagram (tabela `vagas_light`, migration 112) — fora
do catálogo/alertas do site, que seguem só médico. Orçamento mínimo por
decisão do usuário: 1 consulta Serper por área/dia e no máx. 80 chamadas
Gemini por área/dia (teto imposto por `quota_gemini` via
`GEMINI_TETO_ORIGEM_DIA`, além do RPM/TPM de `gemini_util.chamar_api`).
Esta parte é pura (config, prompt, filtro); o I/O fica em
`scripts/rodar_descoberta_light.py`.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date

from . import gemini_util, quota_gemini

TETO_GEMINI_POR_AREA_DIA = 80

#: Prazo mínimo de inscrição (pedido do hub, 2026-10-24): vaga que fecha em
#: menos de 5 dias não rende post. Sem data de fim continua entrando.
PRAZO_MINIMO_DIAS = 5

#: Cargos que nunca entram (professor, estágio, auxiliar, monitor).
EXCLUIR_RE = re.compile(r"professor|docente|estagi|monitor|menor aprendiz")

#: area -> queries Serper (1 crédito cada, todas rodam a cada execução), regex
#: do cargo e descrição pro prompt. `ti` e `engenharia` pedem salário alto
#: (a partir de ~R$ 6.000, só prioridade: o filtro é do hub, aqui só se grava).
AREAS: dict[str, dict] = {
    "enfermagem": {
        "queries": ["concurso enfermeiro edital inscrições abertas"],
        "cargo_re": re.compile(r"enfermeir|enfermagem"),
        "descricao": "enfermeiro / técnico ou auxiliar de enfermagem",
    },
    "engenharia": {
        "queries": [
            "concurso engenheiro civil elétrico mecânico edital inscrições abertas salário",
            "concurso engenheiro ambiental sanitarista agrônomo segurança do trabalho edital inscrições abertas",
        ],
        "cargo_re": re.compile(r"engenheir"),
        "descricao": "engenheiro (civil, elétrico, mecânico, ambiental, químico, sanitarista, segurança do trabalho, agrônomo, controle e automação etc.)",
    },
    "ti": {
        "queries": [
            "concurso analista de sistemas desenvolvedor analista de TI edital inscrições abertas salário",
            "concurso tecnologia da informação analista de dados redes segurança da informação edital inscrições abertas",
        ],
        "cargo_re": re.compile(
            r"analista de (sistemas|ti\b|tecnologia|dados|redes|infraestrutura|seguranca|suporte|desenvolvimento)"
            r"|desenvolvedor|programador|tecnologia da informacao|cientista de dados|ciencia de dados"
            r"|administrador de (redes|banco|dados)|seguranca da informacao|engenheiro de (software|dados|computacao)"
            r"|\bti\b|ciencia da computacao|infraestrutura de ti"
        ),
        "descricao": "tecnologia da informação (analista de sistemas, desenvolvedor/programador, analista de TI, dados, redes, segurança da informação)",
    },
}

URL_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

PROMPT = """Você está lendo uma página sobre concurso público / processo seletivo no Brasil.
Extraia SÓ as vagas de: {descricao}. Ignore todos os outros cargos. Se a página não tiver
vaga dessa área (ou não for edital de vaga), devolva "vagas": [].

Título: {titulo}

Texto:
{texto}

Responda só JSON, sem markdown:
{{"orgao": string|null, "municipio": string|null, "uf": "XX"|null,
 "numero_edital": string|null, "banca": string|null,
 "inscricoes_inicio": "AAAA-MM-DD"|null, "inscricoes_fim": "AAAA-MM-DD"|null,
 "vagas": [{{"cargo": string, "vagas_qtd": number|null, "salario": number|null}}]}}
Nunca invente valor; use null quando não constar."""


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()


def cargo_da_area(cargo: str, area: str) -> bool:
    norm = _normalizar(cargo)
    return bool(AREAS[area]["cargo_re"].search(norm)) and not EXCLUIR_RE.search(norm)


def _data(valor: object) -> date | None:
    if not isinstance(valor, str):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _numero(valor: object) -> float | None:
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


def normalizar_resposta(bruto: object) -> dict:
    """O Gemini às vezes devolve lista em vez do objeto pedido (achado no 1º
    run real, 2026-10-24): lista de vagas vira `{"vagas": [...]}`, lista de
    objetos-edital usa o primeiro; qualquer outra coisa vira vazio."""
    if isinstance(bruto, dict):
        return bruto
    if isinstance(bruto, list) and bruto and all(isinstance(x, dict) for x in bruto):
        return {"vagas": bruto} if "cargo" in bruto[0] else bruto[0]
    return {}


def montar_linhas(extraido: dict, area: str, *, hoje: date, url: str, titulo: str) -> list[dict]:
    """Linhas de `vagas_light` a partir da extração (texto ou PDF): só cargos da
    área e só inscrição com pelo menos `PRAZO_MINIMO_DIAS` restantes (ou sem data de fim — não dá pra afirmar
    que encerrou). Dedup por cargo dentro do mesmo documento."""
    extraido = normalizar_resposta(extraido)
    fim = _data(extraido.get("inscricoes_fim"))
    if fim is not None and (fim - hoje).days < PRAZO_MINIMO_DIAS:
        return []
    linhas: dict[str, dict] = {}
    for vaga in extraido.get("vagas") or []:
        cargo = vaga.get("cargo") if isinstance(vaga, dict) else None
        if not isinstance(cargo, str) or not cargo.strip() or not cargo_da_area(cargo, area):
            continue
        qtd = _numero(vaga.get("vagas_qtd"))
        salario = _numero(vaga.get("salario"))
        linhas.setdefault(
            cargo.strip().casefold(),
            {
                "area": area,
                "cargo": cargo.strip(),
                "orgao": extraido.get("orgao"),
                "municipio": extraido.get("municipio"),
                "uf": extraido.get("uf"),
                "numero_vagas": int(qtd) if qtd is not None else None,
                "salario": salario,
                "inscricoes_inicio": _data(extraido.get("inscricoes_inicio")),
                "inscricoes_fim": fim,
                "numero_edital": extraido.get("numero_edital"),
                "banca": extraido.get("banca") or extraido.get("banca_organizadora"),
                "url": url,
                "titulo": titulo,
            },
        )
    return list(linhas.values())


def extrair_de_texto(titulo: str, texto: str, area: str, *, api_key: str | None = None) -> dict:
    """1 chamada Gemini (via `chamar_api`: RPM, TPM, teto diário e disjuntor)."""
    chave = api_key or os.environ.get("GEMINI_API_KEY")
    if not chave:
        raise RuntimeError("GEMINI_API_KEY não definida.")
    modelo = quota_gemini.proximo_modelo()
    corpo = {
        "contents": [
            {"parts": [{"text": PROMPT.format(descricao=AREAS[area]["descricao"], titulo=titulo, texto=texto)}]}
        ],
        "generationConfig": {"temperature": 0},
    }
    resposta = gemini_util.chamar_api(
        URL_API.format(modelo=modelo),
        corpo,
        chave=chave,
        modelo=modelo,
        timeout=60,
        tokens_estimados=max(gemini_util.ESTIMATIVA_TOKENS_TEXTO, len(texto) // 4),
    )
    resposta.raise_for_status()
    try:
        return gemini_util.parsear_json_resposta(resposta.json()["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError(f"resposta inesperada do Gemini: {exc}") from exc
