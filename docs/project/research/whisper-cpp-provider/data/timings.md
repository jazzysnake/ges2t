# whisper.cpp discovery — timings and accuracy tables

Rendered from `data/runs.jsonl`. `wall_s median (min-max) | RTF` summarises repeat runs;
accuracy is deterministic per configuration (asserted in the renderer). RTF = audio_s / wall_s.
`word_diff` WER is the project's metric; `std` WER is a standard word edit distance.

### Greedy q5_0 sweep — CPU — ges2t-commit-cadence-capture-20261009-195914.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-tiny-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 3 | 0.97 (0.67-1.83) \| 25.80 | 104 | 0.312 | 0.281 |
| cpu ggml-base-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 5 | 2.42 (1.82-5.89) \| 10.34 | 151 | 0.297 | 0.234 |
| cpu ggml-small-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 9 | 6.45 (4.84-7.79) \| 3.88 | 336 | 0.141 | 0.141 |
| cpu ggml-medium-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 5 | 19.29 (8.54-22.45) \| 1.30 | 828 | 0.062 | 0.062 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 4 | 14.95 (11.82-18.54) \| 1.68 | 733 | 0.141 | 0.125 |

### Greedy q5_0 sweep — CPU — jfk.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-tiny-q5_0.bin [greedy] on jfk.wav | 1 | 0.42 \| 26.30 | 103 | 0.000 | 0.000 |
| cpu ggml-base-q5_0.bin [greedy] on jfk.wav | 1 | 0.62 \| 17.79 | 150 | 0.000 | 0.000 |
| cpu ggml-small-q5_0.bin [greedy] on jfk.wav | 1 | 2.23 \| 4.93 | 332 | 0.000 | 0.000 |
| cpu ggml-medium-q5_0.bin [greedy] on jfk.wav | 1 | 7.19 \| 1.53 | 821 | 0.000 | 0.000 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on jfk.wav | 1 | 11.91 \| 0.92 | 724 | 0.000 | 0.000 |

### Quantisation — small — CPU — ges2t-commit-cadence-capture-20261009-195914.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-small-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 9 | 6.45 (4.84-7.79) \| 3.88 | 336 | 0.141 | 0.141 |
| cpu ggml-small-q8_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 6 | 5.86 (2.37-8.64) \| 4.44 | 421 | 0.141 | 0.141 |
| cpu ggml-small.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 6 | 8.57 (3.38-8.69) \| 2.92 | 638 | 0.141 | 0.141 |

### Quantisation — medium — CPU — ges2t-commit-cadence-capture-20261009-195914.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-medium-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 5 | 19.29 (8.54-22.45) \| 1.30 | 828 | 0.062 | 0.062 |
| cpu ggml-medium-q8_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 3 | 17.89 (8.05-21.73) \| 1.40 | 1098 | 0.062 | 0.062 |
| cpu ggml-medium.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 3 | 26.06 (22.47-74.74) \| 0.96 | 1780 | 0.062 | 0.062 |

### Greedy vs beam-5 — CPU — car clip

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-small-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 9 | 6.45 (4.84-7.79) \| 3.88 | 336 | 0.141 | 0.141 |
| cpu ggml-small-q5_0.bin [beam5] on ges2t-commit-cadence-capture-20261009-195914.wav | 2 | 45.12 (14.57-75.67) \| 1.02 | 458 | 0.141 | 0.141 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 4 | 14.95 (11.82-18.54) \| 1.68 | 733 | 0.141 | 0.125 |
| cpu ggml-large-v3-turbo-q5_0.bin [beam5] on ges2t-commit-cadence-capture-20261009-195914.wav | 2 | 17.08 (12.47-21.69) \| 1.58 | 804 | 0.125 | 0.109 |

### .en vs multilingual — base q5_0 — CPU — car clip

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-base-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 5 | 2.42 (1.82-5.89) \| 10.34 | 151 | 0.297 | 0.234 |
| cpu ggml-base.en-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 3 | 2.52 (0.82-2.97) \| 9.93 | 150 | 0.156 | 0.141 |

