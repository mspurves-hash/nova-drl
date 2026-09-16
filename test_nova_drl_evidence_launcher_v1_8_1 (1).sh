#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/opt/nova-drl}"
LAUNCHER="$ROOT/bin/nova-drl-evidence"
SCRIPT="$ROOT/nova_drl_evidence_drilldown_v1_8_1.py"

if [[ ! -f "$LAUNCHER" ]]; then
    echo "FAIL: missing launcher: $LAUNCHER"
    exit 1
fi

if [[ ! -x "$LAUNCHER" ]]; then
    echo "FAIL: launcher is not executable: $LAUNCHER"
    exit 1
fi

if [[ ! -f "$SCRIPT" ]]; then
    echo "FAIL: missing evidence script: $SCRIPT"
    exit 1
fi

if ! grep -Fq 'nova_drl_evidence_drilldown_v1_8_1.py' "$LAUNCHER"; then
    echo "FAIL: launcher does not target v1.8.1"
    exit 1
fi

echo "PASS: nova-drl-evidence launcher points to frozen v1.8.1"
