# Phase 2 WP2 — calibration bundle NOT YET GENERATED

**Date:** 2026-08-19
**Status:** the harness is written and complete; **the run has not been executed**,
so this directory contains **no measurements**.

There are no derived thresholds, no detection limits and no constant verdicts in
this bundle yet, because none have been measured. Nothing in this file is a
result. Delete this file once `run_all.py` has produced the real ledger.

## Why

Every command-execution path available to the agent that authored
`calibration/` was refused by the harness it was running under. The session was
non-interactive, so the approval prompts auto-denied. Exact commands attempted,
all of which returned `This command requires approval`:

```
python calibration/_probe.py
python C:/Projects/.../calibration/_probe.py     (also with sandbox disabled)
.venv/Scripts/python.exe calibration/_probe.py
python -c "import PIL, numpy; ..."
python -m compileall -q calibration
uv run python --version
uv run pytest --version
pytest --version
```

`python --version`, `git status` and `ls` were permitted, which is how the
restriction was identified as an allowlist covering read-only commands rather
than a missing interpreter. The repository's `.venv` and `uv.lock` are present
and intact; nothing is wrong with the environment.

**Consequence: the calibration code has never been executed.** It was written
against the tools' actual source and desk-checked, but it carries no evidence of
running. Expect to fix one or two small runtime errors on first execution.

## What to run

From the repository root:

```
uv run python calibration/run_all.py --quick --once     # ~1 min smoke test, writes to --out
uv run python calibration/run_all.py                    # the real run
```

The full run regenerates this directory:

| File | Contents |
|---|---|
| `derived-thresholds.json` | control sets, per-metric thresholds with n / α / point estimate / bootstrap CI, detection-limit table, the four constant-specific derivations, metric-demotion analysis, constant verdicts |
| `response-curves.json` | every metric's response over every perturbation family's magnitude × extent grid, with monotonicity checks |
| `lch-hue-boundaries.json` | derived LCh hue-angle intervals and the HSV-vs-LCh accent-gate comparison |
| `README.md` | the ledger |

It runs the whole pipeline **twice** and asserts the three JSON payloads are
byte-identical between passes; it exits non-zero if they are not. Roughly
2,700 tool invocations per pass across a thread pool — budget 8–15 minutes total
on a desktop, and use `--jobs N` if the default worker count is wrong for the
machine.

## What must not be done from this bundle yet

- Do **not** apply any constant to `scripts/`. There are no derived constants
  until the run happens.
- Do **not** treat the numbers in `calibration/derive.py` (`CHANGE_AREA_BUDGET`,
  the candidate gate grids, `LCH_GATE_C_MIN`/`L_MIN`) as results. They are
  *inputs*: search ranges and stated criteria, chosen before any measurement.

## Known gap that survives execution

The scope also asks for **a small real-image validation set**, to check that
synthetic-derived thresholds do not fall apart on genuine input. This repository
distributes no images — the phase 1 reference is private and gated behind
`PIL_AGENT_REFERENCE_IMAGE` — so that check is not part of this harness. WP2's
gate in `docs/phase2-scope.md` ("thresholds beat current guesses on the real
validation set") therefore stays open even after a clean run.
