#!/usr/bin/env bash
# Descobre a URL do quick tunnel do Cloudflare, grava em WEBHOOK_URL no .env e
# religa o n8n para que ele passe a montar as URLs de webhook com esse domínio.
#
# Precisa rodar sempre que o container do cloudflared reiniciar: o quick tunnel
# sorteia um subdomínio novo a cada vez. Para uma URL fixa seria preciso uma
# conta na Cloudflare com domínio próprio e um named tunnel.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "aguardando o cloudflared publicar a URL..."
url=""
for _ in $(seq 1 30); do
  url=$(docker compose logs cloudflared 2>&1 \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
        | tail -1) || true
  [ -n "$url" ] && break
  sleep 2
done

if [ -z "$url" ]; then
  echo "erro: nenhuma URL encontrada nos logs do cloudflared." >&2
  echo "verifique com: docker compose logs cloudflared" >&2
  exit 1
fi

echo "túnel: $url"

# Reescreve só a linha do WEBHOOK_URL, preservando o resto do .env.
python3 - "$url" <<'PY'
import pathlib, re, sys
url = sys.argv[1]
p = pathlib.Path(".env")
text = p.read_text()
if re.search(r"(?m)^WEBHOOK_URL=", text):
    text = re.sub(r"(?m)^WEBHOOK_URL=.*$", f"WEBHOOK_URL={url}", text)
else:
    text = text.rstrip("\n") + f"\nWEBHOOK_URL={url}\n"
p.write_text(text)
PY

echo "WEBHOOK_URL gravado no .env; recriando o n8n..."
docker compose up -d n8n

echo
echo "editor local : http://localhost:5678"
echo "url pública  : $url"
