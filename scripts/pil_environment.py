"""Environment allowlist for plugin-launched local subprocesses."""

from __future__ import annotations

import os
from collections.abc import Mapping


# Keep only execution paths, OS temp/profile locations, and the plugin's
# documented local model/OCR settings. Unknown environment variables (including
# tokens, passwords, index URLs and proxy credentials) are not inherited.
_ALLOWED = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA",
    "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "TESSDATA_PREFIX", "PIL_AGENT_TESSERACT", "PIL_AGENT_EMBED_MODEL",
    "PIL_AGENT_EMBED_PREPROCESSING", "LANG", "LC_ALL", "LC_CTYPE",
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
}


def tool_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a fresh child environment from known non-secret settings only."""
    environment = os.environ if source is None else source
    return {
        key: environment[key]
        for key in _ALLOWED
        if key in environment
    }
