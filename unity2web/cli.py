"""Command line for the unity2web harness: compiled Unity game -> native Unity WebGL build."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import webbrowser
from pathlib import Path

from . import assetripper, detect, il2cpp, project, serve, tools, unity, webdata
from .common import (WORK_DIR, HarnessError, Workspace, error, human_size, info, ok, run, save_json, step, warn)


def _workspace(args, game: Path | None = None) -> Workspace:
    if getattr(args, "work", None):
        return Workspace(Path(args.work)).ensure()
    if game is None:
        raise HarnessError("pass --work <folder> (the workspace created by 'export' or 'port')")
    return Workspace.for_game(game).ensure()


# ------------------------------------------------------------------ commands

def cmd_doctor(args) -> int:
    step("environment")
    checks = {
        "python": sys.version.split()[0],
        "dotnet": shutil.which("dotnet") or "MISSING (https://dotnet.microsoft.com/download)",
    }
    for key, value in checks.items():
        (ok if "MISSING" not in value else error)(f"{key}: {value}")
    editors = unity.editors()
    if not editors:
        warn("no Unity Editor found (python u2w.py unity install <version>)")
    for editor in editors:
        (ok if editor.has_webgl else warn)(f"Unity {editor.version}  WebGL={'yes' if editor.has_webgl else 'NO'}  {editor.exe}")
    try:
        ok(f"Unity Hub: {unity.hub_path()}")
    except HarnessError as exc:
        warn(str(exc))
    licences = unity.license_files()
    if licences:
        ok(f"Unity licence: {licences[0]}")
    else:
        warn("no Unity licence file: open Unity Hub, sign in, Preferences > Licenses > Add > Get a free personal licence")
    from .common import TOOLS_DIR, manifest
    for name, spec in manifest().items():
        if name.startswith("_"):
            continue
        version = spec.get("tag") or spec.get("version")
        present = (TOOLS_DIR / name / version).exists()
        (ok if present else info)(f"{name} {version}: {'installed' if present else 'not downloaded yet (python u2w.py tools)'}")
    return 0


def cmd_tools(args) -> int:
    tools.install(args.names)
    return 0


def cmd_detect(args) -> int:
    det = detect.detect(Path(args.game))
    if args.json:
        print(json.dumps(det.to_dict(), indent=2))
    else:
        for line in detect.describe(det):
            print(line)
    return 0


def cmd_export(args) -> int:
    game = Path(args.game).resolve()
    ws = _workspace(args, game)
    det = detect.detect(game)
    for line in detect.describe(det):
        info(line)
    save_json(ws.reports / "detect.json", det.to_dict())
    if det.verdict == "already-web":
        raise HarnessError("input is already a WebGL build; use 'unpack-webgl'")
    if ws.export_dir.exists() and any(ws.export_dir.iterdir()):
        if not args.force:
            raise HarnessError(f"{ws.export_dir} already exists; pass --force to export again")
        shutil.rmtree(ws.export_dir)
        if ws.backup_dir.exists():
            shutil.rmtree(ws.backup_dir)
    inputs = [Path(det.root)] if Path(det.root).is_dir() else [game]
    project_dir = assetripper.export(tools.assetripper(), inputs, ws.export_dir, ws.logs,
                                     scripts=args.scripts, shaders=args.shaders,
                                     unity_version=args.unity_version or det.unity_version)
    ws.update(game=str(game), detect=det.to_dict(), project=str(project_dir),
              unity_version=args.unity_version or det.unity_version, patched=False, build=None)
    ok(f"Unity project: {project_dir}")
    if det.backend == "il2cpp":
        warn("IL2CPP game: scripts in the export are empty stubs. Run 'il2cpp' for reference dumps.")
    return 0


def cmd_scan(args) -> int:
    patcher = tools.patcher()
    target = Path(args.target)
    if (target / "state.json").exists():
        target = Workspace(target).project_dir() / "Assets"
    cmd = ["dotnet", patcher, "scan", target]
    if args.json:
        cmd += ["--json", args.json]
    if args.all:
        cmd.append("--all")
    run(cmd)
    return 0


def cmd_patch(args) -> int:
    ws = _workspace(args)
    stub_returns = {}
    for item in args.stub_return or []:
        name, _, value = item.partition("=")
        stub_returns[name] = int(value or 0)
    resolve = [Path(p) for p in args.resolve or []]
    # AssetRipper copies every assembly the game shipped (UnityEngine, mscorlib, ...) here: ideal for resolution.
    resolve.append(ws.export_dir / "AuxiliaryFiles" / "GameAssemblies")
    version = ws.state().get("unity_version")
    try:
        editor = unity.pick_editor(version, allow_minor_mismatch=True)
        resolve += editor.managed_dirs
    except HarnessError:
        pass
    result = project.patch(ws, rules=Path(args.rules) if args.rules else None, internal=args.internal,
                           disable=args.disable, stub_returns=stub_returns, resolve=resolve)
    ws.update(patched=True, **result)
    return 0


def cmd_verify(args) -> int:
    """ILVerify: proves the patch did not introduce invalid IL before spending an hour on a Unity build."""
    ws = _workspace(args)
    resolve = [ws.export_dir / "AuxiliaryFiles" / "GameAssemblies"] + [Path(p) for p in args.resolve or []]
    step("ILVerify: original vs patched assemblies")
    if not project.verify(ws, resolve):
        raise HarnessError("the patch introduced invalid IL (see above); please report it with reports/patch.json")
    return 0


def cmd_il2cpp(args) -> int:
    game = Path(args.game).resolve()
    ws = _workspace(args, game)
    root = game.parent if game.is_file() else game
    results = il2cpp.dump(root, ws.path / "il2cpp", ws.logs, formats=args.formats)
    ws.update(il2cpp={k: str(v) for k, v in results.items()})
    return 0


def cmd_decompile(args) -> int:
    """Readable C# of the game assemblies, for studying and writing patches (not used by the build)."""
    ws = _workspace(args)
    exe = tools.ilspycmd()
    source_root = Path(ws.state().get("detect", {}).get("managed_dir") or "")
    assemblies = sorted(source_root.glob("*.dll")) if source_root.is_dir() else []
    det = ws.state().get("detect", {})
    names = set(det.get("game_assemblies", []))
    out_root = ws.path / "source"
    for dll in assemblies:
        if names and dll.name not in names:
            continue
        step(f"ILSpy: {dll.name}")
        run([exe, "-p", "-o", out_root / dll.stem, "-r", source_root, dll], log=ws.logs / "ilspy.log", check=False)
    ok(f"decompiled sources in {out_root}")
    return 0


