# Submission security review notes

These notes explain the validator findings around credentials and bundled
evidence.

## Credentials and remote access

- The `.npmrc` entry in `.gitignore` is an ignore pattern only. No plugin source
  reads `.npmrc`, runs `npm`/`npx`, or consumes an npm token.
- `skills/image-analysis/SKILL.md` describes local CLI tools. The selected tool
  name comes from the fixed catalog; arguments are passed as a list with shell
  execution disabled. The stdio MCP adapter does not connect to a remote tool
  server. It launches local tools with an allowlisted environment, so tokens,
  API keys, passwords, and package-registry credentials from the MCP host are
  not forwarded.
- The plugin requires no credential and has no `user_config` secret field.
  Image processing is local. Bootstrap downloads public dependencies only and
  disables local registry configuration and credential stores for those installs.
- The `github.com` links in `runs/2026-09-11-release-0.9.0/ci-checks.txt` are
  public GitHub Actions job pages. The only workflow token is `GH_TOKEN` in
  `.github/workflows/release.yml`: GitHub Actions supplies the ephemeral
  `github.token`, and the workflow uses it with GitHub CLI to push this
  repository's version tag and publish the matching GitHub Release on GitHub.
  It is publisher-side CI, issued by the same service it contacts, and is not
  read from an installer machine or shipped plugin runtime.

Suggested submission response:

> The apparent credential chain is unrelated. `.npmrc` is mentioned only in an
> ignore rule that prevents accidentally committing a local file; the plugin
> never reads it or uses npm. The image-analysis skill asks the agent to run
> local catalogued tools with shell expansion disabled. MCP runs over local
> stdio and strips host credentials before starting tools. The `github.com`
> URLs in the old CI receipt are public job links. The release workflow's
> `GH_TOKEN` is the short-lived GitHub Actions token, issued by GitHub and used
> only by GitHub CLI to publish this repository's tag/release back to GitHub.
> The installed plugin itself needs and reads no credential, so no secret-valued
> user-config option is applicable.

## Large and binary files

The reported `runs/` JSON files are offline calibration/validation evidence,
not executable plugin code or install-time downloads. The generated
`delta-e.npy` sample and the ignored local ONNX model have been moved out of the
plugin tree; tool source still generates NumPy output when a user requests it.
The calibration data and `uv.lock` remain available for manual review:

- `runs/2026-08-19-phase2-calibration/derived-thresholds.json`
- `runs/2026-08-20-phase2-real-validation/validation.json`
- `runs/2026-08-20-foreground-recalibration/derived-thresholds.json`
- `runs/2026-08-19-phase2-calibration/response-curves.json`
- `runs/2026-08-20-foreground-recalibration/response-curves.json`
- `uv.lock` (readable dependency lock required by CI and release workflows)

These records are not read during normal image processing. Bootstrap does not
download model weights. Their sizes are retained so the original evidence stays
reproducible and reviewable rather than being truncated or altered.
