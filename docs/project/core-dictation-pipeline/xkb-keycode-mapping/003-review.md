# 003 — Re-review: layout-aware text→keycode mapping (ticket 22)

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/xkb-keycode-mapping`, HEAD `5a45961` (the fix); the reviewed
  implementation is `d58a50d`; base of the branch is `36aad65`
  (`git diff main...feat/xkb-keycode-mapping`, three dots)
- **Reviewer:** independent re-review (judging the fix and its blast radius)
- **Status:** **pass** — the single blocking finding in `002-review.md` §2 is
  resolved. 0 disagreements across my own independent cross-check.

I did not redo `002`'s accepted checks (ctypes binding, memory/lifecycle,
caching, error types, imports, scope-of-modules). I re-ran the ones the fix
could plausibly move (ranking, tests, mypy, ADR 0019, scope) and rebuilt the
`xkbcli` cross-check from scratch.

## Verdict

The fix is correct and minimal. The reverse lookup now ranks a canonical keysym
ahead of a function-key alias, and my own run of `xkbcli how-to-type` across all
five keymaps finds **0 disagreements**. The blocking finding from `002` §2 is
**resolved**: the 30 pre-fix disagreements (hu 5, us 3, de 3, fr 14, live 5) all
now agree, and nothing that previously agreed was perturbed. Ticket 23 is
therefore **unblocked**.

One non-blocking documentation inaccuracy is recorded in §5 (the addendum and
docstring overstate the role of the `0x01000000 | cp` clause). It does not
change behaviour and does not block.

## 1. Independent cross-check vs `xkbcli how-to-type` — 0 disagreements

### Method

I regenerated the reference myself (I did **not** trust `/tmp/crosscheck_fixed.out`):

- Reference: `xkbcli how-to-type --layout <hu|us|de|fr> -- <char>` and
  `xkbcli how-to-type --keymap=/tmp/rereview_live_keymap.xkb -- <char>`, run per
  character. I dumped the live keymap fresh with `xkbcli dump-keymap-wayland`
  (1560 lines, `xkb_symbols "pc_hu_inet(evdev)_capslock(ctrl_modifier)"`), not a
  captured copy.
- I parsed the **full direct-access section** (every row, all keycodes), not just
  the first row.
- Module side: `XkbKeymap.from_names(...)` / `from_string(live)` and the
  per-character resolution, converted `Linux = XKB − 8`.
- Agreement rule (the addendum's): if `how-to-type` lists a direct access, the
  module must emit one of those XKB keycodes; if the module is unreachable while
  `how-to-type` has a direct access, that is a disagreement.

Bank = the literal 71-character set from `001-comment.md`:

```
0123456789abcxyzöüóőúűéáíÖÜÓŐÚŰÉÁÍ!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~ „”€°
```

### Result

| layout | agree | disagree | both unreachable |
|---|---|---|---|
| hu | 71 | **0** | 0 |
| us | 50 | **0** | 21 |
| de | 55 | **0** | 16 |
| fr | 54 | **0** | 17 |
| live (hu + `caps:ctrl_modifier`) | 71 | **0** | 0 |
| **total** | **301** | **0** | **54** |

That is **71 × 5 = 355 character/layout combinations, 0 disagreements**. I also
ran a deliberately broader sweep of **432 characters** (every printable ASCII
character plus Hungarian/Latin letters, Latin-1 supplement, Latin Extended-A and
U+2000–U+206F) across the same five keymaps: **0 disagreements** (712 agree,
1445 both-unreachable, 3 module-only — the `de` alias cases in §5). The broad
sweep is what closes the "silently dropped character" worry below.

### The 30 formerly-disagreeing cases, confirmed fixed

Every one now resolves to a keycode in `how-to-type`'s set. Module Linux code
(XKB in parentheses), with the matching `how-to-type` row:

| Layout | char | module Linux (XKB) | module mods | `how-to-type` XKB | `how-to-type` mods |
|---|---|---|---|---|---|
| hu | `=` | 8 (16) | Shift | 16 `AE07` | Shift |
| hu | `/` | 7 (15) | Shift | 15 `AE06` | Shift |
| hu | `#` | 45 (53) | Mod5 | 53 `AB02` | Mod5 |
| hu | `*` | 53 (61) | Mod5 | 61 `AB10` | Mod5 |
| hu | `+` | 4 (12) | Shift | 12 `AE03` | Shift |
| us | `#` | 4 (12) | Shift | 12 `AE03` | Shift |
| us | `*` | 9 (17) | Shift | 17 `AE08` | Shift |
| us | `+` | 13 (21) | Shift | 21 `AE12` | Shift |
| de | `=` | 11 (19) | Shift | 19 `AE10` | Shift |
| de | `/` | 8 (16) | Shift | 16 `AE07` | Shift |
| de | `*` | 27 (35) | Shift | 35 `AD12` | Shift |
| fr | `0`–`9` | 11,2,3,4,5,6,7,8,9,10 (19,10…18) | Shift | 19,10…18 | Shift |
| fr | `.` | 51 (59) | Shift | 59 `AB08` | Shift |
| fr | `/` | 52 (60) | Shift | 60 `AB09` | Shift |
| fr | `#` | 4 (12) | Mod5 | 12 `AE03` | Mod5 |
| fr | `+` | 13 (21) | Shift | 21 `AE12` | Shift |
| live | `= / # * +` | 8, 7, 45, 53, 4 | Shift/Mod5 | same as `hu` | same as `hu` |

