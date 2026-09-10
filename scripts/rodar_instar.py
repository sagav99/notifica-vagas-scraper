#!/usr/bin/env python3
"""Entrypoint do cron pra fonte Instar Tecnologia: para cada município com
essa plataforma confirmada (`notifica_vagas_scraper.fontes.instar`), busca
o endpoint de dados abertos de concursos do ano corrente, filtra os itens
"Aberto" e usa Gemini pra extrair vaga(s) de cada `descricao` (texto/HTML
livre, sem estrutura fixa confiável — diferente de dom_amm_mg/IMESO).

**2ª camada** (plano aceito 2026-09-09, achado de layout 2026-09-10, ver
TAREFAS.md): além do endpoint de dados abertos, varre as categorias
relevantes de `/portal/editais` (`_categoria_relevante` — `/portal/
editais` sozinho sempre devolve 0 itens, o CMS exige escolher categoria
do menu) e `/portal/noticias` de cada município, filtra por
palavra-chave (`PALAVRAS_GATILHO_CONCURSO`) — sem Gemini nessa varredura
em si, só aciona extração pro item que bater palavra-chave e não tiver
aparecido já no endpoint de dados abertos (dedup por título, ver
`buscar_itens_layer2`). Cobre os 2 casos reais que motivaram o plano:
prefeitura que nunca preenche "dados abertos" (Confins) e prefeitura que
preenche, mas numa categoria que o endpoint não devolve (Sapucaí-Mirim,
"Chamamento Público" — mesma categoria some do lado de `/portal/editais`
em Confins, achado ao vivo 2026-09-10).

Uso: python scripts/rodar_instar.py
Requer DATABASE_URL e GEMINI_API_KEY no ambiente.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from notifica_vagas_scraper import db, gemini_texto, gemini_util
from notifica_vagas_scraper.fontes import instar

USER_AGENT = "Mozilla/5.0 (compatible; NotificaVagasBot/0.1; +https://github.com/sagav99/notifica-vagas-scraper)"
FONTE_TIPO = "oficial"
DESCRICAO_MAX_CHARS = 8000

#: prioridade do produto é saúde/médicos (ver TAREFAS.md, decisão do
#: usuário 2026-09-01) — achado real na mesma data: a cota diária do
#: Gemini estourou no meio do lote (ordem alfabética simples) e municípios
#: com vaga de médico real de verdade (Carmo do Rio Claro, Guaraciama,
#: Varginha) ficaram de fora até o próximo ciclo. Processar município com
#: sinal de saúde primeiro garante que, se a cota estourar nesta execução,
#: o prejuízo caia nos itens de menor prioridade, não nos de médico.
PALAVRAS_SAUDE = (
    "medic",
    "médic",
    "enfermeir",
    "saude",
    "saúde",
    "odont",
    "fisioterap",
    "psicolog",
    "nutricion",
    "farmaceut",
    "farmac",
    "fonoaudiolog",
)


def _tem_sinal_saude(itens: list[dict]) -> bool:
    for item in itens:
        texto = ((item.get("titulo") or "") + " " + (item.get("descricao") or "")).lower()
        if any(palavra in texto for palavra in PALAVRAS_SAUDE):
            return True
    return False


#: 2ª camada (ver docstring do módulo): gatilho pra decidir se um item de
#: `/portal/editais`/`/portal/noticias` merece ir pro Gemini — mais amplo
#: que PALAVRAS_SAUDE de propósito (concurso/PSS de qualquer área ainda
#: interessa pro produto, saúde é prioridade, não exclusividade). Sem
#: "edital" solto (achado real 2026-09-10: toda listagem de `/portal/
#: editais` tem "edital" no título, licitação/compra incluída — usar como
#: gatilho não filtra nada e estoura cota do Gemini à toa).
PALAVRAS_GATILHO_CONCURSO = (
    "concurso",
    "pss",
    "processo seletivo",
    "chamamento",
    "credenciamento",
    "convocação",
    "convocacao",
)

#: categoria de `/portal/editais` só entra na 2ª camada se o nome bater
#: aqui — sem isso, "Licitações"/"Compra Direta" (sempre presentes,
#: sempre com "edital" no título de cada item) inundam a varredura com
#: Gemini call sem sinal nenhum de concurso público (achado real
#: 2026-09-10, ver PALAVRAS_GATILHO_CONCURSO acima).
PALAVRAS_CATEGORIA_RELEVANTE = ("concurso", "seletivo", "emprego", "vaga", "chamamento", "credenciamento")
PALAVRAS_CATEGORIA_EXCLUIR = ("licita", "pregão", "pregao", "dispensa", "compra")


def _categoria_relevante(categoria: instar.CategoriaEditais) -> bool:
    nome = categoria.nome.lower()
    if any(palavra in nome for palavra in PALAVRAS_CATEGORIA_EXCLUIR):
        return False
    return any(palavra in nome for palavra in PALAVRAS_CATEGORIA_RELEVANTE)


def _tem_palavra_gatilho(item: instar.ItemPortal) -> bool:
    texto = f"{item.titulo} {item.descricao}".lower()
    return any(palavra in texto for palavra in PALAVRAS_GATILHO_CONCURSO)


def _extrair_id_portal(url_item: str) -> str:
    """`/portal/editais/0/3/335/...` -> `"335"`; sem match (layout
    inesperado), cai pro slug da própria URL — continua determinístico,
    só não tão legível."""
    match = re.search(r"/portal/(?:editais|noticias)/\d+/\d+/(\d+)/", url_item)
    return match.group(1) if match else _slug(url_item)


def _parsear_data_iso(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def _requisitar(url: str) -> requests.Response | None:
    try:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, verify=True)
    except requests.exceptions.SSLError:
        resposta = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, verify=False)
    except requests.exceptions.RequestException as exc:
        print(f"  aviso: erro de rede em {url}: {exc}")
        return None

    if resposta.status_code != 200:
        print(f"  aviso: HTTP {resposta.status_code} em {url}")
        return None
    return resposta


def buscar_json_concursos(url_prefeitura: str, ano: int) -> dict | None:
    resposta = _requisitar(instar.url_dados_abertos(url_prefeitura, ano))
    if resposta is None:
        return None
    try:
        return resposta.json()
    except ValueError:
        print(f"  aviso: resposta não é JSON em {resposta.url}")
        return None


def buscar_html(url: str) -> str | None:
    resposta = _requisitar(url)
    return resposta.text if resposta is not None else None


def _urls_listagem_editais(url_prefeitura: str) -> list[str]:
    """`/portal/editais` sozinho sempre devolve 0 itens (achado real
    2026-09-10, ver `instar.listar_categorias_editais`) — precisa
    descobrir as categorias do menu (variam por município: Confins tem
    Editais de Licitação/Concursos/Compra Direta/Chamamento Público) e
    visitar só as relevantes pra concurso público (`_categoria_relevante`
    — sem esse filtro, Licitações/Compra Direta inundam a 2ª camada sem
    sinal nenhum de vaga, ver PALAVRAS_CATEGORIA_RELEVANTE). Sem categoria
    nenhuma encontrada (layout diferente do esperado), cai pra tentar a
    URL base mesmo assim."""
    url_base = instar.url_editais(url_prefeitura)
    html_base = buscar_html(url_base)
    if html_base is None:
        return []
    categorias = [c for c in instar.listar_categorias_editais(html_base) if _categoria_relevante(c)]
    if not categorias:
        return [url_base]
    return [f"{url_base}/{categoria.id}" for categoria in categorias]


def buscar_itens_layer2(url_prefeitura: str, itens_layer1: list[dict]) -> list[instar.ItemPortal]:
    """2ª camada: varre todas as categorias de `/portal/editais` e
    `/portal/noticias`, filtra por `PALAVRAS_GATILHO_CONCURSO` e descarta
    o que o endpoint de dados abertos (`itens_layer1`) já trouxe (dedup
    por título — não temos número de processo pra comparar antes de
    rodar o Gemini, só o título)."""
    ja_vistos = {_slug(item.get("titulo") or "") for item in itens_layer1}
    urls = _urls_listagem_editais(url_prefeitura) + [instar.url_noticias(url_prefeitura)]

    encontrados: list[instar.ItemPortal] = []
    for url in urls:
        html = buscar_html(url)
        if html is None:
            continue
        try:
            itens = instar.listar_itens_portal(html, url_prefeitura)
        except Exception as exc:
            print(f"  aviso: falha lendo listagem {url}: {exc}")
            continue
        for item in itens:
            slug_titulo = _slug(item.titulo)
            if slug_titulo in ja_vistos or not _tem_palavra_gatilho(item):
                continue
            ja_vistos.add(slug_titulo)
            encontrados.append(item)
    return encontrados


def processar_municipio(conn, municipio: instar.MunicipioInstar, fonte_id: str, itens: list[dict]) -> int:
    codigo_ibge = municipio.codigo_ibge
    url_evidencia = municipio.url_prefeitura.rstrip("/") + "/portal/editais"

    total = 0
    for item in itens:
        titulo = item.get("titulo") or ""
        descricao = (item.get("descricao") or "")[:DESCRICAO_MAX_CHARS]
        if not descricao:
            continue

        try:
            extraido = gemini_texto.extrair_vagas_de_texto(titulo, descricao)
        except gemini_texto.ErroExtracaoGemini as exc:
            print(f"  aviso: falha ao extrair '{titulo[:60]}': {exc}")
            continue

        if not extraido.get("vagas"):
            continue

        data_publicacao = _parsear_data_iso(extraido.get("data_publicacao"))
        inscricoes_inicio = _parsear_data_iso(extraido.get("inscricoes_inicio"))
        inscricoes_fim = _parsear_data_iso(extraido.get("inscricoes_fim"))
        # Quando o resumo publicado não nomeia a secretaria/órgão específico,
        # cai pro nome da prefeitura do município — não é dado inventado (o
        # item já vem do portal oficial daquele município), e evita rejeitar
        # vaga real só por causa de um campo que o Gemini não conseguiu
        # extrair do texto truncado (achado analisando vagas rejeitadas em
        # produção, 2026-09-01: maioria das rejeições da fonte Instar era
        # só isso).
        orgao = extraido.get("orgao") or f"Prefeitura Municipal de {municipio.nome}/{municipio.uf}"
        numero_edital = extraido.get("numero_edital") or item.get("numeroEdital")
        numero_processo = item.get("numeroProcesso")
        tipo_oportunidade = extraido.get("tipo_oportunidade")

        for vaga in extraido["vagas"]:
            cargo = vaga.get("cargo")
            if not cargo:
                continue
            identificador_externo = f"{numero_processo}-{_slug(titulo)}-{_slug(cargo)}"
            resultado = db.inserir_vaga_com_evidencia(
                conn,
                fonte_id=fonte_id,
                municipio_id=codigo_ibge,
                identificador_externo=identificador_externo,
                orgao=orgao,
                cargo=cargo,
                salario=Decimal(str(vaga["salario"])) if vaga.get("salario") is not None else None,
                salario_tipo=vaga.get("salario_tipo"),
                tipo_oportunidade=tipo_oportunidade,
                numero_edital=numero_edital,
                data_publicacao=data_publicacao,
                inscricoes_inicio=inscricoes_inicio,
                inscricoes_fim=inscricoes_fim,
                status="aberta",
                resumo=f"{item.get('modalidade') or 'Processo seletivo'} — {titulo}",
                url_evidencia=url_evidencia,
                tipo_documento="pagina_html",
                texto_extraido=None,
                **gemini_util.campos_estruturados_extras(extraido, vaga),
            )
            novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
            print(f"  {cargo}: vaga_id={resultado['vaga_id']} ({novo})")
            total += 1

    return total


def processar_item_layer2(
    conn, municipio: instar.MunicipioInstar, fonte_id: str, item: instar.ItemPortal
) -> int:
    """Mesmo fluxo de `processar_municipio`, pro item vindo de
    `/portal/editais`/`/portal/noticias` (2ª camada) — sem `numeroEdital`/
    `numeroProcesso` estruturado (só existem no JSON de dados abertos),
    então o identificador externo usa o id numérico da própria URL do
    item, que é estável entre execuções."""
    if item.situacao and item.situacao.strip().lower() != instar.SITUACAO_ABERTA:
        return 0

    descricao = item.descricao[:DESCRICAO_MAX_CHARS]
    if not descricao:
        return 0

    try:
        extraido = gemini_texto.extrair_vagas_de_texto(item.titulo, descricao)
    except gemini_texto.ErroExtracaoGemini as exc:
        print(f"  aviso: falha ao extrair '{item.titulo[:60]}' (2ª camada): {exc}")
        return 0

    if not extraido.get("vagas"):
        return 0

    data_publicacao = _parsear_data_iso(extraido.get("data_publicacao"))
    inscricoes_inicio = _parsear_data_iso(extraido.get("inscricoes_inicio"))
    inscricoes_fim = _parsear_data_iso(extraido.get("inscricoes_fim"))
    orgao = extraido.get("orgao") or f"Prefeitura Municipal de {municipio.nome}/{municipio.uf}"
    numero_edital = extraido.get("numero_edital")
    tipo_oportunidade = extraido.get("tipo_oportunidade")
    id_item = _extrair_id_portal(item.url)

    total = 0
    for vaga in extraido["vagas"]:
        cargo = vaga.get("cargo")
        if not cargo:
            continue
        identificador_externo = f"portal2-{id_item}-{_slug(cargo)}"
        resultado = db.inserir_vaga_com_evidencia(
            conn,
            fonte_id=fonte_id,
            municipio_id=municipio.codigo_ibge,
            identificador_externo=identificador_externo,
            orgao=orgao,
            cargo=cargo,
            salario=Decimal(str(vaga["salario"])) if vaga.get("salario") is not None else None,
            salario_tipo=vaga.get("salario_tipo"),
            tipo_oportunidade=tipo_oportunidade,
            numero_edital=numero_edital,
            data_publicacao=data_publicacao,
            inscricoes_inicio=inscricoes_inicio,
            inscricoes_fim=inscricoes_fim,
            status="aberta",
            resumo=f"[2ª camada] {item.titulo}",
            url_evidencia=item.url,
            tipo_documento="pagina_html",
            texto_extraido=None,
            **gemini_util.campos_estruturados_extras(extraido, vaga),
        )
        novo = "nova evidência" if resultado["evidencia_id"] else "já existente (dedup)"
        print(f"  {cargo}: vaga_id={resultado['vaga_id']} ({novo}) [2ª camada]")
        total += 1

    return total


def _tem_sinal_saude_portal(itens: list[instar.ItemPortal]) -> bool:
    return _tem_sinal_saude([{"titulo": item.titulo, "descricao": item.descricao} for item in itens])


def main() -> None:
    conn = db.conectar()
    try:
        municipios = instar.listar_municipios_instar()
        print(f"{len(municipios)} município(s) com plataforma Instar confirmada.")

        # 1ª passada: só busca dados abertos + as 2 listagens da 2ª camada
        # (sem chamar Gemini) pra poder priorizar quem tem sinal de saúde
        # antes de gastar cota — ver PALAVRAS_SAUDE acima. Município com
        # dados abertos vazio (ex: Confins, nunca preenche) não é mais
        # descartado aqui: a 2ª camada roda pra TODO município mesmo assim,
        # é exatamente o caso que ela existe pra cobrir (ver docstring do
        # módulo). try/except por município aqui também (achado de code
        # review, 2026-09-02): payload malformado de 1 entre 231 municípios
        # (ex: JSON que não é um dict) não pode derrubar a passada inteira
        # antes de qualquer Gemini rodar — isso anularia justo a proteção
        # que essa priorização existe pra dar.
        trabalho: list[tuple[instar.MunicipioInstar, list[dict], list[instar.ItemPortal]]] = []
        for municipio in municipios:
            itens_layer1: list[dict] = []
            try:
                payload = buscar_json_concursos(municipio.url_prefeitura, datetime.now().year)
                if payload is not None:
                    itens_layer1 = instar.listar_itens_abertos(payload)
            except Exception as exc:
                print(f"  aviso: falha buscando dados abertos de {municipio.nome}/{municipio.uf}: {exc}")

            try:
                itens_layer2 = buscar_itens_layer2(municipio.url_prefeitura, itens_layer1)
            except Exception as exc:
                print(f"  aviso: falha na 2ª camada de {municipio.nome}/{municipio.uf}: {exc}")
                itens_layer2 = []

            if itens_layer1 or itens_layer2:
                trabalho.append((municipio, itens_layer1, itens_layer2))

        trabalho.sort(key=lambda t: not (_tem_sinal_saude(t[1]) or _tem_sinal_saude_portal(t[2])))
        com_sinal_saude = sum(
            1 for _, l1, l2 in trabalho if _tem_sinal_saude(l1) or _tem_sinal_saude_portal(l2)
        )
        com_2a_camada = sum(1 for _, _, l2 in trabalho if l2)
        print(
            f"{len(trabalho)} município(s) com processo aberto "
            f"({com_sinal_saude} com sinal de saúde, processados primeiro; "
            f"{com_2a_camada} com achado só na 2ª camada de varredura)."
        )

        total_geral = 0
        for municipio, itens_layer1, itens_layer2 in trabalho:
            print(f"Processando {municipio.nome}/{municipio.uf}...")
            try:
                # savepoint por município: erro num município não deixa a
                # transação inteira do lote em estado abortado pros próximos.
                with conn.transaction():
                    fonte_id = db.upsert_fonte(
                        conn,
                        nome=f"Portal {municipio.nome}/{municipio.uf} (Instar)",
                        url=municipio.url_prefeitura,
                        tipo=FONTE_TIPO,
                        uf=municipio.uf,
                    )
                    total_geral += processar_municipio(conn, municipio, fonte_id, itens_layer1)
                    for item in itens_layer2:
                        total_geral += processar_item_layer2(conn, municipio, fonte_id, item)
            except Exception as exc:  # nunca deixar 1 município derrubar o lote inteiro
                print(f"  ERRO processando {municipio.nome}/{municipio.uf}: {exc}")

        conn.commit()
        print(f"\nOk. {total_geral} vaga(s) processada(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    with db.rastrear_execucao("rodar_instar.py"):
        main()
