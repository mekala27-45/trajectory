#!/bin/sh
# Type check src/ and emit dist/.
#
# This does not regenerate the client. If openapi.json has moved, run
# `node tools/generate-client.mjs` first: see the README.
set -e
tsc -p tsconfig.json