### CPU vs Vulkan — ges2t-commit-cadence-capture-20261009-195914.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-small-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 9 | 6.45 (4.84-7.79) \| 3.88 | 336 | 0.141 | 0.141 |
| vulkan ggml-small-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 3 | 0.87 (0.87-0.87) \| 28.91 | 94 | 0.141 | 0.141 |
| cpu ggml-medium-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 5 | 19.29 (8.54-22.45) \| 1.30 | 828 | 0.062 | 0.062 |
| vulkan ggml-medium-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 3 | 1.72 (1.67-1.82) \| 14.57 | 105 | 0.062 | 0.062 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 4 | 14.95 (11.82-18.54) \| 1.68 | 733 | 0.141 | 0.125 |
| vulkan ggml-large-v3-turbo-q5_0.bin [greedy] on ges2t-commit-cadence-capture-20261009-195914.wav | 3 | 1.47 (1.47-1.47) \| 17.05 | 118 | 0.141 | 0.125 |

### CPU vs Vulkan — jfk.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-small-q5_0.bin [greedy] on jfk.wav | 1 | 2.23 \| 4.93 | 332 | 0.000 | 0.000 |
| vulkan ggml-small-q5_0.bin [greedy] on jfk.wav | 0 | MISSING | | | |
| cpu ggml-medium-q5_0.bin [greedy] on jfk.wav | 1 | 7.19 \| 1.53 | 821 | 0.000 | 0.000 |
| vulkan ggml-medium-q5_0.bin [greedy] on jfk.wav | 1 | 1.12 \| 9.84 | 105 | 0.000 | 0.000 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on jfk.wav | 1 | 11.91 \| 0.92 | 724 | 0.000 | 0.000 |
| vulkan ggml-large-v3-turbo-q5_0.bin [greedy] on jfk.wav | 1 | 1.22 \| 9.03 | 117 | 0.000 | 0.000 |

### CPU vs Vulkan — quiet-long.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-small-q5_0.bin [greedy] on quiet-long.wav | 1 | 5.24 \| 4.55 | 336 | 0.194 | 0.139 |
| vulkan ggml-small-q5_0.bin [greedy] on quiet-long.wav | 0 | MISSING | | | |
| cpu ggml-medium-q5_0.bin [greedy] on quiet-long.wav | 1 | 20.28 \| 1.17 | 826 | 0.153 | 0.111 |
| vulkan ggml-medium-q5_0.bin [greedy] on quiet-long.wav | 3 | 1.72 (1.72-1.72) \| 13.84 | 105 | 0.153 | 0.111 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on quiet-long.wav | 1 | 15.07 \| 1.58 | 733 | 0.153 | 0.111 |
| vulkan ggml-large-v3-turbo-q5_0.bin [greedy] on quiet-long.wav | 1 | 1.47 \| 16.23 | 117 | 0.153 | 0.111 |

### Second condition (quiet room) — CPU — quiet-short.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-tiny-q5_0.bin [greedy] on quiet-short.wav | 1 | 0.62 \| 21.82 | 103 | 0.593 | 0.407 |
| cpu ggml-base-q5_0.bin [greedy] on quiet-short.wav | 1 | 1.52 \| 8.87 | 150 | 0.556 | 0.407 |
| cpu ggml-base.en-q5_0.bin [greedy] on quiet-short.wav | 1 | 1.17 \| 11.57 | 149 | 0.556 | 0.407 |
| cpu ggml-small-q5_0.bin [greedy] on quiet-short.wav | 1 | 3.83 \| 3.53 | 337 | 0.444 | 0.333 |
| cpu ggml-medium-q5_0.bin [greedy] on quiet-short.wav | 1 | 13.56 \| 1.00 | 827 | 0.407 | 0.296 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on quiet-short.wav | 1 | 16.73 \| 0.81 | 732 | 0.444 | 0.333 |

