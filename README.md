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

## Status

Ainda não implementado — só o esqueleto do repositório. Ver histórico de
commits para o progresso.

## Segredos

Nenhum segredo neste repositório. Credenciais (Supabase, Gemini) ficam em
GitHub Secrets do workflow do Actions, nunca em arquivo versionado.
