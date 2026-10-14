# Resolved model revision

Resolved 2026-10-14 from the HF API (`?blobs=true`) and the engine's own
`share/nemo-speech/model-index.json`. The two revisions are **different commits**.

| source | revision | size (B) | sha256 |
|---|---|---:|---|
| HF `main` | `ea30d66debe3740a08b573244286791d423d6b3e` | 742090464 | `3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1` |
| **engine index (chosen)** | `1c8deaecc64b91f034d73e08dd8b64625eb3395d` | 741548352 | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |

The downloaded GGUF is the engine-index revision; its sha256 verified with
`sha256sum -c` (OK). The HF `main` pointer is `ea30d66d…` at 742,090,464 B,
sha256 `3fc991d3…` — 542,112 B larger, a different commit the engine does not
pin. `huggingface_hub`'s `/api/models?revision=<sha>` fell back to `main`, so the
pinned revision was confirmed with the `resolve/<sha>` LFS headers instead
(`x-repo-commit`, `x-linked-size`, `x-linked-etag`).
