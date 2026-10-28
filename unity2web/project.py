"""Prepares a recovered Unity project for the browser without touching the game's logic.

1. Patches the game's compiled assemblies at IL level (u2w-patcher), always from pristine backups.
2. Injects Unity2Web: shims DLL, bootstrap, jslib, batch builder, WebGL templates and link.xml.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import tools
from .common import UNITY_FILES, HarnessError, Workspace, info, load_jsonc, ok, run, step, warn

INJECT_ROOT = "Assets/Unity2Web"


def inject(project: Path) -> None:
    step("injecting Unity2Web runtime, builder and templates")
    target = project / INJECT_ROOT
    shutil.copytree(UNITY_FILES / "Unity2Web", target, dirs_exist_ok=True)
    shims_dir = target / "Shims"
    shims_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tools.shims(), shims_dir / "Unity2Web.Shims.dll")
    templates = project / "Assets" / "WebGLTemplates"
    shutil.copytree(UNITY_FILES / "WebGLTemplates", templates, dirs_exist_ok=True)

    if not _asmdef_supported(project):
        # Unity < 2017.3 ignores .asmdef files; loose scripts compile into Assembly-CSharp(-Editor) anyway.
        for asmdef in target.rglob("*.asmdef"):
            asmdef.unlink()
    ok(f"{target}")


def _project_version(project: Path) -> str | None:
    version_file = project / "ProjectSettings" / "ProjectVersion.txt"
    if version_file.exists():
        match = re.search(r"m_EditorVersion:\s*(\S+)", version_file.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1)
    return None


def _asmdef_supported(project: Path) -> bool:
    version = _project_version(project)
    if not version:
        return True
    major, minor = (int(x) for x in version.split(".")[:2])
    return (major, minor) >= (2017, 3)


def game_assemblies(project: Path) -> list[Path]:
    """Managed DLLs exported from the game (excluding Unity2Web's own and editor-only ones)."""
    found = []
    for dll in (project / "Assets").rglob("*.dll"):
        rel = dll.relative_to(project / "Assets").parts
        if rel[0] in ("Unity2Web", "WebGLTemplates") or "Editor" in rel:
            continue
        found.append(dll)
    return sorted(found)


def write_link_xml(project: Path, assemblies: list[str]) -> None:
    """Preserves every game assembly from managed code stripping (reflection-heavy code survives)."""
    lines = ["<linker>"]
    lines += [f'  <assembly fullname="{name}" preserve="all"/>' for name in sorted(set(assemblies))]
    lines.append("</linker>")
    (project / INJECT_ROOT / "link.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def patch(ws: Workspace, *, rules: Path | None = None, internal: list[str] | None = None,
          disable: list[str] | None = None, stub_returns: dict[str, int] | None = None,
          resolve: list[Path] | None = None) -> dict:
    project = ws.project_dir()
    assets = project / "Assets"
    if not assets.is_dir():
        raise HarnessError(f"not a Unity project: {project}")

    inject(project)

    rules_data = load_jsonc(rules) if rules else {}
    if internal:
        rules_data.setdefault("relinkInternal", []).extend(internal)
    if disable:
        rules_data.setdefault("disable", []).extend(disable)
    if stub_returns:
        rules_data.setdefault("stubReturns", {}).update(stub_returns)
    skip = rules_data.get("skipThreadBodies") or []
    resources = project / INJECT_ROOT / "Runtime" / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "u2w-skip-threads.txt").write_text("\n".join(skip) + "\n", encoding="utf-8")
    effective = ws.reports / "rules.effective.json"
    effective.write_text(json.dumps(rules_data, indent=2), encoding="utf-8")

    patcher = tools.patcher()
    step("scanning game assemblies (originals)")
    resolve_args: list[str] = []
    for folder in resolve or []:
        resolve_args += ["--resolve", str(folder)]
    # Re-runs scan the pristine backups, so the report always describes the unpatched game.
    scan_root = ws.backup_dir if ws.backup_dir.is_dir() and any(ws.backup_dir.rglob("*.dll")) else assets
    run(["dotnet", patcher, "scan", scan_root, "--json", ws.reports / "scan.json", "--rules", effective, *resolve_args],
        log=ws.logs / "patcher.log")

    step("patching game assemblies (IL level, originals kept in original-assemblies/)")
    run(["dotnet", patcher, "patch", assets, "--backup", ws.backup_dir, "--rules", effective,
         "--report", ws.reports / "patch.json", *resolve_args], log=ws.logs / "patcher.log")

    report = json.loads((ws.reports / "patch.json").read_text(encoding="utf-8"))
    names = [Path(r.get("Output") or r["Assembly"]).stem for r in report]
    write_link_xml(project, names)
    renames = {r["RenamedFrom"]: r["RenamedTo"] for r in report if r.get("RenamedTo")}
    if renames:
        rewrite_assembly_names(project, renames)
    ok(f"{sum(1 for r in report if r.get('Redirected') or r.get('StubbedNative') or r.get('RelinkedNative') or r.get('UrlFilters'))} "
       f"of {len(report)} assemblies changed; link.xml preserves {len(names)}")
    moved = quarantine_native_plugins(ws, project)
    return {"scan": str(ws.reports / "scan.json"), "patch": str(ws.reports / "patch.json"), "nativeMoved": moved}


