# whisper.cpp streaming — timings and agreement tables

Rendered from `data/summary.jsonl` (rebuilt from `data/passes*/` by
`recompute_summary.py`). Per-pass transcripts are in `data/passes/`.

Definitions:

- **duty** = total `whisper-cli` wall / audio seconds. < 1 is the real-time
  condition (each pass must finish inside one hop). Includes the per-invocation
  model load.
- **first commit** = source-audio time of the first committing pass.
- **latency** = window end minus the audio time of the newest committed word
  (real token timestamps; see the latency table).
- **prefix agree** = agreed words / previous pass words over same-window pairs
  (1.0 = the new pass fully reproduces the previous one).
- **regressed** = same-window pairs where the new pass did not reproduce the words
  already committed in the window.
- **rewritten** = same-window pairs whose overlap text was not identical.
- **dup words** = adjacent repeated words in the committed text (the seam-
  duplication signature).
- **WER (batch)** is the merged report's one-shot `medium q5_0` word_diff number.

## Primary — cap scheme, `-nt` decode (merged-report comparable)

The latency column is taken from the timestamped twins below (the `-nt` decode
has no token times, so its own times are synthetic). Every other column is
measured on the `-nt` decode.

| model/backend | W/H | clip | passes | windows | duty | first commit (s) | latency (s) | prefix agree | regressed | rewritten | dup words | WER (stream) | WER (batch) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medium/vulkan | 5/1 | quiet-long | 24 | 5 | 1.222 | 2 | 0.86 | 0.898 | 7 | 17 | 0 | 0.208 | 0.153 |
| medium/vulkan | 5/2 | quiet-long | 12 | 4 | 0.618 | 6 | 0.27 | 0.891 | 0 | 8 | 0 | 0.167 | 0.153 |
| medium/vulkan | 10/2 | quiet-long | 12 | 3 | 0.682 | 6 | 2.06 | 0.589 | 2 | 9 | 0 | 0.153 | 0.153 |
| small/vulkan | 5/1 | quiet-long | 24 | 5 | 0.466 | 2 | 1.13 | 0.574 | 9 | 17 | 0 | 0.264 | 0.153 |
| small/vulkan | 5/2 | quiet-long | 12 | 4 | 0.248 | 6 | 0.953 | 0.862 | 0 | 8 | 0 | 0.181 | 0.153 |
| small/vulkan | 10/2 | quiet-long | 12 | 3 | 0.26 | 6 | 2.34 | 0.859 | 1 | 9 | 0 | 0.153 | 0.153 |
| turbo/vulkan | 5/1 | quiet-long | 24 | 5 | 1.516 | 2 | 0.71 | 0.724 | 7 | 16 | 1 | 0.264 | 0.153 |
| turbo/vulkan | 5/2 | quiet-long | 12 | 4 | 0.752 | 6 | 0.43 | 0.769 | 1 | 8 | 1 | 0.167 | 0.153 |
| turbo/vulkan | 10/2 | quiet-long | 12 | 3 | 0.756 | 6 | 1.003 | 0.533 | 1 | 9 | 0 | 0.167 | 0.153 |
| medium/vulkan | 5/1 | car | 26 | 6 | 1.242 | 3 | 1.13 | 0.698 | 4 | 19 | 0 | 0.281 | 0.062 |
| medium/vulkan | 5/2 | car | 13 | 5 | 0.646 | 4 | 1.66 | 0.288 | 2 | 8 | 1 | 0.297 | 0.062 |
| medium/vulkan | 10/2 | car | 13 | 3 | 0.671 | 4 | 2.92 | 0.45 | 1 | 9 | 0 | 0.141 | 0.062 |
| small/vulkan | 5/1 | car | 26 | 6 | 0.463 | 3 | 1 | 0.535 | 5 | 19 | 1 | 0.312 | 0.062 |
| small/vulkan | 5/2 | car | 13 | 5 | 0.246 | 4 | 0.49 | 0.508 | 0 | 8 | 1 | 0.344 | 0.062 |
| small/vulkan | 10/2 | car | 13 | 3 | 0.261 | 4 | 3 | 0.532 | 1 | 9 | 0 | 0.312 | 0.062 |
| turbo/vulkan | 5/1 | car | 26 | 6 | 1.524 | 3 | 0.745 | 0.795 | 4 | 17 | 1 | 0.219 | 0.062 |
| turbo/vulkan | 5/2 | car | 13 | 5 | 0.805 | 4 | 0.572 | 0.37 | 1 | 8 | 1 | 0.312 | 0.062 |
| turbo/vulkan | 10/2 | car | 13 | 3 | 0.806 | 4 | 2.71 | 0.924 | 0 | 9 | 0 | 0.141 | 0.062 |
| medium/vulkan | 5/1 | clip30 | 30 | 6 | 1.24 | 2 | 1.315 | 0.868 | 12 | 17 | 0 | — | n/a |
| medium/vulkan | 5/2 | clip30 | 15 | 5 | 0.606 | 4 | 2.23 | 0.961 | 0 | 6 | 0 | — | n/a |
| medium/vulkan | 10/2 | clip30 | 15 | 3 | 0.649 | 4 | 2.23 | 0.875 | 6 | 10 | 0 | — | n/a |
| small/vulkan | 5/1 | clip30 | 30 | 6 | 0.475 | 2 | 1.39 | 0.874 | 10 | 20 | 0 | — | n/a |
| small/vulkan | 5/2 | clip30 | 15 | 5 | 0.24 | 4 | 2.375 | 0.851 | 0 | 9 | 0 | — | n/a |
| small/vulkan | 10/2 | clip30 | 15 | 3 | 0.269 | 4 | 2.44 | 0.808 | 5 | 11 | 1 | — | n/a |
| turbo/vulkan | 5/1 | clip30 | 30 | 6 | 1.528 | 2 | 1.39 | 0.843 | 9 | 21 | 0 | — | n/a |
| turbo/vulkan | 5/2 | clip30 | 15 | 5 | 0.773 | 4 | 2.23 | 0.922 | 0 | 9 | 0 | — | n/a |
| turbo/vulkan | 10/2 | clip30 | 15 | 3 | 0.792 | 4 | 2.615 | 0.825 | 5 | 11 | 0 | — | n/a |
| medium/vulkan | 5/1 | jfk | 11 | 3 | 1.191 | 2 | 0.995 | 0.933 | 2 | 6 | 1 | 0.045 | 0 |
| medium/vulkan | 5/2 | jfk | 6 | 2 | 0.696 | 4 | 1.67 | 0.552 | 1 | 4 | 1 | 0.136 | 0 |
| medium/vulkan | 10/2 | jfk | 6 | 2 | 0.675 | 4 | 2.03 | 1 | 0 | 4 | 1 | 0.045 | 0 |
| small/vulkan | 5/1 | jfk | 11 | 3 | 0.44 | 2 | 1.515 | 0.932 | 4 | 7 | 1 | 0.045 | 0 |
| small/vulkan | 5/2 | jfk | 6 | 2 | 0.253 | 4 | 1.01 | 0.774 | 0 | 3 | 0 | 0.091 | 0 |
| small/vulkan | 10/2 | jfk | 6 | 2 | 0.262 | 4 | 2.59 | 0.944 | 0 | 4 | 1 | 0.045 | 0 |
| turbo/vulkan | 5/1 | jfk | 11 | 3 | 1.49 | 2 | 0.51 | 0.933 | 2 | 6 | 1 | 0.045 | 0 |
| turbo/vulkan | 5/2 | jfk | 6 | 2 | 0.837 | 4 | 1.25 | 0.387 | 0 | 4 | 0 | 0.091 | 0 |
| turbo/vulkan | 10/2 | jfk | 6 | 2 | 0.841 | 4 | 1.97 | 1 | 0 | 4 | 1 | 0.045 | 0 |

