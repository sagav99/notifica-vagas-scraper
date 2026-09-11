"""Localiza em qual página de um PDF um cargo aparece, sem gastar IA —
usado pelo backfill de print de evidência antiga
(`scripts/backfill_print_evidencias.py`) antes de cair no fallback caro
(`gemini_pdf.localizar_pagina_cargo`). Decisão do usuário, 2026-09-10:
"se não precisar de IA pra fazer, pode fazer muito" — por isso a busca de
texto puro (`pdfplumber.extract_text`, já dependência do projeto) vem
primeiro e só cai pra Gemini quando ambígua ou vazia.

Função de match separada da leitura do PDF de propósito (`localizar_
pagina_por_texto` recebe a lista de textos já extraídos, não os bytes do
PDF) pra dar pra testar sem precisar de um PDF real.
"""

from __future__ import annotations

import io
import re
import unicodedata

import pdfplumber


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def localizar_pagina_por_texto(textos_paginas: list[str | None], cargo: str) -> int | None:
    """`None` se o cargo não aparecer em nenhuma página OU aparecer em
    mais de uma (ambíguo — mesma especialidade pode se repetir com
    jornada/faixa diferente, caso real já documentado em `pbh_ibfc.py`) —
    só devolve página quando o match é único, pra nunca gravar print da
    página errada."""
    alvo = _normalizar(cargo) if cargo else ""
    if not alvo:
        return None

    paginas_encontradas = [
        indice + 1
        for indice, texto in enumerate(textos_paginas)
        if texto and alvo in _normalizar(texto)
    ]
    return paginas_encontradas[0] if len(paginas_encontradas) == 1 else None


def localizar_pagina_pdf_sem_ia(pdf_bytes: bytes, cargo: str) -> int | None:
    """Abre o PDF, tenta achar a página do `cargo` só com busca de texto.
    PDF de 1 página só não precisa buscar — é ela mesma, sem ambiguidade
    possível. PDF escaneado (sem texto extraível) devolve `None` em toda
    página, cai pro fallback de IA no chamador.

    PDF corrompido/malformado (`pdfplumber`/`pdfminer` não consegue nem
    abrir, ex.: "No /Root object!") também devolve `None` em vez de deixar
    a exceção subir — achado real em produção (2026-09-11,
    `backfill_print_evidencias.py`): 1 evidência com PDF quebrado matava o
    processo inteiro no meio do lote de 200, perdendo o restante. Mesma
    filosofia do caso "PDF escaneado": sem texto extraível de forma
    confiável, cai pro fallback de IA (que pode ou não conseguir ler o
    mesmo PDF) em vez de travar o lote inteiro."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) == 1:
                return 1
            textos = [pagina.extract_text() for pagina in pdf.pages]
    except pdfplumber.utils.exceptions.PdfminerException:
        return None
    return localizar_pagina_por_texto(textos, cargo)
