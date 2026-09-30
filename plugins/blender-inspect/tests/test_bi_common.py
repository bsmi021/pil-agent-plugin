"""Tests for `plugins/blender-inspect/scripts/blender_common.py`.

Two tiers:

*   Hermetic: discovery order, command construction, sentinel parsing, probe
    failure handling and the refusal contract. These stub the filesystem, PATH
    and the subprocess runner, so they run in CI with no Blender.
*   Blender-gated: a real headless launch proves the flags do what the module
    claims (factory startup, background, a raising probe exits non-zero, the
    `.blend` opens, probe arguments arrive after `--`). They skip when no
    Blender is found.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402

BLENDER, _ = blender_common.resolve_blender_executable()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")


# --- discovery ---------------------------------------------------------------


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No env var, no Windows install, nothing on PATH, whatever this machine has."""
    monkeypatch.delenv("BLENDER_INSPECT_BLENDER", raising=False)
    monkeypatch.setattr(blender_common, "WINDOWS_BLENDER_5_2", str(tmp_path / "absent.exe"))
    monkeypatch.setattr(blender_common.shutil, "which", lambda name: None)
    return tmp_path


def _fake_exe(directory, name):
    path = directory / name
    path.write_text("", encoding="utf-8")
    return str(path)


def test_explicit_executable_wins_over_every_other_source(isolated, monkeypatch):
    explicit = _fake_exe(isolated, "explicit.exe")
    monkeypatch.setenv("BLENDER_INSPECT_BLENDER", _fake_exe(isolated, "env.exe"))
    monkeypatch.setattr(blender_common, "WINDOWS_BLENDER_5_2", _fake_exe(isolated, "win.exe"))

    assert blender_common.resolve_blender_executable(explicit) == (explicit, None)


def test_unresolvable_explicit_executable_is_refused_not_replaced(isolated, monkeypatch):
    monkeypatch.setenv("BLENDER_INSPECT_BLENDER", _fake_exe(isolated, "env.exe"))

    path, reason = blender_common.resolve_blender_executable(str(isolated / "nope.exe"))

    assert path is None
    assert reason.startswith("--blender-executable does not resolve")


def test_environment_variable_comes_second(isolated, monkeypatch):
    env = _fake_exe(isolated, "env.exe")
    monkeypatch.setenv("BLENDER_INSPECT_BLENDER", env)
    monkeypatch.setattr(blender_common, "WINDOWS_BLENDER_5_2", _fake_exe(isolated, "win.exe"))

    assert blender_common.resolve_blender_executable() == (env, None)


def test_unresolvable_environment_variable_is_refused(isolated, monkeypatch):
    monkeypatch.setenv("BLENDER_INSPECT_BLENDER", str(isolated / "nope.exe"))
    monkeypatch.setattr(blender_common, "WINDOWS_BLENDER_5_2", _fake_exe(isolated, "win.exe"))

    path, reason = blender_common.resolve_blender_executable()

    assert path is None
    assert reason.startswith("BLENDER_INSPECT_BLENDER does not resolve")


def test_environment_variable_may_name_a_command_on_path(isolated, monkeypatch):
    on_path = _fake_exe(isolated, "blender-5.2.exe")
    monkeypatch.setenv("BLENDER_INSPECT_BLENDER", "blender-5.2")
    monkeypatch.setattr(
        blender_common.shutil, "which", lambda name: on_path if name == "blender-5.2" else None
    )

    assert blender_common.resolve_blender_executable() == (on_path, None)


def test_windows_5_2_install_comes_before_path(isolated, monkeypatch):
    win = _fake_exe(isolated, "win.exe")
    monkeypatch.setattr(blender_common, "WINDOWS_BLENDER_5_2", win)
    monkeypatch.setattr(blender_common.shutil, "which", lambda name: "/usr/bin/blender")

    assert blender_common.resolve_blender_executable() == (win, None)