## Commit latency — timestamped twins (`-ojf` decode)

| model/backend | W/H | clip | commit latency median (s) | p90 (s) | max (s) |
|---|---|---|---|---|---|
| medium/vulkan | 5/1 | quiet-long | 0.86 | 1.5 | 3.15 |
| medium/cpu | 5/2 | quiet-long | 0.27 | 2.46 | 2.78 |
| medium/vulkan | 5/2 | quiet-long | 0.27 | 2.46 | 2.78 |
| medium/vulkan | 10/2 | quiet-long | 2.06 | 2.434 | 2.5 |
| small/vulkan | 5/1 | quiet-long | 1.13 | 2.516 | 3.22 |
| small/vulkan | 5/2 | quiet-long | 0.953 | 3.355 | 4 |
| small/vulkan | 10/2 | quiet-long | 2.34 | 3.378 | 3.51 |
| turbo/vulkan | 5/1 | quiet-long | 0.71 | 1.654 | 2 |
| turbo/vulkan | 5/2 | quiet-long | 0.43 | 2.17 | 2.56 |
| turbo/vulkan | 10/2 | quiet-long | 1.003 | 2.875 | 3.51 |
| medium/vulkan | 5/1 | car | 1.13 | 1.938 | 2.29 |
| medium/cpu | 5/2 | car | 1.66 | 3.713 | 4 |
| medium/vulkan | 5/2 | car | 1.66 | 3.713 | 4 |
| medium/vulkan | 10/2 | car | 2.92 | 5.362 | 6.51 |
| small/vulkan | 5/1 | car | 1 | 2.8 | 3.26 |
| small/vulkan | 5/2 | car | 0.49 | 2.355 | 3.29 |
| small/vulkan | 10/2 | car | 3 | 4.398 | 4.87 |
| turbo/vulkan | 5/1 | car | 0.745 | 1.894 | 2.19 |
| turbo/vulkan | 5/2 | car | 0.572 | 2 | 3 |
| turbo/vulkan | 10/2 | car | 2.71 | 3.762 | 3.77 |
| medium/vulkan | 5/1 | clip30 | 1.315 | 1.785 | 2 |
| medium/vulkan | 5/2 | clip30 | 2.23 | 2.584 | 2.8 |
| medium/vulkan | 10/2 | clip30 | 2.23 | 2.612 | 2.7 |
| small/vulkan | 5/1 | clip30 | 1.39 | 2.208 | 2.62 |
| small/vulkan | 5/2 | clip30 | 2.375 | 2.791 | 2.8 |
| small/vulkan | 10/2 | clip30 | 2.44 | 3.516 | 3.62 |
| turbo/vulkan | 5/1 | clip30 | 1.39 | 2.068 | 3.57 |
| turbo/vulkan | 5/2 | clip30 | 2.23 | 2.728 | 2.8 |
| turbo/vulkan | 10/2 | clip30 | 2.615 | 4.074 | 5.67 |
| medium/vulkan | 5/1 | jfk | 0.995 | 1.435 | 1.67 |
| medium/vulkan | 5/2 | jfk | 1.67 | 2.405 | 2.42 |
| medium/vulkan | 10/2 | jfk | 2.03 | 2.594 | 2.71 |
| small/vulkan | 5/1 | jfk | 1.515 | 1.959 | 2.26 |
| small/vulkan | 5/2 | jfk | 1.01 | 2.466 | 2.83 |
| small/vulkan | 10/2 | jfk | 2.59 | 2.782 | 2.83 |
| turbo/vulkan | 5/1 | jfk | 0.51 | 1.696 | 1.99 |
| turbo/vulkan | 5/2 | jfk | 1.25 | 2.256 | 2.37 |
| turbo/vulkan | 10/2 | jfk | 1.97 | 2.35 | 2.59 |

