#!/usr/bin/env python3
"""Descoberta (não coleta) de concursos via PCI Concursos — decisão do
usuário (2026-09-01, TAREFAS.md): a PCI usa Cloudflare Turnstile de
verdade no link do edital (`POST /noticias/link` exige token resolvido
num navegador de verdade), então não dá pra baixar o PDF do edital por
aqui. Serve só como SINAL: qual município de MG/SP tem notícia recente
de concurso/processo seletivo, cruzado com `public.municipios` — não
grava `vagas` (sem cargo/salário/edital reais, não tem o que inserir),
só imprime/exporta uma lista pra revisão manual, mesmo padrão das
triagens anteriores (`docs/dados/triagem_*.csv` no repo principal).

Fonte de dados: a página-índice de cada UF (`/concursos/<uf>/`) já lista
~60 notícias recentes num único GET, sem precisar varrer município por
município.

Cada notícia também é buscada (1 GET a mais por achado) pra extrair os
links externos citados no corpo (`parse_external_references`, portado do
protótipo `docs/handoff_2026-08-31/.../pci.py` no repo principal) —
medição real em 2026-09-02 (TAREFAS.md) confirmou 6/6 notícias da amostra
com pelo menos 1 link externo utilizável (banca ou prefeitura), então
vale sempre exportar isso: quando o domínio já é um adaptador conhecido
(Instar/Actcon/etc., conferir manualmente), a vaga já vem por aquele
parser e a linha da PCI é só confirmação; quando não é, é candidato a
banca nova pra fila de triagem.

Uso: python scripts/descobrir_pci.py
Requer DATABASE_URL no ambiente (só pra ler `municipios`, não escreve).
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests
from bs4 import BeautifulSoup

from notifica_vagas_scraper import db
from notifica_vagas_scraper.fontes import fgv

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
UFS = ("mg", "sp")
PCI_HOST = "www.pciconcursos.com.br"
HOSTS_IGNORADOS = {
    PCI_HOST,
    "t.me",
    "linkedin.com",
    "www.linkedin.com",
    "facebook.com",
    "www.facebook.com",
    "x.com",
    "instagram.com",
    "www.instagram.com",
    "whatsapp.com",
    "web.whatsapp.com",
    "api.whatsapp.com",
}


def buscar_noticias(uf_lower: str) -> list[tuple[str, str]]:
    """[(url, titulo), ...] extraído do índice de notícias da UF."""
    resposta = requests.get(
        f"https://www.pciconcursos.com.br/concursos/{uf_lower}/",
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resposta.raise_for_status()
    return re.findall(
        r'href="(https://www\.pciconcursos\.com\.br/noticias/[^"]+)"[^>]*>([^<]+)<',
        resposta.text,
    )


def casar_municipio_com_guarda_de_uf(titulo: str, uf_alvo: str, municipios: list[tuple[str, str]]):
    """`fgv.encontrar_municipio` sozinho ainda deixa passar falso positivo
    específico da PCI: título quase sempre tem um marcador "- UF"
    explícito (ex: "Governo do Tocantins - TO retifica..."), e quando
    esse marcador existe mas não bate com o UF do município casado, é
    sinal forte de coincidência de nome — achado real rodando contra
    produção: "Tocantins" (MG) batendo em "Governo do Tocantins - TO",
    "Abaeté" (MG) batendo dentro de "Abaetetuba - PA" (sem separador de
    palavra entre os dois nomes, caso que a guarda de prefixo do
    `encontrar_municipio` não cobre)."""
    match = fgv.encontrar_municipio(titulo, municipios)
    if not match:
        return None
    marcadores_uf = re.findall(r"-\s*([A-Z]{2})\b", titulo)
    if marcadores_uf and match[1] not in marcadores_uf:
        return None
    return match


def buscar_links_externos(url_noticia: str) -> list[str]:
    """Domínios de referências externas citadas no corpo da notícia
    (link do edital na banca/prefeitura, quando a PCI não hospeda o PDF —
    ver `parse_external_references` no protótipo de referência)."""
    resposta = requests.get(url_noticia, headers={"User-Agent": USER_AGENT}, timeout=20)
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.text, "html.parser")
    hosts: dict[str, None] = {}
    for anchor in soup.select("a[href]"):
        url = urljoin(url_noticia, str(anchor["href"]))
        host = urlparse(url).netloc.lower()
        if not host or host in HOSTS_IGNORADOS:
            continue
        hosts.setdefault(host, None)
    return list(hosts)


def main() -> None:
    conn = db.conectar()
    try:
        municipios = db.listar_nomes_municipios(conn, ufs=["MG", "SP"])
    finally:
        conn.close()

    achados = []
    vistos = set()
    for uf_lower in UFS:
        for url, titulo_bruto in buscar_noticias(uf_lower):
            titulo = titulo_bruto.strip()
            match = casar_municipio_com_guarda_de_uf(titulo, uf_lower.upper(), municipios)
            if not match:
                continue
            chave = (match[0], match[1], url)
            if chave in vistos:
                continue
            vistos.add(chave)
            achados.append({"municipio": match[0], "uf": match[1], "titulo": titulo, "url": url})

    for a in achados:
        try:
            hosts = buscar_links_externos(a["url"])
        except requests.RequestException as exc:
            print(f"aviso: falha buscando links externos de {a['url']}: {exc}", file=sys.stderr)
            hosts = []
        a["links_externos"] = ";".join(hosts)

    escritor = csv.DictWriter(sys.stdout, fieldnames=["municipio", "uf", "titulo", "url", "links_externos"])
    escritor.writeheader()
    for a in achados:
        escritor.writerow(a)

    print(f"\n{len(achados)} notícia(s) da PCI casada(s) com município de MG/SP.", file=sys.stderr)


if __name__ == "__main__":
    main()
