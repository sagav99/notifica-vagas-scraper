"""Fundatec (banca do RS que também faz concursos em SC/PR e no resto do país).

O site `fundatec.org.br` está atrás de WAF (resposta 202 de desafio para
robô), mas os editais ficam num bucket S3 público e listável
(`concursos-publicacoes`), um prefixo por concurso:
`<id>/publico/edital_abertura/<arquivo>.pdf`. Achado 2026-10-28, via
extensão do Chrome. Esta fonte lista o bucket (XML), pega os concursos
recentes, lê o texto do edital e só devolve os que têm cargo de médico numa
prefeitura de uma UF do projeto.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO

BUCKET_URL = "https://concursos-publicacoes.s3.amazonaws.com"
ID_PREFIX = "fundatec"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
# Linha que começa com o cargo (tabela de vagas): "MÉDICO ...", "Médico Pediatra".
# Exclui "atestado/laudo/perícia médica", que não começam a linha.
_RE_LINHA_MEDICO = re.compile(r"^\s*(?:\d+\s*[.\-–)]*\s*)?m[eé]dic[oa]\b(?!\s+(?:veterin|e/ou|de\s+sua|ou\b))", re.IGNORECASE | re.MULTILINE)
_RE_PREFEITURA = re.compile(r"PREFEITURA\s+MUNICIPAL\s+DE\s+([^\n/]+?)\s*(?:/\s*([A-Z]{2}))?\s*(?:\n|-|–|$)", re.IGNORECASE)
_RE_ESTADO = re.compile(r"ESTADO\s+D[OE]\s+([A-ZÁÂÃÉÊÍÓÔÕÚÇ ]+)", re.IGNORECASE)
_UF_POR_ESTADO = {"RIO GRANDE DO SUL": "RS", "SANTA CATARINA": "SC", "PARANA": "PR",
                  "MINAS GERAIS": "MG", "SAO PAULO": "SP"}


@dataclass
class EditalFundatec:
    concurso_id: int
    chave: str  # caminho do PDF no bucket
    modificado: datetime

    @property
    def url(self) -> str:
        return f"{BUCKET_URL}/{self.chave}"


def _listar(requests_get, **params) -> ET.Element:
    resposta = requests_get(BUCKET_URL + "/", params=params, timeout=30)
    resposta.raise_for_status()
    return ET.fromstring(resposta.content)


def listar_ids_concursos(requests_get) -> list[int]:
    ids: list[int] = []
    marcador = ""
    while True:
        params = {"delimiter": "/", "max-keys": 1000}
        if marcador:
            params["marker"] = marcador
        raiz = _listar(requests_get, **params)
        for p in raiz.iter(f"{_NS}Prefix"):
            txt = (p.text or "").rstrip("/")
            if txt.isdigit():
                ids.append(int(txt))
        if (raiz.findtext(f"{_NS}IsTruncated") or "").lower() != "true":
            break
        marcador = raiz.findtext(f"{_NS}NextMarker") or ""
        if not marcador:
            break
    return sorted(set(ids))


def editais_do_concurso(requests_get, concurso_id: int) -> list[EditalFundatec]:
    raiz = _listar(requests_get, prefix=f"{concurso_id}/publico/edital_abertura/")
    editais = []
    for c in raiz.iter(f"{_NS}Contents"):
        chave = c.findtext(f"{_NS}Key") or ""
        if not chave.lower().endswith(".pdf"):
            continue
        mod = datetime.fromisoformat((c.findtext(f"{_NS}LastModified") or "").replace("Z", "+00:00"))
        editais.append(EditalFundatec(concurso_id, chave, mod))
    return sorted(editais, key=lambda e: e.modificado)


def recentes(editais: list[EditalFundatec], *, dias: int, agora: datetime | None = None) -> bool:
    agora = agora or datetime.now(timezone.utc)
    return any(e.modificado >= agora - timedelta(days=dias) for e in editais)


def texto_pdf(pdf: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(BytesIO(pdf)) as doc:
        return "\n".join((p.extract_text() or "") for p in doc.pages)


def tem_cargo_medico(texto: str) -> bool:
    return bool(_RE_LINHA_MEDICO.search(texto))


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").strip().upper()


def municipio_e_uf(texto_inicio: str) -> tuple[str, str] | None:
    """(nome do município, UF) do cabeçalho do edital de uma prefeitura, ou
    None (câmara, autarquia, hospital, universidade...)."""
    m = _RE_PREFEITURA.search(texto_inicio)
    if not m:
        return None
    nome = re.sub(r"\s+", " ", m.group(1)).strip()
    uf = m.group(2)
    if not uf:
        e = _RE_ESTADO.search(texto_inicio)
        if e:
            uf = _UF_POR_ESTADO.get(_sem_acento(e.group(1)))
    return (nome, uf) if uf else None


def casar_municipio(nome: str, uf: str, municipios: dict[tuple[str, str], int]) -> int | None:
    return municipios.get((_sem_acento(nome), uf))
