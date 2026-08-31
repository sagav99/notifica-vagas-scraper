"""Parser da FGV Conhecimento (banca organizadora), site Drupal.

Estrutura investigada (ver docs/fixtures/fgv/ no repo principal):
- GET /concursos lista os concursos "em andamento" num bloco Views
  (`div` com classe contendo "view-concursos-em-andamento"), paginado via
  `?page=N` (GET puro, sem JS necessário pra navegar). Cada item só tem
  título + link — sem UF/município como metadado, precisa casar texto
  contra lista de município.
- Página de cada concurso tem uma lista de PDFs (edital, retificações,
  comunicados, resultados). O edital principal (com a tabela de cargo/
  salário) é identificável pelo texto do link começar com "Edital" — as
  retificações vêm rotuladas "1ª/2ª Retificação" e comunicados como
  "COMUNICADO". Cargo/salário só existem dentro do PDF, não em HTML — ver
  `..gemini_pdf` pra extração.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup

BASE_URL = "https://conhecimento.fgv.br"


@dataclass
class ItemConcurso:
    titulo: str
    url: str


def listar_concursos(html: str) -> list[ItemConcurso]:
    soup = BeautifulSoup(html, "html.parser")
    bloco = soup.find("div", class_=lambda c: c and "view-concursos-em-andamento" in c)
    if bloco is None:
        return []

    itens: list[ItemConcurso] = []
    for row in bloco.find_all("div", class_="views-row"):
        link = row.find("a", href=True)
        if link is None:
            continue
        titulo = link.get_text(strip=True)
        if not titulo:
            continue
        url = link["href"]
        if url.startswith("/"):
            url = BASE_URL + url
        itens.append(ItemConcurso(titulo=titulo, url=url))
    return itens


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()


def encontrar_municipio(titulo: str, municipios: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Casa o título do concurso contra uma lista de (nome, uf) de município.

    Só considera nomes com pelo menos 6 caracteres (sem espaço) pra reduzir
    falso positivo de palavra comum — achado real na triagem manual de
    bancas: "Tocantins" e "Chácara" bateram por coincidência em títulos sem
    relação nenhuma com o município.

    Rejeita match quando "estado" aparece antes do nome no título E o nome
    vem imediatamente precedido de "do/da/dos/das/de" — dois achados reais
    rodando contra produção: existe um município real chamado Tocantins em
    MG, mas "Secretaria de Estado de Saúde do Tocantins" é o ESTADO do
    Tocantins; e "Secretaria da Educação do Estado de São Paulo" é o
    ESTADO de SP, não o município capital (a preposição antes do nome do
    estado varia com o gênero gramatical do nome — "do Tocantins", "de São
    Paulo", "da Bahia" — por isso aceita qualquer uma). "Estado" e a
    preposição podem estar separados por palavras no meio (tipo "de
    Saúde"). Título de município real com preposição na frente mas sem
    "estado" em lugar nenhum (ex: "Prefeitura Municipal do Salvador")
    continua batendo normalmente. Limitação conhecida: um título hipotético
    que mencionasse um órgão estadual E um município na mesma frase
    perderia o município (falso negativo) — aceitável, o risco de dado
    errado (vaga presa ao município errado) é pior que perder uma vaga.

    Rejeita também quando o nome batido é só o PREFIXO de um nome de lugar
    maior que não está na lista — achado real: "São Lourenço" (MG) bateu
    dentro de "São Lourenço da Mata" (PE, fora da nossa lista de MG/SP);
    "nome + ' da'/'do' + mais palavra" sugere que o lugar de verdade é mais
    longo do que o município cadastrado.

    Limitação aceita e não resolvida aqui: nome comum que também é
    município (ex: "Registro/SP" batendo em "cartório de Registro") não
    tem heurística de texto que resolva de forma confiável — fica pra
    revisão administrativa (`vagas.revisao_status`) filtrar antes de virar
    visível pro usuário, que é o mesmo mecanismo que já protege contra
    qualquer engano de fonte automática.
    """
    texto = _normalizar(titulo)
    melhor: tuple[str, str] | None = None
    for nome, uf in municipios:
        nome_norm = _normalizar(nome)
        if len(nome_norm.replace(" ", "")) < 6:
            continue
        posicao = texto.find(nome_norm)
        if posicao == -1:
            continue
        prefixo = texto[:posicao]
        ultima_palavra = prefixo.split()[-1] if prefixo.split() else ""
        if "estado" in prefixo and ultima_palavra in {"do", "da", "dos", "das", "de"}:
            continue
        sufixo = texto[posicao + len(nome_norm) :]
        if re.match(r"^\s+d[oa]s?\s+\w", sufixo):
            continue
        if melhor is None or len(nome_norm) > len(_normalizar(melhor[0])):
            melhor = (nome, uf)
    return melhor


def encontrar_pdf_edital_principal(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=lambda h: h and h.endswith(".pdf")):
        texto = link.get_text(strip=True)
        if re.match(r"^edital\b", texto, re.IGNORECASE):
            href = link["href"]
            return href if href.startswith("http") else BASE_URL + href
    return None