A stricter automated pass over all 301 resolvable bank cases also checks that
the module's modifier set is a subset of the matching row's modifiers (normalising
`LevelThree`↔`Mod5`, ignoring `Lock`): **0 problems**.

### Multi-row direct access (the "first row" trap)

`hu '&'` is not unique. 42 bank characters have more than one keycode in their
direct-access set; the module chose a keycode **in the set** in every case. The
flagged case and a sample:

| layout | char | `how-to-type` keycodes | module XKB | verdict |
|---|---|---|---|---|
| hu | `&` | 45, 54 | **54** | legitimate — 54 is `AB03` level 3 `[Mod5]`, one modifier; 45 is `AC08` `[Shift Mod5]`, two. The tie-break correctly picks the cheaper row. |
| hu | `í`/`Í` | 31, 44, 94 | 94 | in set |
| hu | `<` | 52, 58, 94 | 58 | in set |
| fr | `$` | 13, 35, 442 | 35 | in set |
| fr | `>` | 53, 94 | 94 | in set |

## 2. The 71-vs-77 bank discrepancy — resolved, nothing hidden

`002`'s prose said "77" but its literal bank is 71. Working it out:

- The described set is digits (10) + `abcxyz` (6) + `öüóőúűéáí` (9) + their
  uppercase (9) + all 32 ASCII punctuation characters + space (1) + `„”€°` (4)
  = **71**.
- `002`'s own totals are `hu 72/77`, `us 74/77`, `de 74/77`, `fr 63/77`, i.e.
  72 agree + 5 disagree = 77. My 71-char run gives hu 66 agree + 5 disagree = 71.
  The difference is exactly **6 agreeing characters**.
- The literal bank contains lowercase `abcxyz` but *not* uppercase `ABCXYZ`.
  `002`'s phrase "their uppercase forms" evidently included the uppercase of
  `abcxyz`, giving **77**.

So the correct complete bank is **77** if `ABCXYZ` is counted, and the addendum's
literal bank (71) silently dropped those 6. I tested them explicitly on all five
keymaps: `A B C X Y Z` agree with `how-to-type` everywhere (e.g. `hu` `Y`→XKB 52,
`Z`→XKB 29, the swapped positions; `fr` `A`→24, `Z`→25). They are also inside my
432-character broad sweep. **No disagreement is hidden by the 6-character
omission.** The addendum is right that 71 is its literal set; the "77" in `002`
is the 71 + `ABCXYZ`.

## 3. Falsification of the fix

**Neutralised ranking.** I set `alias = 0` (removing the canonical-first flag)
and ran the suite. The bug reappeared exactly as reported:

- `test_hu_equal_uses_the_real_key_not_keypad_equal`: `hu '='` →
  `[(117,T),(117,F)]` (`KEY_KPEQUAL`).
- `test_fr_zero_uses_the_main_row_not_the_numeric_keypad`: `fr '0'` →
  `[(512,T),(512,F)]` (`KEY_NUMERIC_0`).
- `2 failed, 24 passed` — exactly the two regression tests, nothing else.

My own cross-check under the same neutralised ranking reproduces **30
disagreements — hu 5, us 3, de 3, fr 14, live 5** — with the identical
characters and the identical old module keycodes (`KP_Equal` 117, `KP_Divide`
98, `KP_Multiply` 55, `KP_Add` 78, `XF86NumericPound` 523, `XF86Numeric0…9`
512–521). This is an independent confirmation of both the pre-fix count and the
fix's effect.