def test_blender_on_path_is_the_last_resort(isolated, monkeypatch):
    monkeypatch.setattr(
        blender_common.shutil, "which", lambda name: "/usr/bin/blender" if name == "blender" else None
    )

    assert blender_common.resolve_blender_executable() == ("/usr/bin/blender", None)


def test_nothing_found_names_the_whole_search_order(isolated):
    path, reason = blender_common.resolve_blender_executable()

    assert path is None
    assert "--blender-executable" in reason
    assert "BLENDER_INSPECT_BLENDER" in reason
    assert blender_common.WINDOWS_BLENDER_5_2 in reason
    assert "on PATH" in reason


def test_the_windows_default_is_the_blender_5_2_install():
    assert blender_common.WINDOWS_BLENDER_5_2 == (
        "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
    )


# --- command construction ----------------------------------------------------


def test_command_puts_every_flag_where_blender_reads_it():
    cmd = blender_common.build_command("blender", "probe.py", "scene.blend", ["--x", 3])

    assert cmd == [
        "blender",
        "--factory-startup",
        "--background",
        "scene.blend",
        "--python-exit-code",
        "1",
        "--python",
        "probe.py",
        "--",
        "--x",
        "3",
    ]


def test_command_without_blend_or_arguments():
    cmd = blender_common.build_command("blender", "probe.py")

    assert cmd == [
        "blender",
        "--factory-startup",
        "--background",
        "--python-exit-code",
        "1",
        "--python",
        "probe.py",
    ]


def test_argparse_options_are_shared(capsys):
    import argparse

    parser = argparse.ArgumentParser()
    blender_common.add_blender_arguments(parser)
    args = parser.parse_args(["--blender-executable", "b.exe", "--timeout", "12"])

    assert (args.blender_executable, args.timeout) == ("b.exe", 12)
    assert parser.parse_args([]).timeout == blender_common.DEFAULT_TIMEOUT_SECONDS


# --- sentinels ---------------------------------------------------------------


def _block(tool, body):
    begin, end = blender_common.sentinels(tool)
    return f"{begin}\n{body}\n{end}\n"


def test_sentinels_are_per_tool():
    assert blender_common.sentinels("blender_mesh_audit") == (
        "<<<BLENDER_INSPECT_BLENDER_MESH_AUDIT_BEGIN>>>",
        "<<<BLENDER_INSPECT_BLENDER_MESH_AUDIT_END>>>",
    )


def test_payload_is_lifted_out_of_blender_chatter():
    stdout = "Blender 5.2\nRead prefs\n" + _block("t", '{"a": 1}') + "Blender quit\n"

    assert blender_common.extract_payload(stdout, "t") == {"a": 1}


def test_payload_tolerates_crlf():
    stdout = _block("t", '{"a": 1}').replace("\n", "\r\n")

    assert blender_common.extract_payload(stdout, "t") == {"a": 1}


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "no sentinels at all",
        _block("t", '{"a": 1}') + _block("t", '{"a": 2}'),
        _block("t", '{"a": 1}').rsplit("<<<", 1)[0],
        _block("t", "not json"),
        _block("t", "[1, 2]"),
        _block("other", '{"a": 1}'),
    ],
    ids=["empty", "none", "two-blocks", "no-end", "not-json", "not-object", "other-tool"],
)
def test_malformed_probe_output_is_rejected(stdout):
    assert blender_common.extract_payload(stdout, "t") is None


def test_probe_header_defines_emit_and_args():
    source = blender_common.probe_source("t", "emit({'args': ARGS})\n")
    stdout = io.StringIO()
    namespace = {}
    real_stdout, real_argv = sys.stdout, sys.argv
    try:
        sys.stdout, sys.argv = stdout, ["blender", "--python", "p.py", "--", "a", "b"]
        exec(compile(source, "probe", "exec"), namespace)
    finally:
        sys.stdout, sys.argv = real_stdout, real_argv

    assert blender_common.extract_payload(stdout.getvalue(), "t") == {"args": ["a", "b"]}


# --- run_probe with a stubbed Blender ----------------------------------------


