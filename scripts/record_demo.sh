#!/usr/bin/env bash
# Record docs/demo.gif from the trajectory replay viewer.
#
# The choreography is deliberate holds rather than smooth scrolling. A GIF of continuous
# motion has no repeated frames and compresses terribly: the first attempt at this was
# 9.8 MB for nine seconds. Holding on each step keeps consecutive frames identical, which
# gets the same nine seconds under two megabytes, and is easier to read anyway.
#
#   ./scripts/record_demo.sh                    # picks a run with several failure modes
#   RUN_ID=<run-id> ./scripts/record_demo.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-4321}"
FPS="${FPS:-6}"
WIDTH="${WIDTH:-720}"
COLORS="${COLORS:-48}"
VIDEO_DIR="$(mktemp -d)"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"

echo "building the data bundle and the static site"
uv run python scripts/build_web_bundle.py --from-fixtures >/dev/null
(cd web && npm run build >/dev/null)

RUN_ID="${RUN_ID:-$(uv run python - <<'PY'
import json
from pathlib import Path

runs = json.loads(Path("web/public/data/runs.json").read_text())
# A run with several flagged steps and enough of them to scroll through: the coloured
# borders and the mode chips are the thing worth showing.
best = sorted(
    (r for r in runs if len(r["failure_modes"]) >= 2 and r["steps"] >= 12),
    key=lambda r: (-len(r["failure_modes"]), -r["steps"]),
)
print(best[0]["id"] if best else runs[0]["id"])
PY
)}"
echo "recording run $RUN_ID"

(cd web && npx serve out -l "$PORT" >/dev/null 2>&1 &)
trap 'pkill -f "serve out -l '"$PORT"'" || true' EXIT
sleep 5

VIDEO_DIR="$VIDEO_DIR" RUN_ID="$RUN_ID" PORT="$PORT" node - <<'EOF_NODE'
import { chromium } from "/home/claude/trajectory/web/node_modules/@playwright/test/index.mjs";
import fs from "node:fs";

const out = process.env.VIDEO_DIR;
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  recordVideo: { dir: out, size: { width: 1280, height: 800 } },
});
const page = await context.newPage();
await page.goto(`http://127.0.0.1:${process.env.PORT}/runs/${process.env.RUN_ID}`, {
  waitUntil: "networkidle",
});
await page.waitForTimeout(900);
await page.keyboard.press("f");
await page.waitForTimeout(1500);
for (let i = 0; i < 4; i += 1) {
  await page.keyboard.press("j");
  await page.waitForTimeout(1250);
}
await page.waitForTimeout(700);
await context.close();
await browser.close();
console.log(fs.readdirSync(out).find((n) => n.endsWith(".webm")));
EOF_NODE

VIDEO="$VIDEO_DIR/$(ls "$VIDEO_DIR" | grep '\.webm$' | head -1)"
FILTER="fps=$FPS,crop=1280:660:0:140,scale=$WIDTH:-1:flags=lanczos"

ffmpeg -y -v error -i "$VIDEO" -vf "$FILTER,palettegen=max_colors=$COLORS:stats_mode=diff" \
  "$VIDEO_DIR/palette.png"
ffmpeg -y -v error -i "$VIDEO" -i "$VIDEO_DIR/palette.png" \
  -lavfi "$FILTER[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle:new=1" \
  -loop 0 docs/demo.gif

printf 'wrote docs/demo.gif, %s KiB\n' "$(( $(stat -c%s docs/demo.gif) / 1024 ))"