**Restored.** `git checkout -- src/ges2t/injectors/keymap.py`; md5
`2674a8520099933a6d2ab68a7e3c8977` before and after, `git status` clean, HEAD
still `5a45961`. No probe remains.

**Other direction.** The fix did not break anything that previously agreed.
In-memory comparison of the pre-fix ranking against the shipped ranking over
every codepoint each keymap can produce (`_build_resolutions` re-simulated with
the same helpers) changed **exactly** the 30 alias codepoints per layout and
nothing else.

## 4. Blast radius of ranking canonical-first

- **No cheaper alias can now lose to a more expensive canonical match without
  reason.** `alias` is only the first tuple element; the rest of the tie-break
  `(bit_count, mask, level, layout, keycode)` is unchanged, so among canonical
  matches the module still picks the fewest modifiers, then the lowest mask,
  level, layout and keycode.
- **It cannot select a key the layout cannot reach.** The canonical winner is a
  keysym the module read out of this keymap at a level whose mask it verified
  against a real `xkb_state`; the chosen `(keycode, mask)` is therefore reachable
  by construction. Every one of the 30 is in `how-to-type`'s direct-access set.
- **Only the 30 moved.** Pre-fix vs shipped over all codepoints: hu 5, us 3,
  de 3, fr 14, live 5 changed; nothing else. The changed cases all gain the
  modifier the real key needs (`= + * / #`, `fr` digits) and lose the
  modifier-free keypad/phone alias — the intended trade.

## 5. Preserved alias-only cases (`de` `…`/`′`/`″`) and the U-form clause

Real calls confirm they still resolve (and `how-to-type` reports **no** direct
access for them):

```
de '…' -> [(100,T),(52,T),(52,F),(100,F)]          (AltGr + XKB 60)
de '′' -> [(100,T),(41,T),(41,F),(100,F)]          (AltGr + XKB 49)
de '″' -> [(42,T),(100,T),(41,T),(41,F),(100,F),(42,F)]  (Shift+AltGr + XKB 49)
```

**But the addendum's and docstring's claim that the `0x01000000 | cp` clause is
"exactly what keeps" them resolving is inaccurate.** I enumerated every keysym
candidate for those codepoints on `de`: each has exactly **one** candidate key
and level (`…` XKB 60 level 2, `′` XKB 49 level 2, `″` XKB 49 level 3), all
`0x01002026`/`0x01002032`/`0x01002033`. Because the shipped code **ranks
aliases, it does not reject them**, a sole alias candidate still wins even with
`alias = 1`. I confirmed directly: removing only the `or sym == (0x01000000 |
cp)` clause leaves **all 26 tests passing** and produces byte-identical events
for all three characters.

The clause *is* load-bearing only under a **reject-aliases** policy: simulating
"accept only canonical keysyms" with the U-form clause keeps `…`/`′`/`″`
resolving, while the same policy **without** the clause makes all three
unreachable. So the clause is a correct and harmless part of `002`'s recommended
predicate and future-proofs the tie-break, but the addendum overstates its role
in the code that actually shipped. This is a documentation nit, not a behavioural
defect, and it does not block.

## 6. Nothing else moved

- **Keypad/phone codes.** Across every codepoint resolvable on `hu`, `us`, `de`,
  `fr` and the live keymap, the shipped map selects **0** keycodes in the
  `KEY_KP*`/`KEY_KPNUM` range (55, 71–83, 78, 98, 117) or the `KEY_NUMERIC_*`
  range (0x200–0x20B).
- **Keycode unit unchanged.** `LINUX_KEYCODE_OFFSET = 8` and the single
  `keycode = key - LINUX_KEYCODE_OFFSET` boundary are identical between
  `d58a50d` and HEAD (the fix diff does not touch them). `KeyEvent.keycode`
  remains Linux/evdev; **ticket 23 adds no offset**. Per the constraint I did
  **not** re-run the live round trip; the unit rests on `002`'s documented
  ruling plus the unchanged offset.
- **Ticket text.** The stale research numbers now read
  "`KEY_*` codes emitted are `26` / `43` (research XKB keycodes: `AD11`=34,
  `BKSL`=51; `Linux = XKB − 8`)" — unambiguous, with the unit stated. The only
  remaining `34`/`51` in the ticket are explicitly labelled research XKB. This
  cannot mislead ticket 23.
