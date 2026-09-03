"""Helpers de parsing de texto solto (edital/diário) pra tipos do banco."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

#: Prefixos de entidade municipal (prefeitura/câmara) — compartilhado entre
#: `fontes/imam.py` (`extrair_municipio`) e `fontes/access.py`
#: (`extrair_municipio_uf`), que antes reimplementavam essa lista cada um
#: do seu jeito com case-handling diferente (achado de code review,
#: 2026-09-02). Ordem importa: prefixo mais específico ("Municipal de")
#: precisa vir antes do mais genérico ("de") pra não bater errado.
PREFIXOS_ORGAO_MUNICIPAL = (
    ("Prefeitura Municipal de ", "Prefeitura Municipal"),
    ("Prefeitura de ", "Prefeitura"),
    ("Câmara Municipal de ", "Câmara Municipal"),
    ("Câmara de ", "Câmara"),
)


def separar_prefixo_orgao_municipal(texto: str) -> tuple[str, str] | None:
    """Se `texto` começar com um prefixo conhecido de prefeitura/câmara
    (comparação sempre case-insensitive), retorna `(tipo_orgao, resto)` —
    `resto` preserva a acentuação/espaçamento originais de `texto`, só a
    comparação do prefixo ignora caixa. Retorna `None` se não bater com
    nenhum prefixo conhecido (ex: consórcio intermunicipal, universidade
    federal — não mapeiam 1:1 pra um município, decisão do chamador
    pular). Cada fonte aplica sua própria normalização de caixa no
    `resto` depois (IMAM usa `.title()` porque a origem vem em CAIXA
    ALTA; ACCESS não precisa, a origem já vem bem formatada)."""
    alvo = texto.strip()
    alvo_maiusculo = alvo.upper()
    for prefixo, tipo_orgao in PREFIXOS_ORGAO_MUNICIPAL:
        if alvo_maiusculo.startswith(prefixo.upper()):
            return tipo_orgao, alvo[len(prefixo):].strip()
    return None


def parsear_salario_brl(texto: str | None) -> Decimal | None:
    """"R$ 1.234,56" / "1.234,56" / "R$1234,56" -> Decimal("1234.56").

    Retorna None se não conseguir extrair um número (ex: "a combinar").
    """
    if not texto:
        return None

    match = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)", texto)
    if not match:
        return None

    bruto = match.group(1).replace(".", "").replace(",", ".")
    try:
        return Decimal(bruto)
    except InvalidOperation:
        return None
