#!/bin/zsh
# Wrapper pra rodar scripts/completude_codex.py automaticamente 1x por dia
# via launchd (ver scripts/com.medvagas.completude-codex.plist) — pedido
# do usuário, 2026-09-16: preencher os campos faltando das ~618 vagas
# médicas aprovadas incompletas usando a cota da assinatura Codex/ChatGPT
# já autenticada nesta máquina (`codex login`), sem precisar rodar na mão
# todo dia.
#
# DATABASE_URL nunca fica commitado aqui — lido em runtime do
# .env.local do repo principal (fora deste repo, git-ignorado lá).
# launchd roda com PATH mínimo, sem carregar .zshrc/.zprofile — por isso
# o PATH do nvm (onde o binário `codex` vive) é montado explicitamente
# abaixo em vez de depender do shell interativo do usuário.

set -euo pipefail

REPO_SCRAPER="/Users/herminioneto/notifica-vagas-scraper"
REPO_PRINCIPAL="/Users/herminioneto/notifica-vagas"
LOG_DIR="$REPO_SCRAPER/logs"
LIMITE="${COMPLETUDE_CODEX_LIMITE:-15}"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/completude-codex-$(date +%Y-%m-%d).log"

export PATH="$HOME/.nvm/versions/node/v24.18.0/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

{
  echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) — iniciando completude_codex.py (limite=$LIMITE) ====="

  DATABASE_URL="$(grep '^DATABASE_URL=' "$REPO_PRINCIPAL/.env.local" | cut -d= -f2-)"
  if [ -z "$DATABASE_URL" ]; then
    echo "ERRO: DATABASE_URL não encontrada em $REPO_PRINCIPAL/.env.local"
    exit 1
  fi
  export DATABASE_URL

  cd "$REPO_SCRAPER"
  source .venv/bin/activate
  python scripts/completude_codex.py --limite "$LIMITE"

  echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) — terminou ====="
} >> "$LOG_FILE" 2>&1

# mantém só os últimos 30 dias de log, mesmo padrão do loop-continuar-tarefas
find "$LOG_DIR" -name 'completude-codex-*.log' -mtime +30 -delete 2>/dev/null || true