### Second condition (quiet room) — CPU — quiet-long.wav

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-tiny-q5_0.bin [greedy] on quiet-long.wav | 1 | 0.97 \| 24.60 | 104 | 0.417 | 0.319 |
| cpu ggml-base-q5_0.bin [greedy] on quiet-long.wav | 1 | 4.38 \| 5.43 | 151 | 0.181 | 0.139 |
| cpu ggml-base.en-q5_0.bin [greedy] on quiet-long.wav | 1 | 2.43 \| 9.80 | 150 | 0.208 | 0.153 |
| cpu ggml-small-q5_0.bin [greedy] on quiet-long.wav | 1 | 5.24 \| 4.55 | 336 | 0.194 | 0.139 |
| cpu ggml-medium-q5_0.bin [greedy] on quiet-long.wav | 1 | 20.28 \| 1.17 | 826 | 0.153 | 0.111 |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on quiet-long.wav | 1 | 15.07 \| 1.58 | 733 | 0.153 | 0.111 |

### Per-window cost (5 s clip5) and 30 s clip30 — no reference

| config | n | wall_s median (min-max) \| RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| cpu ggml-small-q5_0.bin [greedy] on clip5.wav | 1 | 3.83 \| 1.31 | 334 | nan | nan |
| vulkan ggml-small-q5_0.bin [greedy] on clip5.wav | 1 | 0.47 \| 10.72 | 94 | nan | nan |
| cpu ggml-medium-q5_0.bin [greedy] on clip5.wav | 1 | 13.71 \| 0.36 | 825 | nan | nan |
| vulkan ggml-medium-q5_0.bin [greedy] on clip5.wav | 1 | 0.97 \| 5.17 | 105 | nan | nan |
| cpu ggml-large-v3-turbo-q5_0.bin [greedy] on clip5.wav | 1 | 14.88 \| 0.34 | 730 | nan | nan |
| vulkan ggml-large-v3-turbo-q5_0.bin [greedy] on clip5.wav | 1 | 1.17 \| 4.28 | 117 | nan | nan |
| cpu ggml-medium-q5_0.bin [greedy] on clip30.wav | 1 | 24.31 \| 1.23 | 828 | nan | nan |
| vulkan ggml-medium-q5_0.bin [greedy] on clip30.wav | 1 | 2.07 \| 14.47 | 105 | nan | nan |
| vulkan ggml-large-v3-turbo-q5_0.bin [greedy] on clip30.wav | 1 | 1.42 \| 21.14 | 117 | nan | nan |

### Verbatim transcripts (car clip, greedy, q5_0 unless noted)

- **ggml-tiny-q5_0.bin [greedy]**, word_diff WER 0.312, std WER 0.281:
  > Good morning! Last night's storm, no doubt power to roughly 4,000 homes across the northern rally and cruise expects three storm, most of them by Friday afternoon. Officials say the reverse crusted at 3.2 meters just after midnight. Well below the Faberier, is that reading correct? Two low-lying roads are in close tonight and the ferry service resumed at half past six.
- **ggml-base-q5_0.bin [greedy]**, word_diff WER 0.297, std WER 0.234:
  > Good morning! Last night's storm no doubt powered roughly 4000 homes across the northern Rally and cruise expect to release storm most of them by Friday afternoon. Officials say the reverse crested at 3.2 meters just after midnight, well below the fulberryer. Is that reading correct? Two low lying roads remain closed night and the ferry service resumes at half past six.