class _Runner:
    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.raises = raises
        self.cmd = None
        self.probe_text = None

    def __call__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.probe_text = Path(cmd[cmd.index("--python") + 1]).read_text(encoding="utf-8")
        if self.raises:
            raise self.raises
        return self.result


def test_run_probe_returns_the_payload_and_cleans_up():
    runner = _Runner(stdout="log\n" + _block("t", '{"ok": true}'))

    payload, error = blender_common.run_probe("blender", "t", "emit({})\n", runner=runner)

    assert (payload, error) == ({"ok": True}, None)
    assert "def emit(payload)" in runner.probe_text
    assert runner.kwargs["timeout"] == blender_common.DEFAULT_TIMEOUT_SECONDS
    assert not Path(runner.cmd[runner.cmd.index("--python") + 1]).exists()


def test_run_probe_reports_a_non_zero_exit_with_the_last_stderr_line():
    runner = _Runner(returncode=1, stderr="Traceback\n  ...\nValueError: boom\n\n")

    payload, error = blender_common.run_probe("blender", "t", "", blend="s.blend", runner=runner)

    assert payload is None
    assert error == "blender exited 1 on s.blend: ValueError: boom"


def test_run_probe_reports_a_missing_payload():
    payload, error = blender_common.run_probe("blender", "t", "", runner=_Runner(stdout="log\n"))

    assert payload is None
    assert error == "probe emitted no sentinel-wrapped payload"


def test_run_probe_reports_a_timeout():
    runner = _Runner(raises=subprocess.TimeoutExpired("blender", 5))

    payload, error = blender_common.run_probe("blender", "t", "", timeout=5, runner=runner)

    assert payload is None
    assert error == "blender timed out after 5s"


def test_run_probe_reports_a_spawn_failure():
    runner = _Runner(raises=FileNotFoundError("no such file"))

    payload, error = blender_common.run_probe("blender", "t", "", runner=runner)

    assert payload is None
    assert error.startswith("blender spawn failed:")


# --- refusal contract, checked through a real process ------------------------

_MINI_TOOL = """
import sys
sys.path.insert(0, {scripts!r})
import blender_common

def main():
    blender, reason = blender_common.resolve_blender_executable({explicit!r})
    if blender is None:
        return blender_common.refuse("blender_mini", reason + "\\nsecond line")
    blender_common.write_payload({{"blender": blender}})
    return 0

raise SystemExit(main())
"""


