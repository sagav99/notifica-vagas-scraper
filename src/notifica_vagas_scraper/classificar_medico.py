"""Classifica se `vagas.cargo` é médico — porta em Python da mesma lógica
de `lib/admin/classificarSaude.ts` (repo principal, função
`categorizarCargoSaude`, categoria `"medico"`). Mudar um lado sem o outro
faz o pipeline (aqui) e o site (lá) divergirem sobre o que é vaga de
médico — manter as duas listas de raízes sincronizadas manualmente.

Usado por `scripts/revisar_vagas.py` (2026-09-12, rebrand Med Vagas,
escopo virou só médico) como filtro determinístico ANTES de gastar
chamada de Gemini: cargo que não é médico vira rejeitada sem custo de
API, mesma filosofia de "não confiar no modelo caro pra decisão que dá
pra resolver com Python determinístico" já usada nesse arquivo pra data.
"""

from __future__ import annotations

import unicodedata

# Mesma rede de segurança de ESPECIALIDADES_MEDICAS_SEM_PREFIXO em
# classificarSaude.ts — cargo que descreve a especialidade sem repetir a
# palavra "médico". Termos ambíguos (plantonista, intensivista) ficam de
# fora de propósito.
_ESPECIALIDADES_SEM_PREFIXO = [
    "cardiolog", "pediatr", "psiquiatr", "ginecolog", "ortoped", "anestesiolog",
    "dermatolog", "oftalmolog", "urolog", "endocrinolog", "hematolog", "oncolog",
    "otorrino", "pneumolog", "reumatolog", "geriatr", "infectolog", "mastolog",
    "angiolog", "gastroenterolog", "nefrolog", "clinica medica", "cirurgia geral",
    "cirurgia vascular", "cirurgiao geral", "medicina intensiva", "medicina de familia",
    "medicina do trabalho", "medicina de emergencia",
]


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFD", texto)
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return sem_acento.strip().lower()


def eh_cargo_medico(cargo: str | None) -> bool:
    """Mesma regra de `categorizarCargoSaude` == "medico": exige a
    palavra completa "médico"/"médica" (prefixo `medic`) ou uma
    especialidade da lista acima, e exclui "médico veterinário"/"medicina
    veterinária" (categoria própria, fora do escopo do produto). Evita os
    2 falsos positivos reais já encontrados no lado TS: "Estagiário -
    Medicina" e "Biomedicina" não viram médico só por conterem o radical
    "medic"."""
    if not cargo:
        return False
    normalizado = _normalizar(cargo)
    palavras = normalizado.split()

    eh_veterinario = "veterinari" in normalizado
    if eh_veterinario:
        return False

    return any(p.startswith("medico") or p.startswith("medica") for p in palavras) or any(
        raiz in normalizado for raiz in _ESPECIALIDADES_SEM_PREFIXO
    )
