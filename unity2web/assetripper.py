"""Drives AssetRipper headlessly through its local web API (the same calls its GUI makes).

AssetRipper 2.x (Source/AssetRipper.GUI.Web) accepts --port, --headless, --log-path; the commands
are form posts: /Settings/Update, /LoadFolder|/LoadFile (field "Path"), /Export/UnityProject (field "Path").
Load and export run synchronously inside the request, so the HTTP call returns when the work is done.
"""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .common import HarnessError, info, ok, step, warn

# Script export: DllExportWithRenaming keeps the game's compiled assemblies (the real game code) and
# renames Assembly-CSharp* so it cannot collide with Unity's own script compilation output.
DEFAULT_SETTINGS = {
    "ScriptExportMode": "DllExportWithRenaming",
    "ShaderExportMode": "Decompile",
    "ScriptContentLevel": "Level2",
    "StreamingAssetsMode": "Extract",
    "BundledAssetsExportMode": "DirectExport",
    "ImageExportFormat": "Png",
    "AudioExportFormat": "Default",
    "SpriteExportMode": "Yaml",
    "TextExportMode": "Parse",
    "ScriptLanguageVersion": "AutoSafe",
    "LightmapTextureExportFormat": "Yaml",
}

# Boolean settings are "on" when the key is present in the form, "off" when absent.
DEFAULT_FLAGS = {
    "EnablePrefabOutlining": False,
    "EnableStaticMeshSeparation": False,  # keep meshes exactly as the game references them
    "EnableAssetDeduplication": False,
    "RemoveNullableAttributes": False,
    "PublicizeAssemblies": False,
    "ScriptTypesFullyQualified": False,
    "ExportUnreadableAssets": False,
    "PreferOriginalTextureExtension": True,
    "IgnoreStreamingAssets": False,
    "SaveSettingsToDisk": False,
}

SCRIPT_MODES = {
    "dll": "DllExportWithRenaming",
    "dll-raw": "DllExportWithoutRenaming",
    "hybrid": "Hybrid",
    "decompiled": "Decompiled",
}

SHADER_MODES = {"decompile": "Decompile", "dummy": "Dummy", "yaml": "Yaml"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AssetRipper:
    def __init__(self, exe: Path, log_path: Path):
        self.exe = exe
        self.log_path = log_path
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        # ProxyHandler({}): HTTP(S)_PROXY must never capture calls to 127.0.0.1.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def __enter__(self) -> "AssetRipper":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        console = open(self.log_path.with_suffix(".console.log"), "w", encoding="utf-8")
        args = [str(self.exe), "--port", str(self.port), "--headless", "--log-path", str(self.log_path)]
        info("$ " + " ".join(args))
        self.process = subprocess.Popen(args, cwd=self.exe.parent, stdout=console, stderr=subprocess.STDOUT)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise HarnessError(f"AssetRipper exited early (code {self.process.returncode}); see {console.name}")
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.base + "/", timeout=2):
                    ok(f"AssetRipper up on {self.base}")
                    return self
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.5)
        raise HarnessError("AssetRipper did not start within 90 s")

    def __exit__(self, *exc) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _post(self, route: str, fields: list[tuple[str, str]], timeout: float | None = None) -> int:
        body = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(self.base + route, data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                return exc.code  # every command answers with a redirect to the GUI page
            raise HarnessError(f"AssetRipper {route} failed: HTTP {exc.code} {exc.read()[:500]!r}")

    def configure(self, settings: dict[str, str], flags: dict[str, bool]) -> None:
        fields = list(settings.items()) + [(k, "on") for k, v in flags.items() if v]
        self._post("/Settings/Update", fields, timeout=60)
        ok("settings applied: " + ", ".join(f"{k}={v}" for k, v in settings.items() if k in ("ScriptExportMode", "ShaderExportMode")))

    def load(self, paths: list[Path]) -> None:
        files = [p for p in paths if p.is_file()]
        route = "/LoadFile" if files and len(files) == len(paths) else "/LoadFolder"
        started = time.time()
        self._post(route, [("Path", str(p)) for p in paths], timeout=None)
        ok(f"loaded in {time.time() - started:.0f} s")

    def export_project(self, output: Path) -> None:
        started = time.time()
        self._post("/Export/UnityProject", [("Path", str(output))], timeout=None)
        ok(f"exported in {time.time() - started:.0f} s")


def export(exe: Path, game_paths: list[Path], output: Path, log_dir: Path, *, scripts: str = "dll",
           shaders: str = "decompile", unity_version: str | None = None) -> Path:
    """Exports the game as a Unity project and returns the project folder."""
    if output.exists() and any(output.iterdir()):
        raise HarnessError(f"export folder is not empty: {output} (use --force to replace it)")
    output.mkdir(parents=True, exist_ok=True)

    settings = dict(DEFAULT_SETTINGS)
    settings["ScriptExportMode"] = SCRIPT_MODES[scripts]
    settings["ShaderExportMode"] = SHADER_MODES[shaders]
    if unity_version:
        settings["DefaultVersion"] = unity_version

    step(f"AssetRipper: {', '.join(str(p) for p in game_paths)}")
    with AssetRipper(exe, log_dir / "assetripper.log") as ripper:
        ripper.configure(settings, DEFAULT_FLAGS)
        ripper.load(game_paths)
        ripper.export_project(output)

    for candidate in [output / "ExportedProject", output]:
        if (candidate / "Assets").is_dir() and (candidate / "ProjectSettings").is_dir():
            return candidate
    for assets in output.rglob("Assets"):
        if (assets.parent / "ProjectSettings").is_dir():
            return assets.parent
    warn(f"see {log_dir / 'assetripper.log'}")
    raise HarnessError(f"AssetRipper finished but no Unity project was found under {output}")
