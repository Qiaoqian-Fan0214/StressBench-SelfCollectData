#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

exec conda run -n self_data_obj python scripts/generate_object_candidates_grounded_sam.py \
  --continue-on-error \
  --labels-per-chunk 40 \
  --sam-boxes-per-chunk 24 \
  --max-objects 15
