#!/bin/sh
# Build the whole workspace. tsc resolves project dependencies itself when the
# references are declared correctly.
set -e
tsc -b --force packages/app
