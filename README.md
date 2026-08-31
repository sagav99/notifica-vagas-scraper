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
- `src/notifica_vagas_scraper/fontes/` — um parser por fonte (hoje só
  `dom_amm_mg.py`, Diário Oficial dos Municípios Mineiros / AMM-MG).
- `src/notifica_vagas_scraper/fontes_conhecidas.py` — lista curada de
  matérias já mapeadas manualmente (a busca automatizada do DOM/AMM-MG
  ainda não está implementada — formulário com token CSRF, ver
  investigação no repo principal).
- `scripts/rodar.py` — entrypoint do cron: busca cada matéria conhecida,
  extrai vagas, grava no Supabase.
- `tests/fixtures/` — cópias de páginas reais salvas por investigação
  (subagente `pesquisador-fonte` do repo principal), usadas pelos testes
  pra não depender do site ao vivo.

## Status

Fatia fina funcionando: 1 fonte (DOM/AMM-MG), 1 município (Pedra
Dourada/MG), rodando ponta a ponta contra o Supabase real. Falta: busca
automatizada por município (hoje é lista curada manual), mais fontes,
camada de auditoria via Gemini.

## Segredos

Nenhum segredo neste repositório. Credenciais (`DATABASE_URL`,
`GEMINI_API_KEY`, `RESEND_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`) ficam em
GitHub Secrets do workflow do Actions, nunca em arquivo versionado.
