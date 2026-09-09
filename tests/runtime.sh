#!/usr/bin/env bash
set -euo pipefail

config="$(realpath "$1")"
work="$(mktemp -d)"
pid=""
cleanup() {
  status=$?
  trap - EXIT
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  if [[ "$status" -ne 0 ]]; then
    for file in caddy.log headers body; do
      if [[ -f "$work/$file" ]]; then
        echo "=== $file ===" >&2
        cat "$work/$file" >&2
      fi
    done
  fi
  rm -rf "$work"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cd "$work"

export HOME="$work/home"
export XDG_CONFIG_HOME="$HOME/config"
export XDG_DATA_HOME="$HOME/data"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"

caddy validate --config "$config" --adapter caddyfile
caddy run --config "$config" --adapter caddyfile > caddy.log 2>&1 &
pid=$!

request() {
  curl -q --noproxy '*' --http1.1 --silent --show-error --max-time 5 \
    -H 'Accept-Language: en' -H 'Cookie:' \
    --dump-header headers --output body --write-out '%{http_code}' "$@"
}

assert_absent() {
  if grep "$@"; then
    echo "Unexpected response content" >&2
    exit 1
  fi
}

base=http://127.0.0.1:18080
ready=false
for ((attempt = 0; attempt < 50; attempt++)); do
  kill -0 "$pid"
  if code="$(request "$base/healthz" 2>/dev/null)" &&
    [[ "$code" == 200 && "$(cat body)" == ok ]]; then
    ready=true
    break
  fi
  sleep 0.1
done
[[ "$ready" == true ]]

check_public() {
  [[ "$(request "$base/healthz")" == 200 ]]
  [[ "$(cat body)" == ok ]]
  assert_absent -qi '^X-Cerberus-Status:' headers
}

check_public
[[ "$(request "$base/protected")" == 200 ]]
grep -Eqi '^X-Cerberus-Status: CHALLENGE[[:space:]]*$' headers
grep -Eqi '^Content-Type: text/html(;|[[:space:]])' headers
grep -Eqi '^Cache-Control: no-cache[[:space:]]*$' headers
grep -Fq '<title>Cerberus Challenge</title>' body
grep -Fq 'id="challenge-script"' body
grep -Fq 'x-challenge=' body
assert_absent -Fq 'Protected origin' body

# Follow the generated page's asset URL instead of pinning a hashed filename.
asset="$(grep -oE '/\.cerberus/static/[^" ]+\.css' body | sed -n '1p')"
[[ -n "$asset" ]]
[[ "$(request "$base$asset")" == 200 ]]
grep -Eqi '^Content-Type: text/css(;|[[:space:]])' headers
grep -Eqi '^Cache-Control: public, max-age=31536000, immutable[[:space:]]*$' headers
grep -Fq 'tailwindcss' body

[[ "$(request --data '' "$base/.cerberus/answer")" == 400 ]]
grep -Eqi '^X-Cerberus-Status: FAIL[[:space:]]*$' headers
grep -Fq 'nonce is empty' body
assert_absent -Eqi '^Set-Cookie: cerberus-auth=[^;[:space:]]+' headers

[[ "$(request "$base/.cerberus/answer")" == 404 ]]
grep -Eqi '^X-Cerberus-Status: FAIL[[:space:]]*$' headers
assert_absent -Fq 'Protected origin' body
check_public
kill -0 "$pid"
echo "Cerberus runtime checks passed"
