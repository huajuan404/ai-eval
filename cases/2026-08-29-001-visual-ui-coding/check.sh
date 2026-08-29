#!/usr/bin/env bash
set -u

WORK_ROOT="${AI_EVAL_WORKDIR:-$PWD}"
CASE_ROOT="${AI_EVAL_CASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
VISUAL_CONTRACT="$CASE_ROOT/verify/visual-contract.json"
REPORT="$WORK_ROOT/ai_eval_check_report.json"
ARTIFACTS="$WORK_ROOT/ai_eval"
mkdir -p "$ARTIFACTS"

write_failure() {
  local message="$1"
  python3 - "$REPORT" "$message" <<'PY'
import json
import sys

path, message = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "schema_version": 1,
            "passed": False,
            "summary": {
                "evaluation_mode": "oracle_exact_match",
                "correct": 0,
                "total": 0,
            },
            "items": [],
            "errors": [message],
        },
        handle,
        ensure_ascii=False,
        indent=2,
    )
PY
  printf 'FAIL %s\n' "$message"
  exit 1
}

mapfile_cmd="mapfile"
if ! command -v "$mapfile_cmd" >/dev/null 2>&1; then
  mapfile_cmd="readarray"
fi
if ! command -v "$mapfile_cmd" >/dev/null 2>&1; then
  package_files=()
  while IFS= read -r package_file; do package_files+=("$package_file"); done < <(
    find "$WORK_ROOT" -maxdepth 4 -name package.json -type f -not -path '*/node_modules/*' -not -path '*/.next/*' | sort
  )
else
  "$mapfile_cmd" -t package_files < <(
    find "$WORK_ROOT" -maxdepth 4 -name package.json -type f -not -path '*/node_modules/*' -not -path '*/.next/*' | sort
  )
fi

if [ "${#package_files[@]}" -ne 1 ]; then
  write_failure "expected exactly one project package.json, found ${#package_files[@]}"
fi

PACKAGE_JSON="${package_files[0]}"
PROJECT_ROOT="$(cd "$(dirname "$PACKAGE_JSON")" && pwd)"

if ! node - "$PACKAGE_JSON" <<'NODE'
const {readFileSync} = require("node:fs");
const pkg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const deps = {...(pkg.dependencies || {}), ...(pkg.devDependencies || {})};
const missing = ["next", "react", "react-dom", "typescript"].filter((name) => !deps[name]);
if (!pkg.scripts?.build || !pkg.scripts?.start || missing.length) {
  console.error(`missing scripts/dependencies: ${missing.join(", ") || "build/start"}`);
  process.exit(1);
}
NODE
then
  write_failure "project must declare Next.js, React, TypeScript, build, and start"
fi

if [ ! -x "$PROJECT_ROOT/node_modules/.bin/next" ]; then
  write_failure "dependencies are not installed; the submitted app was not runnable"
fi

if ! (cd "$PROJECT_ROOT" && NEXT_TELEMETRY_DISABLED=1 npm run build) >"$ARTIFACTS/build.log" 2>&1; then
  write_failure "npm run build failed; see ai_eval/build.log"
fi

free_port() {
  python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

APP_PORT="$(free_port)"
CDP_PORT="$(free_port)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ai-eval-visual.XXXXXX")"
APP_PID=""
CHROME_PID=""
cleanup() {
  if [ -n "$APP_PID" ]; then kill "$APP_PID" >/dev/null 2>&1 || true; wait "$APP_PID" >/dev/null 2>&1 || true; fi
  if [ -n "$CHROME_PID" ]; then kill "$CHROME_PID" >/dev/null 2>&1 || true; wait "$CHROME_PID" >/dev/null 2>&1 || true; fi
  rm -rf -- "$TMP_ROOT"
}
trap cleanup EXIT INT TERM

(cd "$PROJECT_ROOT" && NEXT_TELEMETRY_DISABLED=1 ./node_modules/.bin/next start --hostname 127.0.0.1 --port "$APP_PORT") >"$ARTIFACTS/server.log" 2>&1 &
APP_PID="$!"

app_ready=0
for _ in $(seq 1 100); do
  if curl -fsS "http://127.0.0.1:$APP_PORT/" >/dev/null 2>&1; then app_ready=1; break; fi
  if ! kill -0 "$APP_PID" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
if [ "$app_ready" -ne 1 ]; then
  write_failure "application did not start; see ai_eval/server.log"
fi

find_chrome() {
  for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
    if command -v "$candidate" >/dev/null 2>&1; then command -v "$candidate"; return 0; fi
  done
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
    if [ -x "$candidate" ]; then printf '%s\n' "$candidate"; return 0; fi
  done
  return 1
}

CHROME="$(find_chrome)" || write_failure "Chrome or Chromium is required for browser validation"
command -v magick >/dev/null 2>&1 || write_failure "ImageMagick 'magick' is required for visual comparison"
VISUAL_FLOOR="$(node -e 'const c=require(process.argv[1]); if(c.schema_version!==1 || !Number.isFinite(c.visual_similarity_floor)) process.exit(1); process.stdout.write(String(c.visual_similarity_floor))' "$VISUAL_CONTRACT")" \
  || write_failure "visual contract is invalid"

"$CHROME" \
  --headless=new \
  --no-first-run \
  --no-default-browser-check \
  --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$TMP_ROOT/chrome" \
  --force-device-scale-factor=1 \
  about:blank >"$ARTIFACTS/chrome.log" 2>&1 &
CHROME_PID="$!"

cdp_ready=0
for _ in $(seq 1 100); do
  if curl -fsS "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; then cdp_ready=1; break; fi
  if ! kill -0 "$CHROME_PID" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
if [ "$cdp_ready" -ne 1 ]; then
  write_failure "headless Chrome did not expose the debugging endpoint; see ai_eval/chrome.log"
fi

if node "$CASE_ROOT/oracle/check_visual_ui.mjs" \
  --base-url "http://127.0.0.1:$APP_PORT" \
  --cdp-url "http://127.0.0.1:$CDP_PORT" \
  --work-root "$WORK_ROOT" \
  --reference "$CASE_ROOT/input/reference" \
  --contract "$VISUAL_CONTRACT" \
  --floor "$VISUAL_FLOOR"; then
  exit 0
fi

if [ ! -f "$REPORT" ]; then
  write_failure "browser validation failed before producing a report"
fi
exit 1
