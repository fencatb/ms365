#!/usr/bin/env bash
# Print whether each named environment variable is set, WITHOUT leaking values.
# Usage: bash ci/check_env.sh VAR1 VAR2 ...
# Reusable across all tools in this repo.
set -u
for var in "$@"; do
  val="${!var:-}"
  if [ -n "$val" ]; then
    printf '[env] %-38s SET (length=%s)\n' "$var" "${#val}"
  else
    printf '[env] %-38s UNSET/EMPTY\n' "$var"
  fi
done
