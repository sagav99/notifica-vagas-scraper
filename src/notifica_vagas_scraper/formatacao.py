"""Helpers de parsing de texto solto (edital/diário) pra tipos do banco."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


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
