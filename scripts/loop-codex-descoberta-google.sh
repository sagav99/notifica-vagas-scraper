#!/usr/bin/env bash
# Loop supervisor que roda o Codex sem parar, retomando sozinho depois de
# esgotar a cota (reset por volta de 5h, conforme observado) ou depois do
# Mac ter ficado desligado/dormindo. Segue o mesmo padrão do
# scripts/loop-continuar-tarefas.sh do repo notifica-vagas (ver
# docs/operacao_continua.md lá), adaptado pro Codex CLI e pra uma tarefa
# ÚNICA (não um backlog infinito) — por isso o loop para sozinho quando o
# Codex sinaliza que terminou (arquivo-marcador), em vez de rodar pra
# sempre.
#
# Uso manual: scripts/loop-codex-descoberta-google.sh
# Parar de propósito: matar o PID do lock (ver LOCK_FILE abaixo) ou
# `launchctl unload`/`bootout` do plist, se estiver instalado sob launchd.
#
# Comando real, confirmado contra `codex exec --help` (codex-cli 0.153.4):
# primeiro ciclo manda o prompt inteiro; ciclos seguintes usam
# `codex exec resume --last` pra continuar a mesma sessão em vez de
# repetir a instrução do zero. `--sandbox workspace-write --approve-for-me`
# deixa ele editar/commitar sem ficar esperando aprovação interativa (não
# tem humano pra responder rodando em loop). Ajuste PADRAO_LIMITE_COTA se
# a mensagem real de cota estourada não bater com o grep abaixo (mesma
# ressalva do loop do Claude: heurística de texto precisa ser validada
# contra a mensagem real na primeira vez que acontecer de verdade).

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

# Arquivo que a INSTRUÇÃO do Codex manda ele criar na raiz do repo quando
# a tarefa inteira estiver concluída (todos os itens da seção "Tarefa" do
# INSTRUCAO_CODEX_DESCOBERTA_GOOGLE_SEARCH.md feitos, testes passando,
# commit/push dados). O loop para de tentar assim que esse arquivo existir.
ARQUIVO_CONCLUSAO="$PROJECT_DIR/.codex-descoberta-google-concluido"

# Prompt que é reenviado a cada ciclo — o Codex CLI precisa conseguir
# retomar de onde parou (via resume de sessão, se seu Codex suportar; caso
# não suporte resume automático, o prompt abaixo pede pra ele checar o
# estado atual do repo antes de repetir trabalho já feito).
ARQUIVO_INSTRUCAO="$PROJECT_DIR/INSTRUCAO_CODEX_DESCOBERTA_GOOGLE_SEARCH.md"

LOCK_FILE="$PROJECT_DIR/.codex-loop.pid"
LOG_DIR="$PROJECT_DIR/logs"
RUNS_DIR="$LOG_DIR/runs"

# Marca que já existe uma sessão do Codex iniciada pra essa tarefa — a
# partir da 2a vez, o loop usa `resume --last` em vez de reenviar o
# prompt inteiro (evita repetir trabalho já feito quando retoma depois
# de esgotar cota).
SESSAO_INICIADA="$PROJECT_DIR/.codex-loop-sessao-iniciada"

# Configurável via env, valores padrão em segundos.
SLEEP_ERRO="${LOOP_SLEEP_ERRO:-600}"                     # erro inesperado, 10min
SLEEP_LIMITE_COTA="${LOOP_SLEEP_LIMITE_COTA:-19800}"     # cota esgotada, 5h30 (5h + 30min de folga)
MAX_RUNS_GUARDADOS="${LOOP_MAX_RUNS_GUARDADOS:-100}"
MAX_DIAS_LOG="${LOOP_MAX_DIAS_LOG:-30}"

# Padrões de texto que indicam cota/limite de uso estourado — ajuste
# conforme a mensagem real do seu Codex CLI (case-insensitive).
PADRAO_LIMITE_COTA="usage limit|rate limit|quota|429|try again in|limite de uso|cota esgotada|resets? (at|in)"

mkdir -p "$LOG_DIR" "$RUNS_DIR" "$(dirname "$LOCK_FILE")"

log() {
  local arquivo_hoje="$LOG_DIR/loop-codex-$(date +%F).log"
  local linha
  linha="$(date '+%Y-%m-%d %H:%M:%S %z') [$$] $*"
  echo "$linha" >> "$arquivo_hoje"
  ln -sf "$(basename "$arquivo_hoje")" "$LOG_DIR/loop-codex.log"
  find "$LOG_DIR" -maxdepth 1 -name 'loop-codex-*.log' -mtime "+${MAX_DIAS_LOG}" -delete 2>/dev/null || true
}