- **ggml-small-q5_0.bin [greedy]**, word_diff WER 0.141, std WER 0.141:
  > Good morning. Last night's storm no doubt powered to roughly 4,000 homes across the northern valley and cruise expects to restore most of them by Friday afternoon. Officials say the river is crested at 3.2 meters just after midnight, well below the flood barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-medium-q5_0.bin [greedy]**, word_diff WER 0.062, std WER 0.062:
  > Good morning. Last night's storm knocked out power to roughly 4,000 homes across the Northern Valley, and crews expect to restore most of them by Friday afternoon. Officials say the rivers crested at 3.2 meters just after midnight, well below the flood barrier. Is that reading correct? Two low-lying roads remain closed tonight, and the ferry service resumes at half past six.
- **ggml-large-v3-turbo-q5_0.bin [greedy]**, word_diff WER 0.141, std WER 0.125:
  > Good morning. Last night storm no doubt powered to roughly 4000 homes across the Northern Valley and crews expect to restore most of them by Friday afternoon. Officials say the river is crested at 3.2 meters just after midnight, well below the flood barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-small-q8_0.bin [greedy]**, word_diff WER 0.141, std WER 0.141:
  > Good morning. Last night's storm no doubt powered to roughly 4,000 homes across the northern valley and cruise expect to restore most of them by Friday afternoon. Officials say the river is crested at 3.2 meters just after midnight, well below the fad barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-small.bin [greedy]**, word_diff WER 0.141, std WER 0.141:
  > Good morning. Last night's storm no doubt powered to roughly 4,000 homes across the northern valley and cruise expect to restore most of them by Friday afternoon. Officials say the river is crested at 3.2 meters just after midnight, well below the fad barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-medium-q8_0.bin [greedy]**, word_diff WER 0.062, std WER 0.062:
  > Good morning. Last night's storm knocked out power to roughly 4,000 homes across the Northern Valley and crews expect to restore most of them by Friday afternoon. Officials say the rivers crested at 3.2 meters just after midnight, well below the flood barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-medium.bin [greedy]**, word_diff WER 0.062, std WER 0.062:
  > Good morning. Last night's storm knocked out power to roughly 4,000 homes across the Northern Valley and crews expect to restore most of them by Friday afternoon. Officials say the rivers crested at 3.2 meters just after midnight, well below the flood barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-small-q5_0.bin [beam5]**, word_diff WER 0.141, std WER 0.141:
  > Good morning. Last night's storm no doubt powered to roughly 4,000 homes across the northern valley and cruise expects to restore most of them by Friday afternoon. Officials say the river has crested at 3.2 meters just after midnight, well below the flood barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-large-v3-turbo-q5_0.bin [beam5]**, word_diff WER 0.125, std WER 0.109:
  > Good morning. Last night's storm no doubt powered to roughly 4000 homes across the northern valley and crews expect to restore most of them by Friday afternoon. Officials say the river is crested at 3.2 meters just after midnight, well below the flood barrier. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-base.en-q5_0.bin [greedy]**, word_diff WER 0.156, std WER 0.141:
  > Good morning. Last night's storm no doubt bower to roughly 4000 homes across the Northern Valley and crews expect to restore most of them by Friday afternoon. Officials say the reverse crested at 3.2 meters just after midnight. Well below the fall barrier. Is that reading correct? Two low-lying roads remain close tonight and the ferry service resumes at half past six.
- **ggml-small-q5_1.bin [greedy]**, word_diff WER 0.172, std WER 0.172:
  > Good morning. Last night's storm no doubt powered to roughly 4,000 homes across the Northern Rally and cruise expect to restore most of them by Friday afternoon. Officials say the river has crested at 3.2 meters just after midnight, well below the Fadbear year. Is that reading correct? Two low-lying roads remain closed tonight and the ferry service resumes at half past six.
- **ggml-base-q5_1.bin [greedy]**, word_diff WER 0.344, std WER 0.297:
  > Good morning! Last night's storm no doubt powered roughly 4,000 homes across the northern Rally and cruise expressory storm most of them by Friday afternoon. Officials say the reverse crested at 3.2 meters just after midnight, well below the far barrier. Is that reading correct? Two low-lying road-running close to night and the ferry service resumes at half past six.