def cmd_build(args) -> int:
    ws = _workspace(args)
    project_dir = ws.project_dir()
    state = ws.state()
    if not state.get("patched") and not args.skip_patch_check:
        raise HarnessError("project not patched yet: run 'patch' first (or pass --skip-patch-check)")
    editor = unity.pick_editor(args.unity_version or state.get("unity_version"),
                               explicit=Path(args.unity) if args.unity else None,
                               allow_minor_mismatch=args.allow_version_mismatch)
    output = Path(args.out).resolve() if args.out else ws.build_dir
    if output.exists() and any(output.iterdir()):
        # Only ever wipe a folder that holds a previous build of ours.
        if not (output / "u2w-build.json").exists():
            raise HarnessError(f"{output} is not empty and is not a previous Unity2Web build; choose an empty --out")
        shutil.rmtree(output)
    unity.build_webgl(editor, project_dir, output, ws.logs / "unity-build.log", development=args.development,
                      max_memory=args.max_memory, legacy_memory=args.legacy_memory, exceptions=args.exceptions,
                      emscripten_args=args.emscripten_args)
    streaming = project_dir / "Assets" / "StreamingAssets"
    if not args.no_pack:
        webdata.pack_streaming_assets(output, streaming, limit_mb=args.pack_limit_mb)
    if not args.no_compress:
        step("compressing for delivery")
        webdata.compress(output)
    ws.update(build=str(output), editor=editor.version)
    ok(f"done: python u2w.py serve {output}")
    return 0


def cmd_pack(args) -> int:
    webdata.pack_streaming_assets(Path(args.build), Path(args.streaming_assets))
    return 0


def cmd_compress(args) -> int:
    webdata.compress(Path(args.build), brotli_too=not args.gzip_only)
    return 0


def cmd_serve(args) -> int:
    directory = Path(args.directory)
    if (directory / "state.json").exists():
        directory = Path(Workspace(directory).state().get("build") or Workspace(directory).build_dir)
    if not (directory / "index.html").exists():
        raise HarnessError(f"no index.html in {directory}")
    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}/")
    serve.serve(directory, args.port, args.host, args.isolate)
    return 0


