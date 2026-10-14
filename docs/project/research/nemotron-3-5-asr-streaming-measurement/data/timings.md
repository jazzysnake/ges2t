# Rendered measurement tables

All numbers come from `cli_runs.jsonl`, `stream_summary.jsonl`,
`analysis.jsonl`, and `paced_summary.jsonl` beside this file.

## CLI batch — wall, RTF, peak RSS, accuracy

| label | audio s | wall s | RTF | RSS MB | wer(word_diff) | wer(std) | backend |
|---|---:|---:|---:|---:|---:|---:|---|
| cli-batch-jfk-vulkan0 | 11.00 | 5.11 | 2.15 | 180.9 | 0.000 | 0.000 | Vulkan0 |
| cli-batch-jfk-cpu | 11.00 | 4.38 | 2.51 | 1848.9 | 0.000 | 0.000 | CPU |
| cli-batch-quiet-short-vulkan0 | 13.51 | 4.79 | 2.82 | 180.7 | 0.259 | 0.259 | Vulkan0 |
| cli-batch-quiet-short-cpu | 13.51 | 5.07 | 2.67 | 1882.8 | 0.296 | 0.296 | CPU |
| cli-batch-quiet-long-vulkan0 | 23.81 | 6.05 | 3.94 | 180.8 | 0.167 | 0.139 | Vulkan0 |
| cli-batch-quiet-long-cpu | 23.81 | 6.14 | 3.88 | 2018.2 | 0.194 | 0.167 | CPU |
| cli-batch-ges2t-commit-cadence-capture-20261009-195914-vulkan0 | 25.04 | 6.22 | 4.03 | 180.8 | 0.312 | 0.266 | Vulkan0 |
| cli-batch-ges2t-commit-cadence-capture-20261009-195914-cpu | 25.04 | 6.70 | 3.74 | 2034.9 | 0.234 | 0.203 | CPU |
| cli-batch-clip5-vulkan0 | 5.00 | 2.81 | 1.78 | 180.5 | - | - | Vulkan0 |
| cli-batch-clip5-cpu | 5.00 | 3.22 | 1.55 | 1779.9 | - | - | CPU |
| cli-batch-clip30-vulkan0 | 30.00 | 7.28 | 4.12 | 180.7 | - | - | Vulkan0 |
| cli-batch-clip30-cpu | 30.00 | 7.39 | 4.06 | 2099.1 | - | - | CPU |
| cli-batch-2-vulkan0 | 3.27 | 2.02 | 1.62 | 180.6 | - | - | Vulkan0 |
| cli-batch-2-cpu | 3.27 | 1.64 | 2.00 | 1758.2 | - | - | CPU |
| cli-batch-3-vulkan0 | 6.62 | 2.97 | 2.23 | 180.7 | - | - | Vulkan0 |
| cli-batch-3-cpu | 6.62 | 3.65 | 1.82 | 1799.8 | - | - | CPU |
| cli-batch-four-vulkan0 | 6.58 | 3.97 | 1.66 | 180.6 | - | - | Vulkan0 |
| cli-batch-four-cpu | 6.58 | 3.59 | 1.83 | 1800.4 | - | - | CPU |
| cli-batch-cny16-vulkan0 | 10.44 | 4.34 | 2.40 | 180.6 | - | - | Vulkan0 |
| cli-batch-cny16-cpu | 10.44 | 4.32 | 2.42 | 1850.3 | - | - | CPU |

## C-ABI streaming — throughput, memory, latency

