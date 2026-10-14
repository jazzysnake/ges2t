#!/usr/bin/env bash
# NeMo-Speech.cpp measurement matrix driver (discovery; not shipped code).
#
# Runs the CLI harness (wall/RTF/RSS/accuracy, batch + --stream) and the C-ABI
# streaming harness (partials, append-only stability) over the project's
# reference clips, on Vulkan and CPU. Everything heavy lands in the scratch dir
# outside the repo; nothing here writes under src/ or tests/.
#
# Usage: bash run_matrix.sh <phase>   # phase: cli | stream-vk | stream-cpu | all
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"          # the worktree root
SC="$HOME/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement"
AUDIO="$HOME/.cache/ges2t-discovery/whisper-cpp-provider/audio"
ENG="$SC/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/bin/nemo-speech"
LIB="$SC/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/lib/libnemo_speech_asr_c.so.1"
MODEL="$SC/models/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
mkdir -p "$SC/partials"

CLI_RESULTS="$SC/cli_runs.jsonl"
STREAM_RESULTS="$SC/stream_summary.jsonl"

# clip:reference-key  (empty key = no human reference)
CLIPS=(
  "jfk.wav:jfk"
  "quiet-short.wav:quiet-short"
  "quiet-long.wav:quiet-long"
  "ges2t-commit-cadence-capture-20261009-195914.wav:commit"
  "clip5.wav:"
  "clip30.wav:"
  "2.wav:"
  "3.wav:"
  "four.wav:"
  "cny16.wav:"
)

run_cli_phase() {
  for entry in "${CLIPS[@]}"; do
    clip="${entry%%:*}"; key="${entry##*:}"
    for dev in vulkan:0 cpu; do
      label="cli-batch-${clip%.wav}-${dev//:/}"
      ref=(); [ -n "$key" ] && ref=(--reference-key "$key")
      echo "### $label"
      PYTHONPATH="$ROOT/src" timeout 300 python3 "$HERE/measure_nemo_cli.py" \
        --cli "$ENG" --model "$MODEL" --audio "$AUDIO/$clip" \
        --device "$dev" --label "$label" ${ref[@]+"${ref[@]}"} \
        --results "$CLI_RESULTS" --timeout 280 >/dev/null 2>&1 || echo "  (nonzero exit)"
    done
  done
}

run_stream_phase() {
  dev="$1"; tag="$2"
  for entry in "${CLIPS[@]}"; do
    clip="${entry%%:*}"
    label="stream-${clip%.wav}-${tag}"
    echo "### $label"
    timeout 300 python3 "$HERE/measure_nemo_streaming.py" \
      --lib "$LIB" --model "$MODEL" --audio "$AUDIO/$clip" \
      --device "$dev" --chunk-ms 160 --chunk-sec 0.16 --rnnt-right-context 1 --interim \
      --label "$label" --partials-out "$SC/partials/$label.jsonl" \
      --summary-out "$STREAM_RESULTS" --timeout 280 >/dev/null 2>&1 || echo "  (nonzero exit)"
  done
}

case "${1:-all}" in
  cli) run_cli_phase ;;
  stream-vk) run_stream_phase vulkan vk ;;
  stream-cpu) run_stream_phase cpu cpu ;;
  all) run_cli_phase; run_stream_phase vulkan vk; run_stream_phase cpu cpu ;;
  *) echo "unknown phase: $1" >&2; exit 2 ;;
esac
echo "done; cli rows: $(wc -l < "$CLI_RESULTS" 2>/dev/null || echo 0), stream rows: $(wc -l < "$STREAM_RESULTS" 2>/dev/null || echo 0)"