YAML_SUFFIXES = {".unity", ".prefab", ".asset", ".playable", ".controller", ".overrideController", ".signal"}


def rewrite_assembly_names(project: Path, renames: dict[str, str]) -> int:
    """Serialized data that names an assembly (UnityEvent targets, [SerializeReference] types, editor class ids)
    must follow the rename, or Unity silently drops those calls/objects."""
    alternation = b"|".join(re.escape(old.encode()) for old in sorted(renames, key=len, reverse=True))
    patterns = [
        re.compile(rb"(AssemblyTypeName: [^,\r\n]+, )(" + alternation + rb")(?=\r?\n)"),
        re.compile(rb"(\basm: )(" + alternation + rb")(?=[,}\s])"),
        re.compile(rb"(m_EditorClassIdentifier: )(" + alternation + rb")(?=::)"),
    ]
    mapping = {old.encode(): new.encode() for old, new in renames.items()}
    changed_files = 0
    for path in (project / "Assets").rglob("*"):
        if path.suffix not in YAML_SUFFIXES or not path.is_file():
            continue
        data = path.read_bytes()
        if not data.startswith(b"%YAML") or not any(old in data for old in mapping):
            continue
        new_data = data
        for pattern in patterns:
            new_data = pattern.sub(lambda m: m.group(1) + mapping[m.group(2)], new_data)
        if new_data != data:
            path.write_bytes(new_data)
            changed_files += 1
    ok(f"assembly renames {', '.join(f'{a}->{b}' for a, b in renames.items())}: {changed_files} serialized files updated")
    return changed_files


def quarantine_native_plugins(ws: Workspace, project: Path) -> list[str]:
    """Moves desktop native binaries out of Assets: they cannot run in a browser (their imports are stubbed)
    and Unity would otherwise try to treat them as WebGL plugins. Kept under the workspace for reference."""
    moved = []
    destination = ws.path / "native-plugins"
    for native in sorted((project / "Assets").rglob("*")):
        if native.suffix.lower() not in (".dll", ".so", ".dylib", ".bundle") or not native.is_file():
            continue
        if "Unity2Web" in native.parts or (native.suffix.lower() == ".dll" and _is_managed(native)):
            continue
        rel = native.relative_to(project)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(native), target)
        meta = native.with_name(native.name + ".meta")
        if meta.exists():
            shutil.move(str(meta), target.with_name(target.name + ".meta"))
        moved.append(str(rel))
    for path in moved:
        info(f"native plugin moved out of the project: {path}")
    return moved


def _is_managed(path: Path) -> bool:
    """PE file with a CLI header (data directory 14) is a .NET assembly."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
        if head[:2] != b"MZ":
            return False
        pe = int.from_bytes(head[0x3C:0x40], "little")
        if head[pe:pe + 4] != b"PE\0\0":
            return False
        optional = pe + 24
        magic = int.from_bytes(head[optional:optional + 2], "little")
        data_dirs = optional + (96 if magic == 0x10B else 112)
        cli = data_dirs + 14 * 8
        return int.from_bytes(head[cli:cli + 4], "little") != 0
    except OSError:
        return False


def _normalise_ilverify(output: str) -> set[str]:
    """ILVerify findings without file paths and IL offsets, so original and patched runs compare."""
    findings = set()
    for line in output.splitlines():
        if line.startswith("[IL]") or line.startswith("[MD]"):
            line = re.sub(r"\[[^\[\]]*?\.dll : ", "[", line)
            line = re.sub(r"offset 0x[0-9A-Fa-f]+", "", line)
            findings.add(line.strip())
    return findings


def verify(ws: Workspace, resolve: list[Path]) -> bool:
    """Runs ILVerify on every patched assembly and on its original; fails only on errors the patch introduced."""
    exe = tools.ilverify()
    assets = ws.project_dir() / "Assets"
    references = [str(tools.shims().parent / "*.dll")] + [str(d / "*.dll") for d in resolve if d.is_dir()]
    clean = True
    report = json.loads((ws.reports / "patch.json").read_text(encoding="utf-8")) if (ws.reports / "patch.json").exists() else []
    outputs = {r["Assembly"]: r.get("Output") or r["Assembly"] for r in report}
    renamed_back = {r["RenamedTo"]: r["RenamedFrom"] for r in report if r.get("RenamedTo")}
    for original in sorted(ws.backup_dir.rglob("*.dll")):
        relative = str(original.relative_to(ws.backup_dir))
        patched = assets / outputs.get(relative, relative)
        if not patched.exists():
            continue
        results = []
        for target in (original, patched):
            # Siblings first: renamed references (Game-CSharp-firstpass) only exist next to the patched file.
            cmd = [str(exe), str(target), "--system-module", "mscorlib", "-r", str(target.parent / "*.dll")]
            for ref in references:
                cmd += ["-r", ref]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            output = proc.stdout + proc.stderr
            if target is patched:
                for new, old in renamed_back.items():
                    output = output.replace(f"[{new}]", f"[{old}]")
            results.append(_normalise_ilverify(output))
        introduced = results[1] - results[0]
        name = original.relative_to(ws.backup_dir)
        if introduced:
            clean = False
            warn(f"{name}: {len(introduced)} IL errors introduced by the patch")
            for line in sorted(introduced)[:20]:
                info(line)
        else:
            ok(f"{name}: valid IL (pre-existing findings in the original: {len(results[0])})")
    return clean