| label | audio s | stream s | RTF | RSS MB | VRAM MB | interims | first text (s audio) | steady p50 ms | steady p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| stream-jfk-vk | 11.00 | 3.66 | 3.01 | 196.8 | 952 | 62 | 1.28 | 47.097 | 57.621 |
| stream-quiet-short-vk | 13.51 | 4.65 | 2.91 | 201.1 | 953 | 70 | 2.56 | 46.294 | 59.325 |
| stream-quiet-long-vk | 23.81 | 5.82 | 4.09 | 197.2 | 952 | 130 | 3.2 | 25.704 | 58.122 |
| stream-ges2t-commit-cadence-capture-20261009-195914-vk | 25.04 | 6.27 | 3.99 | 197.3 | 952 | 146 | 1.92 | 24.285 | 58.174 |
| stream-clip5-vk | 5.00 | 2.67 | 1.87 | 196.7 | 953 | 27 | 0.96 | 56.172 | 61.96 |
| stream-clip30-vk | 30.00 | 6.55 | 4.58 | 197.8 | 952 | 183 | 0.96 | 24.37 | 56.95 |
| stream-2-vk | 3.27 | 1.84 | 1.78 | 196.4 | 952 | 15 | 1.12 | 57.193 | 62.237 |
| stream-3-vk | 6.62 | 2.39 | 2.77 | 196.8 | 953 | 37 | 0.96 | 42.673 | 58.533 |
| stream-four-vk | 6.58 | 2.34 | 2.81 | 196.9 | 952 | 36 | 1.12 | 32.955 | 58.885 |
| stream-cny16-vk | 10.44 | 2.35 | 4.45 | 197.1 | 953 | 61 | 0.96 | 24.439 | 28.924 |
| stream-jfk-cpu | 11.00 | 4.83 | 2.28 | 1063.7 | - | 62 | 1.28 | 67.249 | 73.467 |
| stream-quiet-short-cpu | 13.51 | 3.76 | 3.59 | 1063.8 | - | 70 | 2.56 | 42.005 | 43.495 |
| stream-quiet-long-cpu | 23.81 | 5.71 | 4.17 | 1064.3 | - | 130 | 3.2 | 35.986 | 37.249 |
| stream-ges2t-commit-cadence-capture-20261009-195914-cpu | 25.04 | 5.91 | 4.24 | 1064.4 | - | 146 | 1.92 | 35.597 | 37.61 |
| stream-clip5-cpu | 5.00 | 1.35 | 3.71 | 1063.7 | - | 27 | 0.96 | 34.257 | 36.759 |
| stream-clip30-cpu | 30.00 | 7.11 | 4.22 | 1064.2 | - | 183 | 0.96 | 36.062 | 37.745 |
| stream-2-cpu | 3.27 | 1.05 | 3.10 | 1063.7 | - | 15 | 1.12 | 39.631 | 41.463 |
| stream-3-cpu | 6.62 | 1.73 | 3.83 | 1063.7 | - | 37 | 0.96 | 35.017 | 37.062 |
| stream-four-cpu | 6.58 | 1.75 | 3.76 | 1063.5 | - | 36 | 1.12 | 35.253 | 38.498 |
| stream-cny16-cpu | 10.44 | 2.59 | 4.03 | 1063.4 | - | 61 | 0.96 | 35.717 | 37.628 |

## Committed-delta stability

