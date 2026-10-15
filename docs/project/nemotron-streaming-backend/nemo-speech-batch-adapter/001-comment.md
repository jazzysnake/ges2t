# 001 — implementer comment (ticket 58, NeMo-Speech batch adapter)

Branch: `feat/nemo-speech-batch-adapter` · worktree `.worktrees/nemo-speech-batch-adapter`

## What this ticket is

Make `nemo_speech` a selectable engine with a working **batch** path, built on ticket 57's
acquisition (`NEMO_SPEECH_ENGINE`, `nemo_engine.py`, `model_store.ensure_nemo_model`/`nemo_model_path`).
`supports_streaming = False` for this ticket; ticket 59 flips it. No composition-root change (ticket 60),
no `doctor` change (ticket 61).

## Plan

1. `config.py`: `NEMO_SPEECH_ENGINE` → `KNOWN_ENGINES` (not `BATCH_ONLY_ENGINES`).
2. `transcribers/nemo_speech_config.py` (new): `[engine.nemo_speech]` (`device`, `chunk_seconds`,
   `model`); `window_seconds` falls through to the existing unknown-key warning.
3. `transcribers/nemo_speech.py` (new): `NemoSpeechLibrary` Protocol + `CtypesNemoSpeechLibrary` +
   `NemoSpeechTranscriber` (`ensure_model`/`load`/`transcribe`/`stream` refuses/`close`).
4. `tests/test_nemo_speech.py` (new): hermetic, fake-library driven + a real-seam version check.

## C-ABI confirmation (real header, no download, no run)

Header read: `~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/include/nemo_speech/asr.h`
(299 lines). The symbols the ticket names all exist, with these signatures:

```c
nemo_speech_asr_status nemo_speech_asr_create(const nemo_speech_asr_recognizer_config* cfg,
                                              nemo_speech_asr_recognizer** out);
void nemo_speech_asr_destroy(nemo_speech_asr_recognizer* recognizer);
nemo_speech_asr_status nemo_speech_asr_recognize_f32(nemo_speech_asr_recognizer* recognizer,
        const nemo_speech_asr_recognition_options* options, const float* samples,
        size_t n_samples, int32_t sample_rate, nemo_speech_asr_result** out);
const char* nemo_speech_asr_last_error(void);
```

Plus the accessors the batch path needs: `nemo_speech_asr_recognition_options_default`,
`nemo_speech_asr_result_transcript(result, alt)`, `nemo_speech_asr_result_language_count` /
`nemo_speech_asr_result_language_code(result, alt, i)`, `nemo_speech_asr_result_destroy`,
`nemo_speech_asr_version`.

`backend.gpu` comment: `-1 = CPU`; the ticket-55 harness used `0` for Vulkan. No callback in the
ASR ABI (confirmed in ticket 56 §4.1) — batch uses the same polled/push shape.

<!-- appended incrementally as work proceeds -->