- **`_Resolution.rank` widened 5 → 6.** The annotation is
  `tuple[int, int, int, int, int, int]` (line 214); it is constructed in exactly
  one place (line 725) with a 6-element tuple and compared in exactly one place
  (line 724, `rank < existing.rank`). No 5-tuple is constructed or compared
  anywhere; nothing outside `keymap.py` references `_Resolution` or `.rank`.
  ADR 0019 explicitly permits a private helper tuple consumed internally.

## 7. Mechanical checks

- `uv run pytest -q` → **373 passed**; `uv run pytest tests/test_keymap.py -q`
  → **26 passed** (23 test functions, one parametrised ×3). The fix diff adds
  tests only: no `-def test`, no `skip`, no `xfail`. `d58a50d` had 20 test
  functions → 21 collected; HEAD has 23 → 26, i.e. the claimed +5 regression
  cases. No test was deleted, skipped or weakened.
- `rm -rf .mypy_cache && uv run mypy src/ges2t tests` → **Success: no issues
  found in 31 source files** (cache cleared first, so this is not stale).
- ADR 0019: `KeyEvent`, `KeySequence` and `_Resolution` are frozen dataclasses
  with named fields (no positional public tuples); every `= None` parameter has
  an explicit `| None` (no implicit `Optional`); `disallow_untyped_defs` +
  `no_implicit_optional` pass. `uv run ruff check` on the two files → clean.
- Scope of the fix commit `5a45961`: `src/ges2t/injectors/keymap.py`,
  `tests/test_keymap.py`, `ticket.md`, `001-comment.md` — nothing else.
  `git status` clean; no `main` commit.

## Could not verify

- The real application-level effect of the 30 corrections in the user's terminal
  or editor. Re-running the injection probe was explicitly forbidden, and no
  keystrokes were injected and no window was opened.
- Behaviour on layouts beyond `hu`, `us`, `de`, `fr` and the live keymap; the
  algorithm is generic and the sweep was exhaustive only for those five.
- The byte-exact live round trip itself; it is recorded in `002` and I relied on
  it rather than re-running it.

## Non-blocking notes

- The addendum/docstring overstate the `0x01000000 | cp` clause as "exactly what
  keeps" the `de` characters resolving (§5). Correct to "is what would keep them
  resolving under a canonical-only policy; under the shipped rank-don't-reject
  algorithm they resolve either way."
- `_scan_key_for_mod` still carries the dead `exact` local noted in `002`
  (`return exact if exact is not None else partial`, where `exact` is never
  assigned). Harmless; a tidy-up, not a defect.

## Ticket 23 status

**Unblocked.** `sequence_for(text)` returns a `KeySequence` whose
`KeyEvent.keycode` values are Linux/evdev `KEY_*` codes for the keymap it was
compiled from, with modifiers framed press-before / release-after in reverse.
Ticket 23 must pass `event.keycode` **straight** to
`ydotool key <code>:1 <code>:0` with **no offset**, and must build a fresh
`XkbKeymap` per utterance (never cache globally). The blocking `002` §2 alias
defect no longer exists, so ticket 23 can rely on the mapping.

## Appendix — how to reproduce this re-review

```
# reference (independent of the module)
xkbcli dump-keymap-wayland > /tmp/rereview_live_keymap.xkb
xkbcli how-to-type --layout hu -- '='      # expects KEYCODE 16 AE07 [ Shift ]
xkbcli how-to-type --layout fr -- '0'      # expects KEYCODE 19 AE10 [ Shift ]
xkbcli how-to-type --layout hu -- '&'      # two rows: 45 (AC08) and 54 (AB03)

# module side (from the worktree)
uv run pytest -q                            # 373 passed
uv run pytest tests/test_keymap.py -q       # 26 passed
rm -rf .mypy_cache && uv run mypy src/ges2t tests   # 31 files, 0 errors
```

Cross-check scripts used: `/tmp/rr_reference.py`, `/tmp/rr_compare.py`,
`/tmp/rr_module_dump.py`, `/tmp/rr_sim.py`, `/tmp/rr_detail.py`,
`/tmp/rr_alias_candidates.py`, `/tmp/rr_modcheck.py`, `/tmp/rr_abcxyz.py`
(none are in the repository; the only repository write is this review).