| label | interims | append-only (char) frac | rewritten pairs | monotone commits | adjacent dups | algorithmic lag med s | WER (stream) |
|---|---:|---:|---:|---|---:|---:|---:|
| paced-clip30-vulkan | 183 | 1.0 | 0 | True | 0 | 0.24 | - |
| paced-jfk-cpu | 62 | 1.0 | 0 | True | 0 | 0.24 | - |
| paced-jfk-vulkan | 62 | 1.0 | 0 | True | 0 | 0.24 | - |
| silence-10s-cpu | 0 | None | 0 | True | 0 | None | - |
| silence-10s-vulkan | 0 | None | 0 | True | 0 | None | - |
| stream-2-cpu | 15 | 1.0 | 0 | True | 0 | 0.16 | - |
| stream-2-vk | 15 | 1.0 | 0 | True | 0 | 0.16 | - |
| stream-3-cpu | 37 | 1.0 | 0 | True | 0 | 0.2 | - |
| stream-3-vk | 37 | 1.0 | 0 | True | 0 | 0.16 | - |
| stream-clip30-cpu | 183 | 1.0 | 0 | True | 0 | 0.24 | - |
| stream-clip30-vk | 183 | 1.0 | 0 | True | 0 | 0.24 | - |
| stream-clip5-cpu | 27 | 1.0 | 0 | True | 0 | 0.24 | - |
| stream-clip5-vk | 27 | 1.0 | 0 | True | 0 | 0.24 | - |
| stream-cny16-cpu | 61 | 1.0 | 0 | True | 0 | 0.24 | - |
| stream-cny16-vk | 61 | 1.0 | 0 | True | 0 | 0.24 | - |
| stream-four-cpu | 36 | 1.0 | 0 | True | 0 | 0.24 | - |
| stream-four-vk | 36 | 1.0 | 0 | True | 0 | 0.2 | - |
| stream-ges2t-commit-cadence-capture-20261009-195914-cpu | 146 | 1.0 | 0 | True | 0 | 0.24 | 0.3281 |
| stream-ges2t-commit-cadence-capture-20261009-195914-vk | 146 | 1.0 | 0 | True | 0 | 0.24 | 0.3125 |
| stream-jfk-cpu | 62 | 1.0 | 0 | True | 0 | 0.24 | 0.0 |
| stream-jfk-vk | 62 | 1.0 | 0 | True | 0 | 0.24 | 0.0 |
| stream-quiet-long-cpu | 130 | 1.0 | 0 | True | 0 | 0.24 | 0.1667 |
| stream-quiet-long-vk | 130 | 1.0 | 0 | True | 0 | 0.24 | 0.1667 |
| stream-quiet-short-cpu | 70 | 1.0 | 0 | True | 0 | 0.24 | 0.2593 |
| stream-quiet-short-vk | 70 | 1.0 | 0 | True | 0 | 0.24 | 0.2593 |

## Real-time paced runs

| label | audio s | stream s | first text wall s | first text audio s | steady p50 ms | steady p90 ms |
|---|---:|---:|---:|---:|---:|---:|
| paced-jfk-vulkan | 11.00 | 11.10 | 1.164 | 1.28 | 40.822 | 44.73 |
| paced-clip30-vulkan | 30.00 | 30.09 | 0.854 | 0.96 | 41.244 | 45.043 |
| paced-jfk-cpu | 11.00 | 11.18 | 1.209 | 1.28 | 71.784 | 87.734 |

## Chunk-size sweep (Vulkan, `rnnt_right_context` sets the step)

| label | step ms | stream s | RTF | interims | steady p50 ms | steady p90 ms | first text (s audio) |
|---|---:|---:|---:|---:|---:|---:|---:|
| sweep-jfk-80 | 80 | 3.66 | 3.00 | 123 | 21.548 | 23.478 | 1.28 |
| sweep-jfk-160 | 160 | 2.22 | 4.96 | 62 | 21.859 | 24.649 | 1.28 |
| sweep-jfk-320 | 320 | 1.66 | 6.64 | 31 | 24.275 | 29.379 | 1.6 |
| sweep-jfk-560 | 560 | 1.31 | 8.38 | 18 | 32.7 | 43.31 | 1.68 |
| sweep-jfk-1120 | 1120 | 1.05 | 10.49 | 9 | 42.299 | 48.166 | 2.24 |
| sweep-clip30-80 | 80 | 8.49 | 3.53 | 365 | 20.952 | 23.125 | 0.88 |
| sweep-clip30-160 | 160 | 4.74 | 6.33 | 183 | 22.713 | 24.892 | 0.96 |
| sweep-clip30-320 | 320 | 2.97 | 10.11 | 91 | 25.635 | 28.566 | 1.28 |
| sweep-clip30-560 | 560 | 2.68 | 11.18 | 52 | 35.385 | 40.557 | 1.68 |
| sweep-clip30-1120 | 1120 | 1.93 | 15.57 | 26 | 44.065 | 50.072 | 2.24 |
