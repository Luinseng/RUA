"""Downloads the pinned third-party tools and builds the harness's own .NET components."""

from __future__ import annotations

import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from .common import DOTNET_DIR, TOOLS_DIR, HarnessError, info, manifest, ok, run, step, warn

USER_AGENT = "unity2web-harness"


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    info(f"download {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and sys.stdout.isatty():
                print(f"\r      {done * 100 // total:3d}%  {done >> 20} / {total >> 20} MB", end="", flush=True)
        if sys.stdout.isatty():
            print()
    partial.replace(dest)
    return dest


def _github_asset(name: str) -> tuple[Path, Path]:
    """Returns (install_dir, downloaded_file) for a GitHub-release tool from tools.json, fetching it if needed."""
    spec = manifest()[name]
    install = TOOLS_DIR / name / spec["tag"]
    downloaded = install / "_download" / spec["asset"]
    if not downloaded.exists():
        url = f"https://github.com/{spec['repo']}/releases/download/{spec['tag']}/{spec['asset']}"
        _download(url, downloaded)
    return install, downloaded


def _extract_zip(archive: Path, target: Path) -> None:
    """Extracts into its own folder, refusing entries that escape it."""
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        root = target.resolve()
        for member in zf.infolist():
            destination = (target / member.filename).resolve()
            if root not in destination.parents and destination != root:
                raise HarnessError(f"refusing zip entry outside target: {member.filename}")
        zf.extractall(target)


def assetripper() -> Path:
    spec = manifest()["assetripper"]
    install, archive = _github_asset("assetripper")
    app = install / "app"
    exe = next(app.rglob(spec["exe"]), None) if app.exists() else None
    if exe is None:
        _extract_zip(archive, app)
        exe = next(app.rglob(spec["exe"]), None) or next(app.rglob("AssetRipper*.exe"), None)
    if exe is None:
        raise HarnessError(f"AssetRipper executable not found in {app}")
    return exe


def cpp2il() -> Path:
    install, exe = _github_asset("cpp2il")
    return exe


def il2cppdumper() -> Path:
    spec = manifest()["il2cppdumper"]
    install, archive = _github_asset("il2cppdumper")
    app = install / "app"
    exe = next(app.rglob(spec["exe"]), None) if app.exists() else None
    if exe is None:
        _extract_zip(archive, app)
        exe = next(app.rglob(spec["exe"]), None)
    if exe is None:
        raise HarnessError(f"Il2CppDumper.exe not found in {app}")
    return exe


def ilspycmd() -> Path:
    spec = manifest()["ilspycmd"]
    target = TOOLS_DIR / "ilspycmd" / spec["version"]
    exe = target / ("ilspycmd.exe" if os.name == "nt" else "ilspycmd")
    if not exe.exists():
        run(["dotnet", "tool", "install", spec["nuget"], "--version", spec["version"], "--tool-path", target])
    return exe


def ilverify() -> Path:
    spec = manifest()["ilverify"]
    target = TOOLS_DIR / "ilverify" / spec["version"]
    exe = target / ("ilverify.exe" if os.name == "nt" else "ilverify")
    if not exe.exists():
        run(["dotnet", "tool", "install", spec["nuget"], "--version", spec["version"], "--tool-path", target])
    return exe


def _dotnet_build(project: str, out_name: str) -> Path:
    out = TOOLS_DIR / out_name
    run(["dotnet", "build", DOTNET_DIR / project, "-c", "Release", "-o", out, "-nologo", "-v", "q"])
    return out


def patcher(rebuild: bool = False) -> Path:
    dll = TOOLS_DIR / "patcher" / "u2w-patcher.dll"
    if rebuild or not dll.exists() or _stale(dll, DOTNET_DIR / "Unity2Web.Patcher"):
        _dotnet_build("Unity2Web.Patcher", "patcher")
    return dll


def shims(rebuild: bool = False) -> Path:
    dll = TOOLS_DIR / "shims" / "Unity2Web.Shims.dll"
    if rebuild or not dll.exists() or _stale(dll, DOTNET_DIR / "Unity2Web.Shims"):
        _dotnet_build("Unity2Web.Shims", "shims")
    return dll


def _stale(output: Path, source_dir: Path) -> bool:
    built = output.stat().st_mtime
    return any(p.stat().st_mtime > built for p in source_dir.rglob("*") if p.suffix in (".cs", ".csproj") and "obj" not in p.parts)


INSTALLERS = {
    "assetripper": assetripper,
    "cpp2il": cpp2il,
    "il2cppdumper": il2cppdumper,
    "ilspycmd": ilspycmd,
    "ilverify": ilverify,
    "patcher": patcher,
    "shims": shims,
}


def install(names: list[str]) -> dict[str, Path]:
    if not shutil.which("dotnet"):
        raise HarnessError("the .NET SDK (dotnet) is required: https://dotnet.microsoft.com/download")
    results = {}
    for name in names or list(INSTALLERS):
        if name not in INSTALLERS:
            raise HarnessError(f"unknown tool '{name}' (known: {', '.join(INSTALLERS)})")
        step(f"tool: {name}")
        try:
            results[name] = INSTALLERS[name]()
            ok(str(results[name]))
        except HarnessError:
            raise
        except Exception as exc:  # network errors, broken archives
            warn(f"{name}: {exc}")
    return results