def cmd_unpack_webgl(args) -> int:
    source = Path(args.build)
    data_file = source if source.is_file() else webdata.find_data_file(source)
    if not args.out:
        for line in webdata.describe(data_file):
            print(line)
        return 0
    entries = webdata.unpack(data_file, Path(args.out))
    ok(f"{len(entries)} files unpacked to {args.out}")
    info("feed the unpacked folder (data.unity3d etc.) to AssetRipper: python u2w.py export <folder>")
    return 0


def cmd_unity(args) -> int:
    if args.action == "list":
        for editor in unity.editors():
            print(f"{editor.version:16} WebGL={'yes' if editor.has_webgl else 'no ':3}  {editor.exe}")
    elif args.action == "install":
        if not args.version:
            raise HarnessError("unity install needs a version, e.g. 2021.3.45f1")
        unity.install(args.version)
    elif args.action == "changeset":
        print(unity.changeset(args.version))
    return 0


def cmd_port(args) -> int:
    """detect -> export -> patch -> build -> pack -> compress (-> serve)."""
    game = Path(args.game).resolve()
    ws = _workspace(args, game)
    args.work = str(ws.path)
    det = detect.detect(game)
    step("detect")
    for line in detect.describe(det):
        info(line)
    if det.verdict == "blocked" and not args.force_blocked:
        raise HarnessError("blocked: see the reasons above (pass --force-blocked to try anyway)")
    if det.backend == "il2cpp" and not args.force_blocked:
        raise HarnessError("IL2CPP game: the code cannot be recovered automatically. Run 'export' + 'il2cpp' "
                           "and re-implement the logic, or port a Mono build of the same game.")
    tools.install(["assetripper", "patcher", "shims"])
    args.force = args.force_export
    cmd_export(args)
    cmd_patch(args)
    cmd_verify(args)
    args.out = None
    args.skip_patch_check = False
    cmd_build(args)
    if args.serve:
        args.directory = str(ws.build_dir)
        args.open = True
        args.isolate = False
        return cmd_serve(args)
    return 0


# ------------------------------------------------------------------ parser

def _add_patch_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--rules", help="rules.json (see rules.example.json)")
    p.add_argument("--internal", action="append", metavar="LIB",
                   help="re-point a native library to __Internal because you provide its WebAssembly build (e.g. fmodstudio)")
    p.add_argument("--disable", action="append", metavar="GROUP",
                   help="skip a rewrite group: threads, process, folders, paths, urls, pinvoke")
    p.add_argument("--stub-return", action="append", metavar="ENTRY=VALUE",
                   help="constant return value for a stubbed native entry point")
    p.add_argument("--resolve", action="append", metavar="DIR", help="extra folder for assembly resolution")


