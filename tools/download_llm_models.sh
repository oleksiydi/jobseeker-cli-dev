#!/usr/bin/env bash
set -euo pipefail

# Download GGUF models for local llama.cpp runtime.
# - Uses curl with resume (-C -)
# - Stores files on host (default: models)
# - Supports aliases to avoid long URLs in commands

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/models"

print_usage() {
  cat <<'USAGE'
Usage:
  bash tools/download_llm_models.sh --model <alias|all> [--out-dir <path>]

Options:
  --model     Model alias: qwen8 | mistral7 | all
  --out-dir   Destination directory (default: models)
  -h, --help  Show help

Examples:
  bash tools/download_llm_models.sh --model qwen8
  bash tools/download_llm_models.sh --model mistral7 --out-dir /data/models
  bash tools/download_llm_models.sh --model all
USAGE
}

MODEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="${2:-}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      print_usage
      exit 1
      ;;
  esac
done

if [[ -z "$MODEL" ]]; then
  echo "Error: --model is required." >&2
  print_usage
  exit 1
fi

mkdir -p "$OUT_DIR"

download_one() {
  local alias="$1"
  local url="$2"
  local filename="$3"
  local target="${OUT_DIR}/${filename}"
  local tmp="${target}.part"

  echo "[INFO] Downloading ${alias} -> ${target}"
  # Resume partial download if exists.
  curl --fail --location --continue-at - --output "$tmp" "$url"
  mv -f "$tmp" "$target"
  echo "[OK] ${alias}: ${target}"
}

download_qwen8() {
  # Qwen3 8B Instruct, Q4_K_M quantization (for chat/instruction tasks).
  download_one \
    "qwen8" \
    "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf" \
    "Qwen3-8B-Q4_K_M.gguf"
}

download_mistral7() {
  # Mistral 7B Instruct v0.2, Q4_K_M quantization.
  download_one \
    "mistral7" \
    "https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF/resolve/main/mistral-7b-instruct-v0.2.Q4_K_M.gguf" \
    "Mistral-7B-Instruct-Q4_K_M.gguf"
}

case "$MODEL" in
  qwen8)
    download_qwen8
    ;;
  mistral7)
    download_mistral7
    ;;
  all)
    download_qwen8
    download_mistral7
    ;;
  *)
    echo "Error: unsupported model alias '$MODEL'." >&2
    echo "Supported: qwen8 | mistral7 | all" >&2
    exit 1
    ;;
esac

echo "[DONE] Files in ${OUT_DIR}:"
ls -lh "$OUT_DIR"
