#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TOPOLOGY="${LAB_DIR}/clos01.clab.yml"

if ! command -v containerlab >/dev/null 2>&1; then
  echo "containerlab is not installed or not available in PATH." >&2
  exit 1
fi

containerlab inspect -t "${TOPOLOGY}"

echo
echo "Useful checks:"
echo "  docker exec clab-clos01-client1 ip -brief address"
echo "  docker exec clab-clos01-client2 ip -brief address"
echo "  ssh admin@clab-clos01-leaf1"
echo "  ssh admin@clab-clos01-leaf2"
echo "  ssh admin@clab-clos01-spine"
