"""Shared paths, console output and small helpers for the unity2web harness."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(os.environ.get("U2W_TOOLS_DIR", ROOT / "tools"))
WORK_DIR = Path(os.environ.get("U2W_WORK_DIR", ROOT / "work"))
UNITY_FILES = ROOT / "unity"
DOTNET_DIR = ROOT / "dotnet"
MANIFEST = ROOT / "tools.json"

for _stream in (sys.stdout, sys.stderr):
    # Windows consoles and pipes default to a legacy code page; never crash on a check mark.
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def step(message: str) -> None:
    print(_paint("1;36", "==> ") + _paint("1", message), flush=True)


def info(message: str) -> None:
    print("    " + message, flush=True)


def ok(message: str) -> None:
    print(_paint("32", "  ✓ ") + message, flush=True)


def warn(message: str) -> None:
    print(_paint("33", "  ! ") + message, flush=True)


def error(message: str) -> None:
    print(_paint("31", "  ✗ ") + message, file=sys.stderr, flush=True)


class HarnessError(RuntimeError):
    """A failure the user can act on; printed without a traceback."""


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonc(path: Path) -> Any:
    """JSON with whole-line // comments and trailing commas (the format of rules.json)."""
    text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def manifest() -> dict:
    return load_json(MANIFEST, {})


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._")
    return slug or "game"


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def run(cmd: Sequence[str | os.PathLike], *, cwd: Path | None = None, log: Path | None = None,
        check: bool = True, env: dict | None = None, timeout: float | None = None,
        input_text: str | None = None) -> subprocess.CompletedProcess:
    """Runs a command, streaming output to the console and optionally tee-ing it into a log file."""
    args = [str(c) for c in cmd]
    info("$ " + " ".join(f'"{a}"' if " " in a else a for a in args))
    started = time.time()
    with subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                          text=True, encoding="utf-8", errors="replace") as proc:
        if input_text is not None:
            proc.stdin.write(input_text)
            proc.stdin.close()
        lines: list[str] = []
        sink = open(log, "a", encoding="utf-8") if log else None
        try:
            for line in proc.stdout:
                lines.append(line)
                if sink:
                    sink.write(line)
                print("      " + line.rstrip(), flush=True)
                if timeout and time.time() - started > timeout:
                    proc.kill()
                    raise HarnessError(f"timed out after {timeout:.0f}s: {args[0]}")
        finally:
            if sink:
                sink.close()
        code = proc.wait()
    result = subprocess.CompletedProcess(args, code, "".join(lines), "")
    if check and code != 0:
        raise HarnessError(f"command failed with exit code {code}: {Path(args[0]).name}")
    return result


def which(names: Iterable[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


class Workspace:
    """Per-game working folder: everything the pipeline produces lives under work/<slug>/."""

    def __init__(self, path: Path):
        self.path = path.resolve()

    @classmethod
    def for_game(cls, game: Path, explicit: Path | None = None) -> "Workspace":
        if explicit:
            return cls(explicit)
        return cls(WORK_DIR / slugify(game.resolve().name))

    @property
    def state_file(self) -> Path:
        return self.path / "state.json"

    @property
    def logs(self) -> Path:
        return self.path / "logs"

    @property
    def reports(self) -> Path:
        return self.path / "reports"

    @property
    def export_dir(self) -> Path:
        return self.path / "export"

    @property
    def backup_dir(self) -> Path:
        return self.path / "original-assemblies"

    @property
    def build_dir(self) -> Path:
        return self.path / "build" / "webgl"

    def project_dir(self) -> Path:
        """The Unity project inside the AssetRipper export (the folder holding Assets/ and ProjectSettings/)."""
        state = self.state()
        if state.get("project"):
            return Path(state["project"])
        for candidate in [self.export_dir / "ExportedProject", self.export_dir]:
            if (candidate / "Assets").is_dir():
                return candidate
        for assets in self.export_dir.rglob("Assets"):
            if assets.is_dir() and (assets.parent / "ProjectSettings").is_dir():
                return assets.parent
        raise HarnessError(f"no exported Unity project in {self.export_dir}; run 'export' first")

    def state(self) -> dict:
        return load_json(self.state_file, {})

    def update(self, **values: Any) -> dict:
        state = self.state()
        state.update(values)
        save_json(self.state_file, state)
        return state

    def ensure(self) -> "Workspace":
        for folder in (self.path, self.logs, self.reports):
            folder.mkdir(parents=True, exist_ok=True)
        return self
