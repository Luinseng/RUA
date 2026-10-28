"""Finds or installs the matching Unity Editor (with WebGL support) and runs batch-mode builds."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .common import HarnessError, info, ok, run, step, warn

VERSION_DIR_RE = re.compile(r"^\d+\.\d+\.\d+[abcfpx]\d+$")
RELEASE_API = "https://services.api.unity.com/unity/editor/release/v1/releases"


@dataclass
class Editor:
    version: str
    exe: Path

    @property
    def root(self) -> Path:
        return self.exe.parent

    @property
    def has_webgl(self) -> bool:
        return (self.root / "Data" / "PlaybackEngines" / "WebGLSupport").is_dir()

    @property
    def managed_dirs(self) -> list[Path]:
        data = self.root / "Data"
        return [d for d in (data / "Managed" / "UnityEngine", data / "Managed") if d.is_dir()]


def _editor_roots() -> list[Path]:
    roots = [Path(p) for p in os.environ.get("U2W_UNITY_EDITORS", "").split(os.pathsep) if p]
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", "")):
        if base:
            roots.append(Path(base) / "Unity" / "Hub" / "Editor")
    appdata = os.environ.get("APPDATA")
    if appdata:
        secondary = Path(appdata) / "UnityHub" / "secondaryInstallPath.json"
        if secondary.exists():
            try:
                value = json.loads(secondary.read_text(encoding="utf-8"))
                if value:
                    roots.append(Path(value))
            except (json.JSONDecodeError, OSError):
                pass
    roots += [Path("/Applications/Unity/Hub/Editor"), Path.home() / "Unity" / "Hub" / "Editor"]
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


def _exe_in(version_dir: Path) -> Path | None:
    for candidate in (version_dir / "Editor" / "Unity.exe", version_dir / "Unity.app" / "Contents" / "MacOS" / "Unity",
                      version_dir / "Editor" / "Unity"):
        if candidate.exists():
            return candidate
    return None


def editors() -> list[Editor]:
    found: dict[str, Editor] = {}
    for root in _editor_roots():
        for child in root.iterdir():
            if child.is_dir() and VERSION_DIR_RE.match(child.name):
                exe = _exe_in(child)
                if exe:
                    found.setdefault(child.name, Editor(child.name, exe))
    return sorted(found.values(), key=lambda e: _version_key(e.version))


def _version_key(version: str) -> tuple:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)([abcfpx])(\d+)", version)
    if not match:
        return (0,)
    a, b, c, kind, n = match.groups()
    return int(a), int(b), int(c), "abcfpx".index(kind), int(n)


def pick_editor(version: str | None, *, explicit: Path | None = None, allow_minor_mismatch: bool = False) -> Editor:
    if explicit:
        exe = explicit if explicit.is_file() else _exe_in(explicit)
        if not exe:
            raise HarnessError(f"Unity executable not found at {explicit}")
        match = re.search(r"\d+\.\d+\.\d+[abcfpx]\d+", str(exe))
        return Editor(match.group(0) if match else (version or "custom"), exe)
    installed = editors()
    if version:
        exact = [e for e in installed if e.version == version]
        if exact:
            return exact[0]
        family = [e for e in installed if e.version.split(".")[:2] == version.split(".")[:2]]
        if family and allow_minor_mismatch:
            warn(f"Unity {version} not installed; using {family[-1].version} from the same {'.'.join(version.split('.')[:2])} line")
            return family[-1]
        hint = f"install it with:  python u2w.py unity install {version}"
        if family:
            hint += f"   (or pass --allow-version-mismatch to use {family[-1].version})"
        raise HarnessError(f"Unity {version} is not installed. The project must open in the game's own version. {hint}")
    if not installed:
        raise HarnessError("no Unity Editor found; install one with 'python u2w.py unity install <version>'")
    return installed[-1]


def _hub_from_registry() -> list[str]:
    """Unity Hub can live anywhere; its uninstall entry records the executable (DisplayIcon)."""
    if os.name != "nt":
        return []
    import winreg
    found = []
    roots = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, base in roots:
        try:
            with winreg.OpenKey(hive, base) as uninstall:
                for index in range(winreg.QueryInfoKey(uninstall)[0]):
                    try:
                        with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as entry:
                            if not str(winreg.QueryValueEx(entry, "DisplayName")[0]).startswith("Unity Hub"):
                                continue
                            for value in ("DisplayIcon", "InstallLocation"):
                                try:
                                    raw = str(winreg.QueryValueEx(entry, value)[0]).strip('"').split(",")[0]
                                except OSError:
                                    continue
                                if raw:
                                    found.append(raw if raw.lower().endswith(".exe") else str(Path(raw) / "Unity Hub.exe"))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def hub_path() -> Path:
    candidates = [os.environ.get("U2W_UNITY_HUB", "")] + _hub_from_registry()
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
        if base:
            candidates += [str(Path(base) / "Unity Hub" / "Unity Hub.exe"), str(Path(base) / "Programs" / "Unity Hub" / "Unity Hub.exe")]
    candidates += ["/Applications/Unity Hub.app/Contents/MacOS/Unity Hub", str(Path.home() / "Applications" / "Unity Hub.AppImage")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise HarnessError("Unity Hub not found; install it from https://unity.com/download or set U2W_UNITY_HUB")


def license_files() -> list[Path]:
    """Licence files the Unity licensing client reads (entitlement licence for Unity 6, .ulf for older serials)."""
    bases = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Unity",
             Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Unity",
             Path("/Library/Application Support/Unity"), Path.home() / ".local" / "share" / "unity3d" / "Unity"]
    found = []
    for base in bases:
        for name in ("licenses/UnityEntitlementLicense.xml", "Unity_lic.ulf"):
            if (base / name).is_file():
                found.append(base / name)
    return found


def changeset(version: str) -> str:
    url = f"{RELEASE_API}?{urllib.parse.urlencode({'version': version, 'limit': 1})}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "unity2web-harness"}), timeout=30) as r:
        data = json.load(r)
    for release in data.get("results", []):
        if release.get("version") == version and release.get("shortRevision"):
            return release["shortRevision"]
    raise HarnessError(f"Unity {version} not found in Unity's release archive")


def install(version: str) -> None:
    hub = hub_path()
    installed = {e.version: e for e in editors()}
    if version in installed:
        if installed[version].has_webgl:
            ok(f"Unity {version} with WebGL support is already installed")
            return
        step(f"adding WebGL Build Support to Unity {version}")
        run([hub, "--", "--headless", "install-modules", "--version", version, "--module", "webgl"])
        return
    rev = changeset(version)
    step(f"installing Unity {version} ({rev}) + WebGL Build Support via Unity Hub")
    run([hub, "--", "--headless", "install", "--version", version, "--changeset", rev, "--module", "webgl"])


_INTERESTING = re.compile(r"\[u2w\]|error|exception|failed|Build Finished|Building|Compiling|il2cpp|emcc|license", re.I)


def batch(editor: Editor, project: Path, log: Path, extra: list[str], *, build_target: str | None = "WebGL") -> int:
    """Runs the editor in batch mode, mirroring the useful part of Editor.log to the console."""
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.unlink()
    args = [str(editor.exe), "-batchmode", "-quit", "-logFile", str(log)]
    if "-createProject" not in extra:
        args += ["-projectPath", str(project)]
    if build_target:
        args += ["-buildTarget", build_target]
    args += extra
    info("$ " + " ".join(f'"{a}"' if " " in a else a for a in args))
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    position = 0
    last_report = time.time()
    while True:
        finished = proc.poll() is not None
        if log.exists():
            with open(log, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(position)
                chunk = fh.read()
                position = fh.tell()
            for line in chunk.splitlines():
                if _INTERESTING.search(line) and "warning CS" not in line:
                    print("      " + line[:240], flush=True)
        if finished:
            break
        if time.time() - last_report > 120:
            info(f"... still running ({log.stat().st_size >> 20 if log.exists() else 0} MB of log)")
            last_report = time.time()
        time.sleep(2)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    if "No valid Unity Editor license" in text or "LicensingClient has failed" in text:
        warn("Unity licence problem: open Unity Hub once and activate a (free) Personal licence.")
    return proc.returncode


def build_webgl(editor: Editor, project: Path, output: Path, log: Path, *, development: bool = False,
                max_memory: int = 4096, legacy_memory: int = 1024, exceptions: str = "FullWithoutStacktrace",
                emscripten_args: str | None = None) -> None:
    if not editor.has_webgl:
        raise HarnessError(f"Unity {editor.version} has no WebGL Build Support; run: python u2w.py unity install {editor.version}")
    extra = ["-executeMethod", "Unity2Web.Editor.U2WBuilder.Build", "-u2wOut", str(output),
             "-u2wMaxMemory", str(max_memory), "-u2wLegacyMemory", str(legacy_memory), "-u2wExceptions", exceptions]
    if development:
        extra.append("-u2wDevelopment")
    if emscripten_args:
        extra += ["-u2wEmscriptenArgs", emscripten_args]
    step(f"Unity {editor.version}: WebGL build (first open imports every asset - large games take a long time)")
    code = batch(editor, project, log, extra)
    summary = output / "u2w-build.json"
    result = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else {}
    if code != 0 or not result.get("success"):
        raise HarnessError(f"WebGL build failed (exit {code}): {result.get('detail', 'no summary written')}; full log: {log}")
    ok(f"WebGL build ready in {output} ({result.get('seconds', '?')} s)")
