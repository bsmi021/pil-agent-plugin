**0.8.0 — platform bootstrap and dependency diagnostics.** Adds a Python
bootstrap for a plugin-local environment on Windows, macOS, and Linux, with
opt-in OCR, embedding, and reconstruction dependencies. Its check command
distinguishes a recorded successful bootstrap from current readiness, rechecks
live dependencies, and skips repeat installation only when both pass. A bundled
bootstrap skill guides agents through setup and status checks.

OCR now discovers standard Windows Tesseract installations and supports an
environment override and image-free executable/language diagnostics. Embedding
diagnostics validate runtime/model setup; DLL and model-load failures preserve
exit 2 with empty stdout. PowerShell installation and model/profile configuration
commands are documented. Focused process tests exercise missing/discovered
dependencies, override precedence, paths with spaces, and bootstrap lifecycle.
Model weights remain caller-supplied; embedding claims and calibration results
are unchanged.

Full diff: https://github.com/bsmi021/pil-agent-plugin/commits/v0.8.0
