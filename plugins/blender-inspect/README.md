# blender-inspect

An agent plugin that lets coding agents see what a Blender scene actually
contains: depth, surface imperfections and topology defects, read from the
scene itself rather than guessed from flat renders.

Agents modeling in Blender usually get orthographic, flat-lit renders with no
depth data. Those hide flipped faces, dents, holes and interpenetrating parts.
The scene already holds exact depth and topology; this plugin exposes it.

It ships alongside [`pil-agent-plugin`](../../README.md) in the same repository
and marketplace. `pil-agent-plugin` stays the image-measurement plugin;
`blender-inspect` is the home of the Blender tools.

## Privacy and data sent

Everything runs locally in a headless Blender process. The plugin has no
telemetry, hosted service, account or API key. See [Privacy policy](PRIVACY.md).

## Requirements

- **Python 3.11+** for the host-side wrapper scripts (standard library only)
- **Blender** (tested with Blender 5.2). The scripts find it in this order:
  `--blender-executable`, the `BLENDER_INSPECT_BLENDER` environment variable,
  `C:/Program Files/Blender Foundation/Blender 5.2/blender.exe`, then `blender`
  on `PATH`.

Blender always runs with `--factory-startup --background`, so user add-ons and
preferences never load into an inspection run.

## Layout

| Path | Role |
|---|---|
| `plugin.json` | Portable Agent Plugins 1.0.0 manifest |
| `.claude-plugin/plugin.json` | Claude Code manifest |
| `.codex-plugin/plugin.json` | Codex manifest and interface metadata |
| `scripts/blender_common.py` | Blender discovery, headless launch, sentinel parsing and the refusal contract shared by every `blender_*.py` tool |
| `skills/`, `agents/` | Skills and the inspector agent |
| `tests/` | `test_bi_*.py`; Blender-dependent tests skip when Blender is missing |
| `evals/` | `claude plugin eval` suite |

Every tool follows one refusal contract: when it cannot answer (no Blender,
unreadable `.blend`, a probe that did not finish cleanly) it exits 2 with
byte-empty stdout and a one-line reason on stderr.

## Development

From the repository root:

```bash
uv run pytest -q plugins/blender-inspect/tests
```

## Status

**0.1.0 — Plugin scaffold and shared Blender launcher.** First release of
`blender-inspect` as a second plugin in the `pil-agent-plugin` marketplace,
with the four-manifest set and `scripts/blender_common.py`.

## License

MIT. See [LICENSE](LICENSE).