adquirir_lock() {
  if [ -f "$LOCK_FILE" ]; then
    local pid_antigo
    pid_antigo="$(cat "$LOCK_FILE" 2>/dev/null || true)"
    if [ -n "$pid_antigo" ] && kill -0 "$pid_antigo" 2>/dev/null; then
      log "Já existe uma instância rodando (PID $pid_antigo) — abortando."
      exit 0
    fi
    log "Lock órfão encontrado (PID $pid_antigo morto) — assumindo o lock."
  fi
  echo $$ > "$LOCK_FILE"
}

liberar_lock() {
  local pid_no_lock
  pid_no_lock="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [ "$pid_no_lock" = "$$" ]; then
    rm -f "$LOCK_FILE"
  fi
}

trap 'log "Sinal de término recebido — encerrando o loop de propósito."; liberar_lock; exit 0' SIGTERM SIGINT

dormir_interrompivel() {
  local segundos="$1"
  sleep "$segundos" &
  wait $!
}

podar_runs_antigos() {
  local total
  total="$(find "$RUNS_DIR" -maxdepth 1 -name '*.log' | wc -l | tr -d ' ')"
  if [ "$total" -gt "$MAX_RUNS_GUARDADOS" ]; then
    find "$RUNS_DIR" -maxdepth 1 -name '*.log' -print0 | xargs -0 ls -t | tail -n +"$((MAX_RUNS_GUARDADOS + 1))" | xargs -I{} rm -f {}
  fi
}

executar_um_ciclo() {
  local ts
  ts="$(date +%Y%m%dT%H%M%S)"
  local arquivo_saida="$RUNS_DIR/$ts.log"

  log "Iniciando ciclo do Codex (saída em $arquivo_saida)."

  local saida
  local codigo_saida
  if [ -f "$SESSAO_INICIADA" ]; then
    log "Sessão já iniciada anteriormente — retomando com 'codex exec resume --last'."
    saida="$(codex exec resume --last \
      --approve-for-me \
      -C "$PROJECT_DIR" \
      2>&1)"
  else
    log "Primeira execução — enviando a instrução completa."
    saida="$(codex exec \
      --approve-for-me \
      -C "$PROJECT_DIR" \
      "$(cat "$ARQUIVO_INSTRUCAO")" \
      2>&1)"
    touch "$SESSAO_INICIADA"
  fi
  codigo_saida=$?
  printf '%s\n' "$saida" > "$arquivo_saida"
  podar_runs_antigos

  if [ -f "$ARQUIVO_CONCLUSAO" ]; then
    log "Tarefa concluída (arquivo $ARQUIVO_CONCLUSAO encontrado). Encerrando o loop."
    liberar_lock
    exit 0
  fi

  if [ "$codigo_saida" -ne 0 ]; then
    log "codex retornou código de saída $codigo_saida (não-zero)."
  fi

  if echo "$saida" | grep -Eiq "$PADRAO_LIMITE_COTA"; then
    log "Cota/limite de uso provavelmente estourado (texto bateu no padrão). Dormindo ${SLEEP_LIMITE_COTA}s (~5h30) antes de retomar."
    dormir_interrompivel "$SLEEP_LIMITE_COTA"
  elif [ "$codigo_saida" -ne 0 ]; then
    log "Erro inesperado (não bateu padrão de cota). Dormindo ${SLEEP_ERRO}s antes de tentar de novo. Ver $arquivo_saida."
    dormir_interrompivel "$SLEEP_ERRO"
  else
    log "Ciclo terminou sem erro mas sem o arquivo de conclusão — Codex pode ainda estar no meio da tarefa. Retomando em ${SLEEP_ERRO}s."
    dormir_interrompivel "$SLEEP_ERRO"
  fi
}

log "=== Loop do Codex iniciado (PID $$) ==="
adquirir_lock

if [ ! -f "$ARQUIVO_INSTRUCAO" ]; then
  log "ERRO: $ARQUIVO_INSTRUCAO não encontrado nesta pasta. Copie o arquivo de instrução pra cá antes de rodar o loop."
  liberar_lock
  exit 1
fi

while true; do
  if [ -f "$ARQUIVO_CONCLUSAO" ]; then
    log "Arquivo de conclusão já existe antes de começar — nada a fazer. Encerrando."
    liberar_lock
    exit 0
  fi
  if ! executar_um_ciclo; then
    log "Exceção não tratada dentro de executar_um_ciclo. Dormindo ${SLEEP_ERRO}s e continuando."
    dormir_interrompivel "$SLEEP_ERRO"
  fi
done
