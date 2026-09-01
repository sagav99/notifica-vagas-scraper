# Notifica Vagas — Scraper

Coleta de vagas de concurso público (SP e MG) para o projeto
[Notifica Vagas](https://github.com/sagav99/notifica-vagas) (repositório
privado — site, banco de dados e lógica de negócio ficam lá).

Este repositório é público de propósito: usa o cron do GitHub Actions com
minutos ilimitados (disponível só em repositório público) para rodar a
coleta periodicamente sem custo. Por isso ele contém só código técnico de
coleta — nenhuma decisão de produto, análise de fonte ou documento
estratégico vive aqui.

## O que faz

Coleta HTML/PDF de fontes de vagas de concurso público (bancas, prefeituras,
diários oficiais) e grava o resultado bruto no Supabase do projeto principal,
para posterior verificação/enriquecimento via Gemini API.

## Stack

Python. `requests` + `beautifulsoup4` pra HTML, `pdfplumber` pra PDF,
`psycopg` pra escrever direto no Postgres via `DATABASE_URL` (mesmo padrão
do runner de migrations do repo principal — conexão direta, ignora RLS,
sem precisar de `supabase-py`). Testes com `pytest`.

## Estrutura

- `src/notifica_vagas_scraper/db.py` — upsert de município/fonte, insert de
  vaga+evidência com dedup (município + órgão + cargo + número de edital).
- `src/notifica_vagas_scraper/ibge.py` — lookup de código IBGE via API
  pública do IBGE (não é scraping adversarial, é referência oficial).
- `src/notifica_vagas_scraper/fontes/` — um parser por fonte:
  `dom_amm_mg.py` (Diário Oficial dos Municípios Mineiros / AMM-MG),
  `imeso.py` (IMESO, banca que atende ~111 entidades de MG — descoberta
  automática via `/edital`, sem lista curada) e `fgv.py` (FGV Conhecimento
  — listagem sem metadado de UF, casa título contra catálogo de município;
  cargo/salário só existem no PDF do edital, ver `gemini_pdf.py`).
- `src/notifica_vagas_scraper/gemini_pdf.py` — extração de cargo/salário/
  vagas de PDF de edital via Gemini (document understanding, não OCR
  manual) — usada só quando a fonte não expõe isso em HTML.
- `src/notifica_vagas_scraper/fontes_conhecidas.py` — lista curada de
  matérias já mapeadas manualmente (a busca automatizada do DOM/AMM-MG
  ainda não está implementada — formulário com token CSRF, ver
  investigação no repo principal).
- `scripts/rodar.py` — entrypoint do cron do DOM/AMM-MG: busca cada
  matéria conhecida, extrai vagas, grava no Supabase.
- `scripts/rodar_imeso.py` — entrypoint do cron da IMESO: descobre todos
  os editais com inscrição aberta automaticamente, extrai vagas, grava.
- `scripts/rodar_fgv.py` — entrypoint do cron da FGV: percorre a listagem
  paginada, casa contra município, extrai vagas do PDF via Gemini.
- `src/notifica_vagas_scraper/fontes/instar.py` + `scripts/rodar_instar.py`
  — entrypoint do cron da plataforma Instar Tecnologia (achado
  2026-09-01): 230 municípios de MG confirmados
  (`dados/municipios_instar.csv`) com endpoint JSON de dados abertos de
  concurso (`/portal/dados-abertos/concursos/<ano>`), sem autenticação.
  `descricao` de cada item é texto/HTML livre sem estrutura fixa — extrai
  via Gemini (`gemini_texto.py`), igual FGV faz com PDF.
- `src/notifica_vagas_scraper/quota_gemini.py` — rastreador de cota
  diária compartilhado entre gemini_pdf.py/gemini_texto.py/revisao_ia.py:
  troca de gemini-3.5-flash-lite pra gemini-3.1-flash-lite depois de
  ~470 chamadas no dia (cota separada, ganha outras ~470/dia).
- `src/notifica_vagas_scraper/revisao_ia.py` + `scripts/revisar_vagas.py`
  — entrypoint do cron que decide aprovar/rejeitar cada vaga pendente via
  Gemini (1 chamada por vaga, sem humano no loop). Ver
  `docs/revisao_automatica_gemini.md` no repo principal.
- `scripts/importar_municipios.py` / `importar_municipios_ibge.py` —
  upsert em lote do catálogo de municípios (CSV ou API do IBGE →
  `public.municipios`).
- `src/notifica_vagas_scraper/descoberta_prefeitura.py` +
  `scripts/descobrir_urls_prefeitura.py` — descoberta heurística de URL
  oficial de prefeitura (`<slug>.<uf>.gov.br`, com verificação de conteúdo),
  sem depender de busca externa nem de investigação manual por cidade.
- `tests/fixtures/` — cópias de páginas reais salvas por investigação
  (subagente `pesquisador-fonte` do repo principal), usadas pelos testes
  pra não depender do site ao vivo.

## Status

3 fontes rodando ponta a ponta contra o Supabase real: DOM/AMM-MG (lista
curada manual, 1 município), IMESO (descoberta automática, 2 municípios
via editais com inscrição aberta) e FGV (descoberta automática por match
de município no título + extração de PDF via Gemini, 2 municípios).
Catálogo `municipios` completo pra MG+SP, com `url_prefeitura` descoberto
heuristicamente pra ~83%. Falta: busca automatizada por município no
DOM/AMM-MG, mais fontes.

## Segredos

Nenhum segredo neste repositório. Credenciais (`DATABASE_URL`,
`GEMINI_API_KEY`, `RESEND_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`) ficam em
GitHub Secrets do workflow do Actions, nunca em arquivo versionado.
