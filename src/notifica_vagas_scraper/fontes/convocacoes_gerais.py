"""Detector GERAL de convocação de médico (nível da publicação), para o PDF de convocações do plano Pro (2026-11-08).

Diferente de `convocados_publicos` (que só reconhece convocação de concurso que JÁ temos cadastrado e extrai nomes), aqui basta o
diário publicar uma convocação/chamamento de candidatos aprovados com cargo de médico, de qualquer concurso: guardamos município, data,
link e um trecho. Serve ao "saiu convocação" (sem nomes) e a medir a cobertura. Sem Gemini, só texto.
"""

from __future__ import annotations

import re
import unicodedata

JANELA_ANTES = 500
JANELA_DEPOIS = 900
MAX_TRECHO = 320

_GATILHO = re.compile(r"convoc\w*|chamamento|nomea\w*|nomei\w*")
_MEDICO = re.compile(r"\bmedic[oa]s?\b|\bmedicina\b|\bpsiquiatr\w*|\bpediatr\w*|\bginecolog\w*|\bortoped\w*|\bcardiolog\w*|\banestesi\w*")
_VETERINARIO = re.compile(r"medic[oa]s?\s+veterinari|veterinari[oa]")
_CONTEXTO = re.compile(r"aprovad\w*|classificad\w*|candidat\w*|concurso publico|processo seletivo|cadastro de reserva|edital")
# "nomeação" sozinha é muito comum em cargo em comissão: só vale com sinal de concurso.
_CONTEXTO_NOMEACAO = re.compile(r"aprovad\w*|classificad\w*|concurso publico|cadastro de reserva")
_NAO_CONVOCACAO = re.compile(r"convoc\w*\s+(?:para\s+)?(?:a\s+)?(?:reuniao|audiencia publica|conferencia|sessao|assembleia|eleicao)")
_CONCURSO = re.compile(r"(concurso\s+publico|processo\s+seletivo(?:\s+simplificado)?|edital)\s*(?:n[ºo.°]*\s*)?0*(\d{1,4})\s*/\s*(\d{2,4})")


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")


def _limpar(trecho: str) -> str:
    return re.sub(r"\s+", " ", trecho).strip()


def detectar(titulo: str, texto: str) -> dict | None:
    """Devolve {trecho, concurso, tipo} da primeira convocação de médico encontrada, ou None.
    `trecho` é do texto original (com acentos), com o tamanho limitado."""
    if not texto:
        return None
    norm = normalizar(texto)
    if len(norm) != len(texto):  # a normalização NFD sem acento preserva o tamanho quase sempre; se não, trabalha só no normalizado
        original = norm
    else:
        original = texto
    for g in _GATILHO.finditer(norm):
        ini, fim = max(0, g.start() - JANELA_ANTES), min(len(norm), g.end() + JANELA_DEPOIS)
        janela = norm[ini:fim]
        if not _MEDICO.search(janela) or not _CONTEXTO.search(janela):
            continue
        if g.group(0).startswith("nome") and not _CONTEXTO_NOMEACAO.search(janela):
            continue
        if _NAO_CONVOCACAO.search(norm[g.start() : g.end() + 60]):
            continue
        m_med = _MEDICO.search(janela)
        # só "médico veterinário" na janela não vale
        so_vet = all(_VETERINARIO.search(janela[max(0, mm.start() - 5) : mm.end() + 30]) for mm in _MEDICO.finditer(janela) if mm.group(0).startswith("medic"))
        if so_vet and m_med and m_med.group(0).startswith("medic"):
            continue
        c = _CONCURSO.search(janela)
        concurso = f"{c.group(1).replace('  ', ' ').title()} {int(c.group(2))}/{c.group(3)}" if c else None
        return {
            "trecho": _limpar(original[max(0, g.start() - 120) : g.start() + MAX_TRECHO]),
            "concurso": concurso,
            "tipo": "nomeacao" if g.group(0).startswith("nome") else "convocacao",
        }
    return None


def municipio_da_entidade(entidade: str | None) -> str | None:
    """'Prefeitura Municipal de Foo' -> 'Foo'; entidades que não são prefeitura (câmara, instituto...) ficam como estão."""
    if not entidade:
        return None
    m = re.match(r"(?i)^\s*(?:prefeitura(?:\s+municipal)?(?:\s+d[eao])?|munic[ií]pio\s+d[eao]|pm\s+d[eao])\s+(.+?)\s*$", entidade)
    return (m.group(1) if m else entidade).strip()[:120]