def test_refusal_is_exit_2_with_empty_stdout_and_one_stderr_line(tmp_path):
    tool = tmp_path / "blender_mini.py"
    tool.write_text(
        _MINI_TOOL.format(scripts=str(SCRIPTS), explicit=str(tmp_path / "missing.exe")),
        encoding="utf-8",
    )

    proc = subprocess.run([sys.executable, str(tool)], capture_output=True)

    assert proc.returncode == 2
    assert proc.stdout == b""
    lines = proc.stderr.decode("utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("blender_mini: --blender-executable does not resolve")
    assert lines[0].endswith("second line")


def test_write_payload_is_deterministic_json():
    stream = io.StringIO()
    blender_common.write_payload({"b": 1, "a": [1.5]}, stream)

    assert stream.getvalue() == json.dumps({"a": [1.5], "b": 1}, indent=2) + "\n"
    with pytest.raises(ValueError):
        blender_common.write_payload({"a": float("nan")}, io.StringIO())


# --- real headless Blender ---------------------------------------------------

_STATE_PROBE = """
import bpy
emit({
    "version": list(bpy.app.version),
    "background": bpy.app.background,
    "factory_startup": bpy.app.factory_startup,
    "filepath": bpy.data.filepath,
    "args": ARGS,
})
"""


@needs_blender
def test_headless_launch_is_background_factory_startup_and_passes_arguments():
    payload, error = blender_common.run_probe(
        BLENDER, "blender_state", _STATE_PROBE, script_args=["--views", "front"]
    )

    assert error is None
    assert payload["version"][0] >= 5
    assert payload["background"] is True
    assert payload["factory_startup"] is True
    assert payload["args"] == ["--views", "front"]


@needs_blender
def test_the_blend_file_is_opened_before_the_probe_runs(tmp_path):
    blend = tmp_path / "saved.blend"
    save = "import bpy\nbpy.ops.wm.save_as_mainfile(filepath=ARGS[0])\nemit({})\n"
    _, error = blender_common.run_probe(BLENDER, "blender_save", save, script_args=[blend])
    assert error is None and blend.is_file()

    payload, error = blender_common.run_probe(BLENDER, "blender_state", _STATE_PROBE, blend=blend)

    assert error is None
    assert Path(payload["filepath"]).resolve() == blend.resolve()


@needs_blender
def test_a_probe_that_raises_makes_blender_exit_non_zero():
    payload, error = blender_common.run_probe(
        BLENDER, "blender_boom", "raise RuntimeError('planted failure')\n"
    )

    assert payload is None
    assert error.startswith("blender exited 1")


# --- PNG metadata strip (hermetic; Pillow is only the test oracle) -----------


def _png_with_metadata(path):
    from PIL import Image, PngImagePlugin

    image = Image.new("RGBA", (5, 3))
    image.putdata([(x * 40, y * 80, 7, 255 - x) for y in range(3) for x in range(5)])
    info = PngImagePlugin.PngInfo()
    info.add_text("Date", "2026/09/29 12:00:00")
    info.add_text("RenderTime", "00:01.23")
    image.save(path, format="PNG", pnginfo=info)
    return image.tobytes()


def test_strip_png_metadata_drops_text_and_keeps_pixels(tmp_path):
    from PIL import Image

    path = tmp_path / "render.png"
    pixels = _png_with_metadata(path)
    assert b"tEXt" in path.read_bytes()

    blender_common.strip_png_metadata(path)

    data = path.read_bytes()
    assert b"tEXt" not in data and b"RenderTime" not in data
    with Image.open(path) as stripped:
        assert stripped.mode == "RGBA"
        assert stripped.tobytes() == pixels
    assert not list(tmp_path.glob("*.stripped"))


def test_strip_png_metadata_makes_differently_stamped_files_identical(tmp_path):
    from PIL import Image, PngImagePlugin

    image = Image.new("RGB", (4, 4), (1, 2, 3))
    paths = []
    for stamp in ("12:00:00", "12:00:01"):
        info = PngImagePlugin.PngInfo()
        info.add_text("Time", stamp)
        path = tmp_path / f"{stamp.replace(':', '')}.png"
        image.save(path, format="PNG", pnginfo=info)
        blender_common.strip_png_metadata(path)
        paths.append(path)

    assert paths[0].read_bytes() == paths[1].read_bytes()


@pytest.mark.parametrize(
    "mangle",
    [
        lambda data: b"GIF89a" + data[6:],
        lambda data: data[:-8],
        lambda data: data[:20] + bytes([data[20] ^ 0xFF]) + data[21:],
    ],
    ids=["signature", "truncated", "crc"],
)
def test_strip_png_metadata_refuses_a_malformed_png(tmp_path, mangle):
    path = tmp_path / "bad.png"
    _png_with_metadata(path)
    original = mangle(path.read_bytes())
    path.write_bytes(original)

    with pytest.raises(ValueError):
        blender_common.strip_png_metadata(path)
    assert path.read_bytes() == original


# --- host side is stdlib only (binding decision) -----------------------------


def test_every_blender_tool_imports_only_the_stdlib_and_blender_common():
    import ast

    allowed_local = {"blender_common"}
    offenders = {}
    for path in sorted(SCRIPTS.glob("blender_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in allowed_local or name == "__future__":
                    continue
                if name not in sys.stdlib_module_names:
                    offenders.setdefault(path.name, []).append(name)
    assert not offenders, f"host-side imports outside the stdlib: {offenders}"
