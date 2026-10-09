# Code style: full type annotations, interface-typed signatures, classes over tuples

- **Status:** accepted
- **Date:** 2026-10-09

## Context

After approving the ticket breakdown, the user added an explicit implementation standard:

> "make sure that the implementation agents use type hints everywhere, preferably depend on
> interfaces (not just when inheriting, in function params as well) and return classes instead
> of tuples of random strings ints etc."

This is a real enforcement problem, not a preference. The project's central requirement is
that the model runtime is abstracted away (ADR 0010), and an abstraction that is only used for
inheritance is not an abstraction: if a function takes `WhistleTranscriber` or returns a
`tuple[str, str]` whose positions the caller must remember, then the seam is decorative and
the swap described in ADR 0006 is not actually a one-module change.

It also directly serves the test strategy (ADR 0014): fakes can only be substituted at
interface-typed call sites, and a typed return class is trivially assertable in a way that
`result[1] == "..."` is not.

Three tickets in the approved breakdown currently specify tuple returns and must be corrected
to satisfy this ADR: `config-loading` (`tuple[Config, list[str]]`-shaped warnings),
`whistle-engine-config` (`from_mapping(...) -> tuple[WhistleConfig, list[str]]`), and
`streaming-window-engine` (`process(buffer) -> tuple[str, str]`).

## Decision

**Annotations are mandatory and complete.**

- Every function, method, and lambda that is not a throwaway has **all parameters and the
  return type annotated**, including `-> None`. This applies to tests and helper code, not
  just to `src/`.
- No bare `Any` in a signature where a real type is knowable. `Any` is permitted only at a
  genuine untyped boundary (the raw mapping of an `[engine.*]` TOML table, third-party JSON),
  and must be converted to a typed object at the earliest possible point.
- No implicit `Optional`: write `str | None`, never `str` with a documented "can be None".
- Third-party libraries without stubs (`gi`, `pystray`, `cactus_needle`) are handled with
  narrow, local `# type: ignore[...]` comments that name the specific code, never with a
  blanket module-level ignore and never by widening our own types to `Any`.

**Signatures depend on interfaces, not implementations.**

- Any parameter or return type that has (or could have) more than one implementation must be
  typed as the relevant `Protocol` from `interfaces.py` — `Transcriber`, `AudioSource`,
  `Injector`, `StatusUI`, `DictationSession` — or as a superset such as `Iterable[AudioBuffer]`.
- Concrete classes may appear **only** in three places: inside their own module, in the
  composition root (`pipeline.py` factories, ADR 0010's "one place" rule), and in a test that
  is specifically testing that concrete class.
- `Callable` parameters must state their full signature, e.g.
  `session_factory: Callable[[], DictationSession]`, not `Callable` or `Callable[..., Any]`.
- Dependencies are passed in, not imported and constructed by the callee. A constructor
  argument typed as an interface is the default shape for any collaborator.

**Return classes, not tuples.**

- A function returning more than one value returns a **frozen dataclass** (or a `NamedTuple`
  where a small immutable record is genuinely all that is needed) with named fields —
  `StreamingPass(committed: str, pending: str)`,
  `ConfigLoadResult(config: Config, warnings: list[str])`,
  `WhistleConfigResult(config: WhistleConfig, warnings: list[str])`.
- **Positional tuples are banned as public return values.** A private helper may use a tuple
  internally if it is consumed two lines later and is not exported; anything crossing a module
  boundary is a class.
- `tuple[...]` is still correct for genuinely homogeneous, order-agnostic sequences (for
  example `-> tuple[Path, Path]` is *not* acceptable; use
  `ModelPaths(model=Path, engine=Path)`), and for variadic forwarding.
- Raw `dict` may not be a public return value where a dataclass fits. Where a mapping must
  cross a boundary (raw TOML, raw engine JSON), type it `Mapping[str, Any]` and convert to a
  typed object at the earliest point.
- Distinguish failure with exceptions or a typed result object, **never** with a sentinel
  (`None`, `-1`, `""`) that the caller must special-case.

**Enforcement.**

- `mypy` is a development dependency, configured to check the project with
  `disallow_untyped_defs = true` and `no_implicit_optional = true`, ignoring missing imports
  for third-party libraries.
- **`uv run mypy src/ges2t tests` must exit 0**, in addition to `uv run pytest`, as part of the
  definition of done for every ticket. Test code is in scope: this ADR requires annotations
  "in tests and helper code", so a check limited to `src/ges2t` would not enforce its own rule.
  (Tickets written before 2026-10-09 name only `src/ges2t`; the wider command is the one that
  counts.) Ticket `project-scaffold` sets this up, and ticket `type-check-whole-tree` closed
  the initial gap this omission created in `tests/test_whistle_config.py`.
- This is a static check only. No runtime type-validation library (`pydantic`, `beartype`,
  `typeguard`) is added — the project stays stdlib-first (ADR 0001).

## Consequences

- Swapping the engine (ADR 0006) is a one-module change **only if** this rule holds, so a
  reviewer should treat an implementation-typed signature as a defect, not a nitpick.
- **Discovered 2026-10-09:** the original enforcement command (`mypy src/ges2t`) did not cover
  tests, and a genuine violation survived ticket 4's review as a result — exactly the kind of
  silent non-enforcement this ADR exists to prevent. The command is now
  `mypy src/ges2t tests`. This is recorded because the failure mode (an enforced rule whose
  check does not actually cover the rule's scope) is more dangerous than the violation itself.
- Test fakes become drop-in because every call site is interface-typed (ADR 0014); a signature
  that takes a concrete class is also a testability defect.
- Named return classes make call sites self-documenting and make it impossible to confuse two
  same-shaped strings — the `committed`/`pending` pair in the streaming decoder being the
  obvious case.
- Slightly more code: a dataclass for what could have been a two-element tuple. Accepted; the
  cost is a few lines per boundary and the benefit is the project's core requirement.
- `mypy` will occasionally disagree with a dynamic third-party API. The answer is a narrow,
  named `type: ignore`, not loosening our own annotations — and never a ticket-level exception.
