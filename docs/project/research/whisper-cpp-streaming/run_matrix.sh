#!/bin/bash
# Drive the whisper.cpp streaming matrix. One whisper-cli per pass, every combination
# bounded by `timeout`. Safe to re-run: already-finished labels are skipped.
#
# Usage:  bash run_matrix.sh <phase>
#   phase = medium-vk | small-vk | turbo-vk | medium-cpu | slide | silence | all
set -u

ROOT=/home/matee/Workspace/Personal/ges2t/.worktrees/whisper-cpp-streaming
cd "$ROOT" || exit 1
H=docs/project/research/whisper-cpp-streaming/measure_streaming.py
M=~/.cache/ges2t-discovery/whisper-cpp-provider/models
A=~/.cache/ges2t-discovery/whisper-cpp-provider/audio
SCRATCH=~/.cache/ges2t-discovery/whisper-cpp-streaming-scratch
PASSES=docs/project/research/whisper-cpp-streaming/data/passes
SUMMARY=docs/project/research/whisper-cpp-streaming/data/summary.jsonl
LOG=$SCRATCH/matrix.log
mkdir -p "$PASSES" "$SCRATCH"

# done?  (skip a combo whose label is already in the summary)
done_label() { grep -q "\"label\": \"$1\"" "$SUMMARY" 2>/dev/null; }

