"""Descoberta "leve" de vagas de enfermagem, engenharia e TI (2026-10-24).

Só alimenta posts de Instagram (tabela `vagas_light`, migration 112) — fora
do catálogo/alertas do site, que seguem só médico. Orçamento mínimo por
decisão do usuário: consultas curtas Serper por área/execução e no máx. 80 chamadas
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
#: do cargo e descrição pro prompt. Consultas curtas, no estilo das de médico
#: (escolhidas em 2026-11-11 por teste de 18 candidatas: nº de resultados novos
#: com o cargo no título, janela qdr:d7); as longas e específicas de antes
#: limitavam demais. `ti` e `engenharia` pedem salário alto (a partir de
#: ~R$ 6.000, só prioridade: o filtro é do hub).
AREAS: dict[str, dict] = {
    "enfermagem": {
        # 2 fixas + `UFS_ROTATIVAS_POR_EXECUCAO` por estado em rodízio (ver
        # `consultas_da_area`); pedido do usuário em 2026-11-16: 3 consultas
        # nacionais traziam pouquíssimo enfermeiro (o post do @enfvagas ficava
        # sem vaga). Teste: "concurso enfermeiro <UF>" rende 4-7 links novos.
        "queries": [
            "processo seletivo enfermeiro prefeitura",
            "edital enfermeiro inscrições abertas",
        ],
        "ufs_rotativas": [
            "SP", "MG", "BA", "RJ", "PR", "RS", "PE", "CE", "SC", "GO", "PA", "MA", "ES",
            "PB", "PI", "RN", "MT", "MS", "AL", "SE", "AM", "TO", "RO", "AC", "AP", "RR", "DF",
        ],
        "cargo_re": re.compile(r"enfermeir|enfermagem"),
        "descricao": "enfermeiro / técnico ou auxiliar de enfermagem",
    },
    "engenharia": {
        "queries": [
            "concurso engenheiro civil prefeitura",
            "concurso público engenheiro",
            "concurso engenheiro elétrico mecânico",
        ],
        "cargo_re": re.compile(r"engenheir"),
        "descricao": "engenheiro (civil, elétrico, mecânico, ambiental, químico, sanitarista, segurança do trabalho, agrônomo, controle e automação etc.)",
    },
    "ti": {
        "queries": [
            "concurso tecnologia da informação",
            "concurso TI MG",
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

UFS_ROTATIVAS_POR_EXECUCAO = 3


def consultas_da_area(area: str, hoje: date) -> list[str]:
    """Consultas fixas da área + as de estado do dia (rodízio por data, sem estado a gravar)."""
    cfg = AREAS[area]
    consultas = list(cfg["queries"])
    ufs = cfg.get("ufs_rotativas")
    if ufs:
        # a execução roda dia sim/dia não: dividir por 2 evita pular estados
        inicio = (hoje.toordinal() // 2 * UFS_ROTATIVAS_POR_EXECUCAO) % len(ufs)
        consultas += [f"concurso enfermeiro {ufs[(inicio + i) % len(ufs)]}" for i in range(UFS_ROTATIVAS_POR_EXECUCAO)]
    return consultas


URL_API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

PROMPT = """Você está lendo uma página sobre concurso público / processo seletivo no Brasil.
Extraia SÓ as vagas de: {descricao}. Ignore todos os outros cargos. Se a página não tiver
vaga dessa área (ou não for edital de vaga), devolva "vagas": [].

Título: {titulo}

Texto:
{texto}

Procure com atenção o salário/vencimento/remuneração mensal de cada cargo e as datas de
início e fim das inscrições, mesmo que estejam em outro trecho da página.

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


#: Páginas que são só notícia/agregador costumam não trazer salário nem prazo; o
#: edital oficial linkado traz. No máx. isso de leituras extras de PDF por área/execução
#: (cada uma é 1 chamada Gemini, dentro do teto diário por área).
LIMITE_EDITAL_POR_AREA_EXECUCAO = 8


def faltam_dados(linhas: list[dict]) -> bool:
    return bool(linhas) and any(l["salario"] is None or l["inscricoes_fim"] is None for l in linhas)


def mesclar_com_edital(extraido: dict, do_edital: dict) -> dict:
    """Completa (nunca sobrescreve) a extração da página com a do edital em PDF:
    campos gerais nulos e salário/qtd de cada cargo; cargos que só o edital tem entram.
    Salário por plantão do edital é ignorado (não é remuneração mensal)."""
    base = dict(normalizar_resposta(extraido))
    edital = normalizar_resposta(do_edital)
    for campo in ("orgao", "municipio", "uf", "numero_edital", "inscricoes_inicio", "inscricoes_fim"):
        if not base.get(campo) and edital.get(campo):
            base[campo] = edital[campo]
    if not base.get("banca"):
        base["banca"] = edital.get("banca") or edital.get("banca_organizadora")
    do_pdf = {
        _normalizar(v["cargo"]).strip(): v
        for v in edital.get("vagas") or []
        if isinstance(v, dict) and isinstance(v.get("cargo"), str)
    }
    vagas = []
    for v in base.get("vagas") or []:
        v = dict(v) if isinstance(v, dict) else v
        par = do_pdf.pop(_normalizar(v["cargo"]).strip(), None) if isinstance(v, dict) and isinstance(v.get("cargo"), str) else None
        if par:
            if v.get("salario") is None and par.get("salario_tipo") != "plantao":
                v["salario"] = par.get("salario")
            if v.get("vagas_qtd") is None:
                v["vagas_qtd"] = par.get("vagas_qtd")
        vagas.append(v)
    base["vagas"] = vagas
    return base


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
