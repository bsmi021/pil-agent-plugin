"""Discover public CLI contracts and execute bounded, failure-preserving batches."""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from pil_io import emit, read_json, write_json
from pil_environment import tool_environment

TOOL_VERSION = "0.9.5"
ROOT = Path(__file__).resolve().parent
_LOCK = threading.RLock()
# MCP hosts can carry their own authentication environment. The local tool
# subprocesses have no need for it, so inherit only OS paths/temp settings and
# the plugin's documented model/OCR path settings.
MUTATING = {
    "pil_normalize",
    "pil_mask",
    "pil_register",
    "pil_diff_regions",
    "pil_calibrate",
    "pil_crop",
    "pil_annotate",
    "pil_blender_fit",
    "pil_blender_render",
    "pil_character_sheet_review",
    "pil_multiview_render",
    "pil_reconstruct",
    "pil_ocr",
    "pil_semantic_record",
    "pil_bootstrap",
    "pil_pipeline",
    "pil_capabilities",
}
REQUIREMENTS = {
    "pil_embed": ["onnxruntime", "caller_model"],
    "pil_ocr": ["tesseract"],
    "pil_register": ["cv2"],
    "pil_multiview_prepare": ["cv2"],
    "pil_multiview_solve": ["scipy"],
    "pil_reconstruct": ["cv2", "scipy", "blender"],
}


def names():
    result = []
    for path in sorted(ROOT.glob("pil_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if (
            any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "main"
                for node in tree.body
            )
            and path.stem != "pil_mcp"
        ):
            result.append(path.stem)
    return result


class _ParserReady(BaseException):
    def __init__(self, parser):
        self.parser = parser


def _schema(parser):
    arguments = []
    commands = {}
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            commands = {key: _schema(value) for key, value in action.choices.items()}
            arguments.append(
                {
                    "name": action.dest,
                    "type": "string",
                    "choices": list(commands),
                    "required": action.required,
                    "position": "subcommand",
                }
            )
            continue
        kind = (
            "boolean"
            if isinstance(
                action, (argparse._StoreTrueAction, argparse._StoreFalseAction)
            )
            else {int: "integer", float: "number"}.get(action.type, "string")
        )
        arguments.append(
            {
                "name": action.dest,
                "flags": action.option_strings,
                "type": kind,
                "required": action.required,
                "nargs": action.nargs,
                "repeatable": isinstance(action, argparse._AppendAction),
                "choices": list(action.choices) if action.choices is not None else None,
                "default": action.default
                if isinstance(
                    action.default, (str, int, float, bool, list, dict, type(None))
                )
                else str(action.default),
                "description": action.help,
            }
        )
    return {
        "description": parser.description,
        "arguments": arguments,
        "commands": commands,
    }


def cli_schema(name):
    # Capture the actual argparse contract before parsing or executing any command.
    with _LOCK:
        module = importlib.import_module(name)
        original = argparse.ArgumentParser.parse_args

        def capture(parser, *args, **kwargs):
            raise _ParserReady(parser)

        argparse.ArgumentParser.parse_args = capture
        try:
            module.main([])
        except _ParserReady as ready:
            return _schema(ready.parser)
        finally:
            argparse.ArgumentParser.parse_args = original
    raise ValueError(f"{name} did not expose an argparse contract")


def _available(dependency):
    if dependency == "caller_model":
        import os

        path = os.environ.get("PIL_AGENT_EMBED_MODEL")
        return bool(path and Path(path).is_file())
    if dependency == "blender":
        from pil_blender_mesh import resolve_blender_executable

        return resolve_blender_executable(None) is not None
    if dependency == "tesseract":
        from pil_ocr import _find_tesseract

        try:
            return bool(_find_tesseract(None))
        except Exception:
            return False
    return importlib.util.find_spec(dependency) is not None