run_cap() { # label model-file model-name backend W H audio reference-key
  local label=$1 model=$2 mname=$3 backend=$4 W=$5 Hp=$6 audio=$7 ref=$8
  [[ "$audio" == /* ]] || audio="$A/$audio"
  done_label "$label" && { echo "skip $label"; return; }
  echo "[$(date +%H:%M:%S)] RUN $label" | tee -a "$LOG"
  timeout 900 uv run python "$H" --scheme cap \
    --model "$M/$model" --model-name "$mname" --backend "$backend" \
    --window "$W" --hop "$Hp" --audio "$audio" \
    --label "$label" --reference-key "$ref" \
    --passes-out "$PASSES/$label.jsonl" --summary-out "$SUMMARY" --timeout 300 \
    >>"$LOG" 2>&1 || echo "  !! $label exit=$?" | tee -a "$LOG"
}

run_cap_nt() { # label model-file model-name backend W H audio reference-key
  local label=$1 model=$2 mname=$3 backend=$4 W=$5 Hp=$6 audio=$7 ref=$8
  [[ "$audio" == /* ]] || audio="$A/$audio"
  done_label "$label" && { echo "skip $label"; return; }
  echo "[$(date +%H:%M:%S)] RUN $label" | tee -a "$LOG"
  timeout 900 uv run python "$H" --scheme cap --no-timestamps \
    --model "$M/$model" --model-name "$mname" --backend "$backend" \
    --window "$W" --hop "$Hp" --audio "$audio" \
    --label "$label" --reference-key "$ref" \
    --passes-out "$PASSES/$label.jsonl" --summary-out "$SUMMARY" --timeout 300 \
    >>"$LOG" 2>&1 || echo "  !! $label exit=$?" | tee -a "$LOG"
}

run_slide() { # label model-file model-name backend W H audio reference-key
  local label=$1 model=$2 mname=$3 backend=$4 W=$5 Hp=$6 audio=$7 ref=$8
  done_label "$label" && { echo "skip $label"; return; }
  echo "[$(date +%H:%M:%S)] RUN $label" | tee -a "$LOG"
  timeout 900 uv run python "$H" --scheme slide \
    --model "$M/$model" --model-name "$mname" --backend "$backend" \
    --window "$W" --hop "$Hp" --audio "$A/$audio" \
    --label "$label" --reference-key "$ref" \
    --passes-out "$PASSES/$label.jsonl" --summary-out "$SUMMARY" --timeout 300 \
    >>"$LOG" 2>&1 || echo "  !! $label exit=$?" | tee -a "$LOG"
}

CLIPS=("quiet-long.wav:quiet-long" "ges2t-commit-cadence-capture-20261009-195914.wav:commit" "clip30.wav:clip30" "jfk.wav:jfk")
CONFIGS=("5:2" "5:1" "10:2")

phase=${1:-all}

if [[ "$phase" == "medium-vk" || "$phase" == all ]]; then
  for cfg in "${CONFIGS[@]}"; do W=${cfg%%:*}; Hp=${cfg##*:};
    for clip in "${CLIPS[@]}"; do f=${clip%%:*}; ref=${clip##*:};
      run_cap "cap-medium-vk-w${W}h${Hp}-${ref}" ggml-medium-q5_0.bin medium-q5_0 vulkan "$W" "$Hp" "$f" "$ref"
    done
  done
fi

if [[ "$phase" == "cap-nt" || "$phase" == all ]]; then
  for model in "ggml-medium-q5_0.bin:medium-q5_0:medium" "ggml-small-q5_0.bin:small-q5_0:small" "ggml-large-v3-turbo-q5_0.bin:turbo-q5_0:turbo"; do
    f=${model%%:*}; rest=${model#*:}; name=${rest%%:*}; tag=${rest##*:}
    for cfg in "${CONFIGS[@]}"; do W=${cfg%%:*}; Hp=${cfg##*:};
      for clip in "${CLIPS[@]}"; do cl=${clip%%:*}; ref=${clip##*:};
        run_cap_nt "ntcap-${tag}-vk-w${W}h${Hp}-${ref}" "$f" "$name" vulkan "$W" "$Hp" "$cl" "$ref"
      done
    done
  done
fi

if [[ "$phase" == "small-vk" || "$phase" == all ]]; then
  for cfg in "${CONFIGS[@]}"; do W=${cfg%%:*}; Hp=${cfg##*:};
    for clip in "${CLIPS[@]}"; do f=${clip%%:*}; ref=${clip##*:};
      run_cap "cap-small-vk-w${W}h${Hp}-${ref}" ggml-small-q5_0.bin small-q5_0 vulkan "$W" "$Hp" "$f" "$ref"
    done
  done
fi

if [[ "$phase" == "turbo-vk" || "$phase" == all ]]; then
  for cfg in "${CONFIGS[@]}"; do W=${cfg%%:*}; Hp=${cfg##*:};
    for clip in "${CLIPS[@]}"; do f=${clip%%:*}; ref=${clip##*:};
      run_cap "cap-turbo-vk-w${W}h${Hp}-${ref}" ggml-large-v3-turbo-q5_0.bin turbo-q5_0 vulkan "$W" "$Hp" "$f" "$ref"
    done
  done
fi

if [[ "$phase" == "medium-cpu" || "$phase" == all ]]; then
  run_cap "cap-medium-cpu-w5h2-commit" ggml-medium-q5_0.bin medium-q5_0 cpu 5 2 "ges2t-commit-cadence-capture-20261009-195914.wav" commit
  run_cap "cap-medium-cpu-w5h2-quiet-long" ggml-medium-q5_0.bin medium-q5_0 cpu 5 2 "quiet-long.wav" quiet-long
fi

if [[ "$phase" == "slide" || "$phase" == all ]]; then
  for cfg in "5:2" "5:1"; do W=${cfg%%:*}; Hp=${cfg##*:};
    for clip in "${CLIPS[@]}"; do f=${clip%%:*}; ref=${clip##*:};
      run_slide "slide-medium-vk-w${W}h${Hp}-${ref}" ggml-medium-q5_0.bin medium-q5_0 vulkan "$W" "$Hp" "$f" "$ref"
    done
  done
fi

if [[ "$phase" == "silence" || "$phase" == all ]]; then
  # 10 s of digital silence, generated in scratch, never committed.
  python3 - <<'PY'
import wave, struct
p=f"/home/matee/.cache/ges2t-discovery/whisper-cpp-streaming-scratch/silence10.wav"
with wave.open(p,'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\x00\x00"*16000*10)
PY
  for m in "ggml-medium-q5_0.bin:medium-q5_0" "ggml-small-q5_0.bin:small-q5_0" "ggml-large-v3-turbo-q5_0.bin:turbo-q5_0"; do
    f=${m%%:*}; name=${m##*:}
    run_cap "cap-${name}-vk-w5h1-silence" "$f" "$name" vulkan 5 1 "$SCRATCH/silence10.wav" ""
  done
fi

echo "PHASE $phase DONE"
