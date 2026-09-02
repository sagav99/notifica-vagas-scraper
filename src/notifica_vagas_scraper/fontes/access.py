"""Parser do Instituto ACCESS (concursos.access.org.br), banca
organizadora nacional (RJ, SP, MG, GO, SC vistos numa mesma amostra) —
achado 2026-09-01, investigação via `curl`/Python puro. **`TAREFAS.md`
tinha isso marcado como "403 confirmado" (mesmo bloqueio que o Codex
achou em julho) — não reproduzido nesta sessão, o domínio respondeu 200
normalmente.** Não sabemos se foi desbloqueio do lado deles ou
diferença de rede/IP; se voltar a dar 403, não é regressão do parser.

Mesma plataforma da JCM Concursos (**ProSeleta / selecao.net.br** —
"Desenvolvido por ProSeleta" no rodapé) — a página de detalhe do
processo (`/informacoes/<id>/`) é idêntica em estrutura à da JCM, por
isso reaproveita `fontes/proseleta.py` inteiro
(`listar_documentos`/`escolher_edital`/`listar_vagas_html`). Só o card
da listagem (`/index/abertos/`) tem layout diferente: aqui tipo, órgão e
número do edital vêm em elementos HTML separados (`<p class="tipo">`,
`<h3>`, `<p class="edital">`), não concatenados numa string só como na
JCM.

**Achado de peso pra prioridade "saúde/médicos" do projeto (2026-09-01,
ver `TAREFAS.md`)**: o edital 01/2025 de Contagem/MG (598 vagas, fora do
período de inscrição agora — não é "vaga nova" pra notificar, mas prova
que a fonte é real) tem **~40 especialidades médicas diferentes**
(Clínico Geral, Cardiologia, Pediatria, Psiquiatria, Cirurgia Geral,
Médico da Família etc.) — sinal forte de que essa banca deve ser
monitorada de perto pra pegar o próximo edital assim que abrir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .proseleta import Documento, VagaHtml, escolher_edital, listar_documentos, listar_vagas_html

__all__ = [
    "ItemListagem",
    "Documento",
    "VagaHtml",
    "BASE_URL",
    "listar_processos_abertos",
    "extrair_municipio_uf",
    "listar_documentos",
    "escolher_edital",
    "listar_vagas_html",
]

BASE_URL = "https://concursos.access.org.br"

_PREFIXOS_ENTIDADE = (
    ("Prefeitura Municipal de ", "Prefeitura Municipal"),
    ("Prefeitura de ", "Prefeitura"),
    ("Câmara Municipal de ", "Câmara Municipal"),
    ("Câmara de ", "Câmara"),
)


@dataclass
class ItemListagem:
    processo_id: int
    url: str
    orgao: str
    municipio: str
    uf: str
    tipo_processo: str
    numero_edital: str | None


def extrair_municipio_uf(entidade: str) -> tuple[str, str, str] | None:
    """`None` pra entidade que não é prefeitura/câmara (ex: universidade
    federal, instituto de previdência — vistos na amostra real, não
    mapeiam 1:1 pra um município) ou sem UF no final."""
    match = re.match(r"^(.*)/([A-Za-z]{2})$", entidade.strip())
    if not match:
        return None
    resto, uf = match.group(1).strip(), match.group(2).upper()

    for prefixo, orgao in _PREFIXOS_ENTIDADE:
        if resto.startswith(prefixo):
            return orgao, resto[len(prefixo):].strip(), uf
    return None


def listar_processos_abertos(html: str) -> list[ItemListagem]:
    soup = BeautifulSoup(html, "html.parser")
    itens: list[ItemListagem] = []

    for h3 in soup.select("div.dados h3"):
        link = h3.find("a", href=True)
        if link is None:
            continue
        match_id = re.match(r"^/informacoes/(\d+)/$", link.get("href", ""))
        if not match_id:
            continue

        separado = extrair_municipio_uf(link.get_text(strip=True))
        if separado is None:
            continue
        orgao, municipio, uf = separado

        container = h3.find_parent("div", class_="dados")
        tipo_tag = container.find("p", class_="tipo") if container else None
        edital_tag = container.find("p", class_="edital") if container else None
        tipo_processo = tipo_tag.get_text(strip=True) if tipo_tag else ""
        numero_edital = None
        if edital_tag is not None:
            texto = edital_tag.get_text(" ", strip=True)
            numero_edital = re.sub(r"^Edital\s*n[ºo°]?\s*", "", texto, flags=re.IGNORECASE).strip() or None

        itens.append(
            ItemListagem(
                processo_id=int(match_id.group(1)),
                url=f"{BASE_URL}{match_id.group(0)}",
                orgao=orgao,
                municipio=municipio,
                uf=uf,
                tipo_processo=tipo_processo,
                numero_edital=numero_edital,
            )
        )

    return itens