def catalog():
    tools = []
    for name in names():
        requirements = REQUIREMENTS.get(
            name,
            (
                ["blender"]
                if name.startswith("pil_blender")
                or name in ("pil_character_sheet_review", "pil_multiview_render")
                else []
            ),
        )
        schema = cli_schema(name)
        tools.append(
            {
                "name": name,
                "description": schema["description"] or name,
                "cli_schema": schema,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "argv": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "CLI tokens; see cli_schema for flags, types, choices and subcommands.",
                        },
                        "timeout": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 1800,
                            "default": 300,
                        },
                    },
                    "required": ["argv"],
                    "additionalProperties": False,
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "exit_code": {"type": ["integer", "null"]},
                        "result": {"type": ["object", "array", "null"]},
                        "error": {"type": ["string", "null"]},
                    },
                    "required": ["ok", "exit_code", "result", "error"],
                },
                "mutates": name in MUTATING,
                "requirements": {r: _available(r) for r in requirements},
                "readiness_scope": "dependency discovery only; execution validates engines, model and input files",
                "cost_class": "external_engine" if requirements else "local_cpu",
                "measurement_status": (
                    "diagnostic_or_demoted"
                    if name
                    in (
                        "pil_components",
                        "pil_silhouette",
                        "pil_alignment",
                        "pil_register",
                        "pil_diff_regions",
                    )
                    else "read_result_limits_and_configuration"
                ),
                "argument_discovery": f"{name} --help; cli_schema includes subcommands",
            }
        )
    return {
        "tool": "pil_capabilities",
        "version": TOOL_VERSION,
        "schema": "capability-catalog-v1",
        "tools": tools,
    }


def invoke(tool, argv, timeout=300):
    if tool not in names():
        return {
            "ok": False,
            "exit_code": None,
            "result": None,
            "error": "unknown public tool",
        }
    if (
        not isinstance(argv, list)
        or not all(isinstance(a, str) for a in argv)
        or not isinstance(timeout, (int, float))
        or not 1 <= timeout <= 1800
    ):
        return {
            "ok": False,
            "exit_code": None,
            "result": None,
            "error": "argv must be strings and timeout must be between 1 and 1800 seconds",
        }
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / f"{tool}.py"), *argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=tool_environment(),
            shell=False,
        )
        try:
            result = json.loads(proc.stdout) if proc.stdout.strip() else None
        except ValueError:
            return {
                "ok": False,
                "exit_code": proc.returncode,
                "result": None,
                "error": proc.stderr.strip() or "tool returned non-JSON output",
            }
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "result": result,
            "error": proc.stderr.strip()
            or (None if proc.returncode == 0 else "tool reported a nonzero status"),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "exit_code": None, "result": None, "error": str(exc)}


def batch(jobs):
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 100:
        raise ValueError("batch must contain 1 to 100 jobs")
    items = []
    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            items.append(
                {
                    "id": str(i),
                    "ok": False,
                    "exit_code": None,
                    "result": None,
                    "error": "job must be an object",
                }
            )
            continue
        items.append(
            {
                "id": job.get("id", str(i)),
                **invoke(job.get("tool"), job.get("argv"), job.get("timeout", 300)),
            }
        )
    return {
        "tool": "pil_capabilities",
        "version": TOOL_VERSION,
        "command": "batch",
        "items": items,
        "ok": all(item["ok"] for item in items),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", help="return one tool contract")
    parser.add_argument(
        "--batch", help="JSON array of id/tool/argv jobs; failures retained"
    )
    parser.add_argument("--output", help="new JSON receipt file")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="compact batch summary; requires --output to preserve full results",
    )
    args = parser.parse_args(argv)
    try:
        if args.summary and (not args.output or not args.batch):
            raise ValueError("--summary requires --batch and --output")
        result = batch(read_json(args.batch)) if args.batch else catalog()
        if args.tool:
            if args.batch:
                raise ValueError("--tool cannot be combined with --batch")
            found = [item for item in result["tools"] if item["name"] == args.tool]
            if not found:
                raise ValueError("unknown public tool")
            result = {
                "tool": "pil_capabilities",
                "version": TOOL_VERSION,
                "tools": found,
            }
        if args.output:
            write_json(args.output, result)
        if args.summary:
            emit(
                {
                    "tool": "pil_capabilities",
                    "version": TOOL_VERSION,
                    "ok": result["ok"],
                    "artifact": args.output,
                    "items": [
                        {k: item[k] for k in ("id", "ok", "exit_code", "error")}
                        for item in result["items"]
                    ],
                }
            )
        else:
            emit(result)
        return 0 if result.get("ok", True) else 1
    except (ValueError, OSError, KeyError) as exc:
        print(f"pil_capabilities: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