## Fixed sliding window — slide scheme (`-ojf` decode, secondary)

| model/backend | W/H | clip | passes | windows | duty | first commit (s) | latency (s) | prefix agree | regressed | rewritten | dup words | WER (stream) | WER (batch) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medium/vulkan | 5/1 | quiet-long | 24 | 0 | 1.408 | 5 | 1.24 | 0.432 | 19 | 19 | 5 | 0.417 | 0.153 |
| medium/vulkan | 5/2 | quiet-long | 12 | 0 | 0.709 | 6 | 2.185 | 0.398 | 9 | 9 | 1 | 0.694 | 0.153 |
| medium/vulkan | 5/1 | car | 25 | 0 | 1.386 | 3 | 1.72 | 0.35 | 22 | 23 | 1 | 0.484 | 0.062 |
| medium/vulkan | 5/2 | car | 13 | 0 | 0.7 | 4 | 2.69 | 0.353 | 11 | 11 | 2 | 0.625 | 0.062 |
| medium/vulkan | 5/1 | clip30 | 30 | 0 | 1.36 | 3 | 1.51 | 0.611 | 25 | 22 | 6 | — | n/a |
| medium/vulkan | 5/2 | clip30 | 15 | 0 | 0.649 | 4 | 2.375 | 0.46 | 13 | 13 | 0 | — | n/a |
| medium/vulkan | 5/1 | jfk | 11 | 0 | 1.22 | 2 | 1.69 | 0.575 | 8 | 8 | 3 | 0.409 | 0 |
| medium/vulkan | 5/2 | jfk | 6 | 0 | 0.648 | 4 | 2.71 | 0.486 | 4 | 4 | 1 | 0.455 | 0 |

## `medium q5_0` CPU reference (`-ojf` decode)

| model/backend | W/H | clip | passes | windows | duty | first commit (s) | latency (s) | prefix agree | regressed | rewritten | dup words | WER (stream) | WER (batch) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medium/cpu | 5/2 | quiet-long | 12 | 4 | 6.106 | 6 | 0.27 | 0.632 | 1 | 7 | 1 | 0.375 | 0.153 |
| medium/cpu | 5/2 | car | 13 | 5 | 5.871 | 4 | 1.66 | 0.729 | 3 | 8 | 0 | 0.266 | 0.062 |

## Silence control — 10 s of digital silence

| model/backend | W/H | clip | passes | windows | duty | first commit (s) | latency (s) | prefix agree | regressed | rewritten | dup words | WER (stream) | WER (batch) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medium/vulkan | 5/1 | silence | 10 | 2 | 1.186 | — | — | — | 0 | 0 | 0 | — | n/a |
| small/vulkan | 5/1 | silence | 10 | 2 | 0.476 | — | — | — | 0 | 0 | 0 | — | n/a |
| turbo/vulkan | 5/1 | silence | 10 | 2 | 1.472 | 2 | 0.01 | 1 | 5 | 0 | 0 | — | n/a |

