#!/usr/bin/env bash
# Vendor the client-side search model onto our own CDN so the read path has no third-party runtime
# dependency. The semantic-search query embedder (transformers.js, Xenova/bge-small-en-v1.5) otherwise
# fetches its ONNX weights from HuggingFace at runtime — which rate-limited us once and is a SPOF we
# don't control. This downloads the model once (locally, where HF is reachable) and rsyncs it to
# gs://<bucket>/pubs/models/, served same-origin as the viewer (no CORS) and matched by vsearch.ts's
# env.remoteHost. The viewer's `?models=` override points elsewhere for spikes.
#
#   ./scripts/vendor_search_assets.sh            # download + upload to the prod public bucket
#   ./scripts/vendor_search_assets.sh --local    # download only (stage in ./search-assets, no upload)
#
# Re-run is cheap + idempotent: skip-existing on download, rsync only ships changes. The model is
# immutable, so this is effectively one-time.
set -euo pipefail

BUCKET="${PUBLIC_BUCKET:-ut-dnr-ugs-maps-prod-public}"
MODEL="Xenova/bge-small-en-v1.5"
HF="https://huggingface.co/${MODEL}/resolve/main"
STAGE="${STAGE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/search-assets}"
DEST="${STAGE}/models/${MODEL}"

# The exact file set transformers.js needs for a quantized feature-extraction pipeline. quantized=true
# (the v2 default) loads onnx/model_quantized.onnx — the full model.onnx is fetched too as a fallback.
FILES=(
  "config.json"
  "tokenizer.json"
  "tokenizer_config.json"
  "special_tokens_map.json"
  "onnx/model_quantized.onnx"
)

mkdir -p "${DEST}/onnx"
for f in "${FILES[@]}"; do
  out="${DEST}/${f}"
  if [[ -s "${out}" ]]; then echo "have ${f}"; continue; fi
  echo "fetch ${f}"
  curl -fSL --retry 4 --retry-delay 2 -o "${out}" "${HF}/${f}"
done
echo "staged → ${DEST}"

if [[ "${1:-}" == "--local" ]]; then
  echo "local-only: skipping upload"; exit 0
fi

# Upload (needs storage.objectAdmin on the bucket). Immutable + correct types so
# the CDN caches hard and transformers.js parses the JSON/ONNX without sniff surprises.
echo "uploading → gs://${BUCKET}/pubs/models/"
gsutil -m -h "Cache-Control:public, max-age=31536000, immutable" \
  rsync -r "${STAGE}/models" "gs://${BUCKET}/pubs/models"
echo "done"