def _add_build_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--unity", help="path to a specific Unity.exe or its version folder")
    p.add_argument("--unity-version", help="override the detected Unity version")
    p.add_argument("--allow-version-mismatch", action="store_true", help="use another patch release of the same Unity line")
    p.add_argument("--development", action="store_true", help="development build (profiler, readable stack traces)")
    p.add_argument("--max-memory", type=int, default=4096, help="WebAssembly heap limit in MB (Unity 2021.2+, default 4096)")
    p.add_argument("--legacy-memory", type=int, default=1024, help="fixed heap in MB for Unity < 2021.2 (default 1024, max 2032)")
    p.add_argument("--exceptions", default="FullWithoutStacktrace",
                   choices=["None", "ExplicitlyThrownExceptionsOnly", "FullWithoutStacktrace", "FullWithStacktrace"])
    p.add_argument("--emscripten-args", help="extra emscripten flags (e.g. '-s STACK_SIZE=5MB')")
    p.add_argument("--no-pack", action="store_true", help="do not pack StreamingAssets into the .data file")
    p.add_argument("--pack-limit-mb", type=int, default=1024, help="skip packing above this StreamingAssets size (default 1024)")
    p.add_argument("--no-compress", action="store_true", help="skip writing .gz/.br sidecars")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="u2w", description="Unity game -> native Unity WebGL build harness")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check Python, .NET, Unity editors, Hub and tools").set_defaults(func=cmd_doctor)

    p = sub.add_parser("tools", help="download pinned tools and build the patcher/shims")
    p.add_argument("names", nargs="*", help="assetripper cpp2il il2cppdumper ilspycmd patcher shims (default: all)")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("detect", help="fingerprint a compiled Unity game")
    p.add_argument("game", help="game folder, .exe, .apk or WebGL build folder")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("export", help="AssetRipper: game -> Unity project (original compiled code kept as DLLs)")
    p.add_argument("game")
    p.add_argument("--work", help=f"workspace folder (default {WORK_DIR}/<game>)")
    p.add_argument("--scripts", default="dll", choices=list(assetripper.SCRIPT_MODES),
                   help="dll = original compiled assemblies (default, faithful); decompiled = ILSpy C# (editable, may not compile)")
    p.add_argument("--shaders", default="decompile", choices=list(assetripper.SHADER_MODES))
    p.add_argument("--unity-version", help="override the detected Unity version")
    p.add_argument("--force", action="store_true", help="replace an existing export")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("scan", help="report browser-incompatible APIs in game assemblies")
    p.add_argument("target", help="folder, .dll or workspace")
    p.add_argument("--json")
    p.add_argument("--all", action="store_true", help="include BCL/Unity assemblies")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("patch", help="IL-patch the exported game assemblies and inject the Unity2Web runtime")
    p.add_argument("--work", required=True)
    _add_patch_options(p)
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("verify", help="ILVerify the patched assemblies against the originals")
    p.add_argument("--work", required=True)
    p.add_argument("--resolve", action="append", metavar="DIR")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("il2cpp", help="reference dumps for IL2CPP games (Il2CppDumper + Cpp2IL)")
    p.add_argument("game")
    p.add_argument("--work")
    p.add_argument("--formats", nargs="*", help="Cpp2IL output formats (default: dummydll isil diffable-cs)")
    p.set_defaults(func=cmd_il2cpp)

    p = sub.add_parser("decompile", help="ILSpy: readable C# of the game assemblies (reference only)")
    p.add_argument("--work", required=True)
    p.set_defaults(func=cmd_decompile)

    p = sub.add_parser("build", help="Unity batch-mode WebGL build + pack StreamingAssets + compress")
    p.add_argument("--work", required=True)
    p.add_argument("--out", help="output folder (default <work>/build/webgl)")
    p.add_argument("--skip-patch-check", action="store_true")
    _add_build_options(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("pack", help="pack a StreamingAssets folder into a WebGL build's .data file")
    p.add_argument("build")
    p.add_argument("streaming_assets")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("compress", help="write .gz/.br sidecars for a WebGL build")
    p.add_argument("build")
    p.add_argument("--gzip-only", action="store_true")
    p.set_defaults(func=cmd_compress)

    p = sub.add_parser("serve", help="serve a WebGL build locally with correct headers")
    p.add_argument("directory", help="build folder or workspace")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--isolate", action="store_true", help="COOP/COEP headers (multithreaded builds)")
    p.add_argument("--open", action="store_true", help="open the browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("unpack-webgl", help="list or extract the .data package of an existing WebGL build")
    p.add_argument("build", help="WebGL build folder or .data file")
    p.add_argument("--out", help="extract here (omit to list)")
    p.set_defaults(func=cmd_unpack_webgl)

    p = sub.add_parser("unity", help="list/install Unity editors (via Unity Hub CLI)")
    p.add_argument("action", choices=["list", "install", "changeset"])
    p.add_argument("version", nargs="?")
    p.set_defaults(func=cmd_unity)

    p = sub.add_parser("port", help="everything: detect, export, patch, build, pack, compress [, serve]")
    p.add_argument("game")
    p.add_argument("--work")
    p.add_argument("--scripts", default="dll", choices=list(assetripper.SCRIPT_MODES))
    p.add_argument("--shaders", default="decompile", choices=list(assetripper.SHADER_MODES))
    p.add_argument("--force-export", action="store_true", help="replace an existing export")
    p.add_argument("--force-blocked", action="store_true", help="continue despite a blocked/IL2CPP verdict")
    p.add_argument("--serve", action="store_true", help="serve and open the browser when done")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    _add_patch_options(p)
    _add_build_options(p)
    p.set_defaults(func=cmd_port)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except HarnessError as exc:
        error(str(exc))
        return 1
    except KeyboardInterrupt:
        error("interrupted")
        return 130
