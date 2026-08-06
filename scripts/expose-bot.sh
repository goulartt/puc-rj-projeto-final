#!/usr/bin/env bash
#
# Publica o bot: túnel novo, .env atualizado, n8n reconectado, webhook registrado.
#
# O túnel rápido do Cloudflare sorteia um domínio a cada início, e o n8n só lê
# `N8N_WEBHOOK_URL` no boot. Isso cria uma ordem que não é óbvia e que, feita
# errada, deixa o Telegram apontando para um domínio morto sem erro visível —
# a mensagem chega no vazio.
#
# A ordem que funciona, e o porquê de cada passo:
#
#   1. sobe o túnel primeiro, porque é ele que decide a URL;
#   2. espera o túnel responder de verdade, e não só imprimir a URL no log:
#      o domínio aparece antes de a rota estar de pé;
#   3. grava a URL no .env e recria o n8n, que é a única forma de ele reler
#      a variável (`restart` mantém o ambiente antigo);
#   4. apaga o webhook anterior — sem isso o Telegram guarda o domínio morto;
#   5. reinicia o n8n para ele registrar o webhook novo;
#   6. confirma no próprio Telegram, que é a única fonte que importa.
#
# Uso: ./scripts/expose-bot.sh

set -euo pipefail
cd "$(dirname "$0")/.."

log() { printf '\033[36m▸\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -f .env ] || die ".env nao encontrado"

log "subindo o tunel"
docker compose up -d --force-recreate cloudflared >/dev/null 2>&1

url=""
for _ in $(seq 1 30); do
  url=$(docker compose logs cloudflared --since 3m 2>&1 \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  if [ -n "$url" ] && [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "$url/healthz")" = "200" ]; then
    break
  fi
  url=""
  sleep 4
done
[ -n "$url" ] || die "o tunel nao respondeu a tempo"
log "tunel no ar: $url"

log "gravando a URL no .env e recriando o n8n"
python3 - "$url" <<'PY'
import pathlib, re, sys
path = pathlib.Path(".env")
text = path.read_text()
line = f"WEBHOOK_URL={sys.argv[1]}"
path.write_text(re.sub(r"^WEBHOOK_URL=.*$", line, text, flags=re.M)
                if re.search(r"^WEBHOOK_URL=", text, flags=re.M)
                else text.rstrip() + "\n" + line + "\n")
PY
docker compose up -d n8n >/dev/null 2>&1

until docker compose exec -T n8n sh -c 'wget -qO- --timeout=3 http://localhost:5678/healthz >/dev/null 2>&1'; do
  sleep 3
done

# shellcheck disable=SC1091
set -a; . ./.env; set +a
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || die "TELEGRAM_BOT_TOKEN ausente do .env"

log "limpando o webhook anterior e reativando"
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook" >/dev/null
docker compose restart n8n >/dev/null 2>&1
until docker compose exec -T n8n sh -c 'wget -qO- --timeout=3 http://localhost:5678/healthz >/dev/null 2>&1'; do
  sleep 3
done

registered=$(curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" \
             | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"].get("url",""))')

if [ -z "$registered" ]; then
  docker compose logs n8n --since 2m 2>&1 | grep -iE "Chat no Telegram|did fail" | tail -5
  die "o Telegram nao aceitou o webhook"
fi

printf '\n\033[32m✓\033[0m bot no ar\n'
printf '  editor : http://localhost:5678\n'
printf '  webhook: %s\n' "$registered"
