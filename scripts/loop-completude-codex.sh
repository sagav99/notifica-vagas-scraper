#!/bin/zsh
# Wrapper pra rodar scripts/completude_codex.py em CONTÍNUO via launchd
# (ver scripts/com.medvagas.completude-codex.plist, KeepAlive=true) —
# pedido do usuário, 2026-09-16: "tire esse limite, e pra ficar rodando
# direto ... até acabar os tokens ... quando fica sem net volta sozinho
# etc nao perde progresso". O script Python em si já é o loop (nunca
# termina sozinho, só em SIGINT/SIGTERM) — este wrapper só prepara o
# ambiente e existe pra caso o processo Python morra por qualquer
# motivo: `launchd` com KeepAlive=true sobe ele de novo automaticamente
# (retry em falha de rede, queda do processo etc. já é coberto por
# isso, sem lógica extra aqui).
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

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/completude-codex-$(date +%Y-%m-%d).log"

export PATH="$HOME/.nvm/versions/node/v24.18.0/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

{
  echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) — iniciando completude_codex.py (contínuo) ====="

  DATABASE_URL="$(grep '^DATABASE_URL=' "$REPO_PRINCIPAL/.env.local" | cut -d= -f2-)"
  if [ -z "$DATABASE_URL" ]; then
    echo "ERRO: DATABASE_URL não encontrada em $REPO_PRINCIPAL/.env.local"
    exit 1
  fi
  export DATABASE_URL

  cd "$REPO_SCRAPER"
  source .venv/bin/activate
  # LOG_FILE fica fixo no dia em que o processo SUBIU (não vira à meia-
  # noite sozinho) -- como o processo roda contínuo por horas/dias, o
  # arquivo só troca de fato quando o launchd reinicia o wrapper (queda
  # de rede, kill, reboot). Isso é aceitável: `tail -f` no arquivo do
  # dia do último start sempre mostra o log corrente.
  # -u (unbuffered): sem isso, print() vai pra um buffer de bloco quando
  # a saída é redirecionada pra arquivo (não é TTY) -- o log só grava de
  # verdade quando o buffer enche ou o processo termina, então um
  # `tail -f` fica "parado" mesmo com o processo trabalhando normal
  # (achado real, 2026-09-17, conferindo progresso com `tail` vs. o
  # dado real já gravado no banco).
  python -u scripts/completude_codex.py

  echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) — processo terminou (launchd deve reiniciar se KeepAlive=true) ====="
} >> "$LOG_FILE" 2>&1

find "$LOG_DIR" -name 'completude-codex-*.log' -mtime +30 -delete 2>/dev/null || true
