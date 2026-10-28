"""Fingerprints a compiled Unity game: engine version, platform, scripting backend, middleware and web blockers."""

from __future__ import annotations

import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .common import HarnessError, dir_size, human_size

VERSION_RE = re.compile(rb"(?<![0-9.])((?:[345]|20[1-9][0-9]|6[0-9]{3})\.[0-9]{1,2}\.[0-9]{1,3}[abcfpx][0-9]{1,3})(?![0-9])")

# Files whose header carries the Unity version string, most reliable first.
VERSION_SOURCES = ("globalgamemanagers", "mainData", "data.unity3d", "level0", "sharedassets0.assets",
                   "unity default resources", "resources.assets")

# (pattern on lower-case file name, middleware, web status, advice)
MIDDLEWARE = [
    (r"^steam_api(64)?\.dll$|^libsteam_api\.(so|dylib)$|steamworks\.net|^facepunch\.steamworks", "Steamworks", "stub",
     "No Steam in the browser: native calls are stubbed (achievements, cloud saves, overlay vanish)."),
    (r"^(fmod|fmodstudio|fmodl|fmodstudiol)\.dll$|^libfmod", "FMOD", "relink",
     "Add the FMOD WebGL static libraries of the SAME FMOD version and relink with --internal fmod --internal fmodstudio."),
    (r"aksoundengine|akunitysoundengine|^ak\.wwise", "Wwise", "blocker",
     "Wwise needs its Web platform build (2023.1+) matching the game's banks, or a Web Audio re-implementation."),
    (r"discord_game_sdk|discord-rpc|discordrpc", "Discord SDK", "stub", "Rich presence is stubbed."),
    (r"xinputinterface|xinputdotnetpure", "XInput", "stub",
     "XInput is stubbed; gamepads reach Unity through the browser Gamepad API (mapping may differ)."),
    (r"^rewired", "Rewired", "check", "Rewired supports WebGL, but the game ships the desktop build of Rewired_Core; swap in the WebGL one if input fails."),
    (r"^galaxy(64)?\.dll$|galaxycsharp", "GOG Galaxy", "stub", "Stubbed."),
    (r"^eossdk|epiconlineservices", "Epic Online Services", "stub", "Stubbed; online features off."),
    (r"photon", "Photon", "check", "Photon can run over WebSockets in WebGL; needs the WebGL transport and a reachable server."),
    (r"^(mirror|telepathy|kcp2k|kcp_transport)", "Mirror/Telepathy", "blocker", "Raw sockets: needs a WebSocket transport and a relay."),
    (r"bink", "Bink video", "blocker", "Bink has no WebGL decoder: convert videos to WebM/MP4 and use VideoPlayer URLs."),
    (r"^cri|criware", "CRIWARE", "blocker", "CRI ADX/Sofdec need CRI's WebGL SDK."),
    (r"openvr|ovrplugin|^oculus|openxr", "VR runtime", "blocker", "VR runtimes do not exist in the browser (WebXR is a different API)."),
    (r"sqlite", "SQLite", "blocker", "Native SQLite: needs a WebAssembly SQLite build or a managed port."),
    (r"^(xlua|tolua|slua|ulua)", "Native Lua", "blocker", "Native Lua VM: needs an Emscripten build of the Lua library (relink as __Internal)."),
    (r"moonsharp", "MoonSharp", "ok", "Managed Lua: works."),
    (r"nvngx|dlss|ffx|fidelityfx|xess", "GPU upscaler", "stub", "Vendor upscalers do not exist in WebGL."),
    (r"^bakery|^lightmapper", "Bakery runtime", "check", "Runtime lightmapping helpers may need replacing."),
    (r"^sentis|onnx|tensorflow|barracuda", "ML runtime", "check", "Inference backends need WebGL/WebGPU support."),
]

ENGINE_NATIVE = {
    "unityplayer.dll", "gameassembly.dll", "unitycrashhandler64.exe", "unitycrashhandler32.exe",
    "libunity.so", "libmain.so", "libil2cpp.so", "libmonobdwgc-2.0.so", "libmono.so", "libmonosgen-2.0.so",
    "mono-2.0-bdwgc.dll", "mono.dll", "mono-2.0-sgen.dll", "winpixeventruntime.dll", "d3d12core.dll",
    "lib_burst_generated.dll", "libburst_generated.so",
}


@dataclass
class Detection:
    game: str
    root: str
    platform: str = "unknown"
    unity_version: str | None = None
    backend: str = "unknown"            # mono | il2cpp | unknown
    dotnet_profile: str | None = None   # legacy-3.5 | 4.x
    data_dir: str | None = None
    managed_dir: str | None = None
    game_assemblies: list[str] = field(default_factory=list)
    package_assemblies: list[str] = field(default_factory=list)
    native_plugins: list[str] = field(default_factory=list)
    middleware: list[dict] = field(default_factory=list)
    render_pipeline: str = "builtin"
    input_system: str = "legacy"
    streaming_assets_bytes: int = 0
    total_bytes: int = 0
    verdict: str = "unknown"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def find_unity_version(data_dir: Path) -> str | None:
    for name in VERSION_SOURCES:
        candidate = data_dir / name
        if candidate.is_file():
            with open(candidate, "rb") as fh:
                match = VERSION_RE.search(fh.read(1 << 16))
            if match:
                return match.group(1).decode()
    return None


def _scan_middleware(names: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    for name in names:
        lower = Path(name).name.lower()
        for pattern, label, status, advice in MIDDLEWARE:
            if re.search(pattern, lower):
                entry = found.setdefault(label, {"name": label, "status": status, "advice": advice, "files": []})
                entry["files"].append(name)
                break
    return list(found.values())


def _classify_assemblies(names: list[str]) -> tuple[list[str], list[str]]:
    game, packages = [], []
    for name in sorted(names):
        stem = Path(name).stem
        if re.match(r"^(mscorlib|netstandard|System(\..+)?|Mono\..+|Microsoft\..+|UnityEngine(\..+)?|UnityEditor(\..+)?|Boo\..+|UnityScript.*)$", stem):
            continue
        (packages if stem.startswith("Unity.") else game).append(Path(name).name)
    return game, packages


def _pipeline_and_input(names: list[str], blob: bytes = b"") -> tuple[str, str]:
    # Match assembly image names (".dll"): IL2CPP metadata also lists InternalsVisibleTo targets, so URP
    # games mention "Unity.RenderPipelines.HighDefinition.Runtime" without shipping it.
    joined = " ".join(n.lower() for n in names) + " " + blob.decode("latin-1").lower()
    pipeline = ("hdrp" if "renderpipelines.highdefinition.runtime.dll" in joined
                else "urp" if "renderpipelines.universal.runtime.dll" in joined else "builtin")
    new_input = "unity.inputsystem.dll" in joined
    return pipeline, ("new" if new_input else "legacy")


def _detect_desktop(root: Path, det: Detection) -> None:
    data_dirs = [d for d in root.glob("*_Data") if d.is_dir()]
    mac_data = list(root.glob("*.app/Contents/Resources/Data")) + ([root / "Contents/Resources/Data"] if (root / "Contents/Resources/Data").is_dir() else [])
    if data_dirs:
        data = data_dirs[0]
        det.platform = "windows" if list(root.glob("*.exe")) else "linux"
    elif mac_data:
        data = mac_data[0]
        det.platform = "mac"
    elif (root / "globalgamemanagers").exists() or (root / "mainData").exists():
        data = root
        det.platform = "data-folder"
    else:
        raise HarnessError(f"{root} does not look like a Unity build (no *_Data folder, no globalgamemanagers)")

    det.data_dir = str(data)
    det.unity_version = find_unity_version(data)

    metadata = data / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    il2cpp_binaries = [p for p in ("GameAssembly.dll", "GameAssembly.so", "GameAssembly.dylib") if (root / p).exists()]
    managed = data / "Managed"
    if metadata.exists() or il2cpp_binaries:
        det.backend = "il2cpp"
    elif (managed / "Assembly-CSharp.dll").exists() or managed.is_dir():
        det.backend = "mono"
        det.managed_dir = str(managed)

    if (root / "MonoBleedingEdge").is_dir():
        det.dotnet_profile = "4.x"
    elif (root / "Mono").is_dir():
        det.dotnet_profile = "legacy-3.5"

    if managed.is_dir():
        dlls = [p.name for p in managed.glob("*.dll")]
        det.game_assemblies, det.package_assemblies = _classify_assemblies(dlls)
        det.render_pipeline, det.input_system = _pipeline_and_input(dlls)
    elif metadata.exists():
        with open(metadata, "rb") as fh:
            blob = fh.read()
        det.render_pipeline, det.input_system = _pipeline_and_input([], blob)

    natives: list[str] = []
    plugin_dir = data / "Plugins"
    if plugin_dir.is_dir():
        natives += [str(p.relative_to(root)) for p in plugin_dir.rglob("*") if p.suffix.lower() in (".dll", ".so", ".dylib", ".bundle")]
    natives += [p.name for p in root.glob("*.dll") if p.name.lower() not in ENGINE_NATIVE]
    det.native_plugins = sorted(set(natives))
    det.middleware = _scan_middleware(det.native_plugins + det.game_assemblies)

    streaming = data / "StreamingAssets"
    det.streaming_assets_bytes = dir_size(streaming) if streaming.is_dir() else 0
    det.total_bytes = dir_size(root)


def _detect_android(apk: Path, det: Detection) -> None:
    det.platform = "android"
    with zipfile.ZipFile(apk) as zf:
        names = zf.namelist()
        for source in VERSION_SOURCES:
            member = f"assets/bin/Data/{source}"
            if member in names:
                match = VERSION_RE.search(zf.open(member).read(1 << 16))
                if match:
                    det.unity_version = match.group(1).decode()
                    break
        if any(n.endswith("libil2cpp.so") for n in names) or "assets/bin/Data/Managed/Metadata/global-metadata.dat" in names:
            det.backend = "il2cpp"
        elif any(n.startswith("assets/bin/Data/Managed/") and n.endswith(".dll") for n in names):
            det.backend = "mono"
        dlls = [Path(n).name for n in names if n.startswith("assets/bin/Data/Managed/") and n.endswith(".dll")]
        det.game_assemblies, det.package_assemblies = _classify_assemblies(dlls)
        det.render_pipeline, det.input_system = _pipeline_and_input(dlls)
        libs = [n for n in names if n.startswith("lib/") and n.endswith(".so") and Path(n).name.lower() not in ENGINE_NATIVE]
        det.native_plugins = sorted(libs)
        det.middleware = _scan_middleware(det.native_plugins + det.game_assemblies)
        det.streaming_assets_bytes = sum(i.file_size for i in zf.infolist() if i.filename.startswith("assets/") and not i.filename.startswith("assets/bin/"))
    det.data_dir = f"{apk}!/assets/bin/Data"
    det.total_bytes = apk.stat().st_size


def _detect_webgl(root: Path, det: Detection) -> bool:
    build = root / "Build" if (root / "Build").is_dir() else root
    loaders = list(build.glob("*.loader.js")) + list(build.glob("UnityLoader.js"))
    datas = [p for p in build.iterdir() if re.search(r"\.data(\.gz|\.br|\.unityweb)?$", p.name)] if build.is_dir() else []
    if not loaders and not datas:
        return False
    det.platform = "webgl"
    det.backend = "il2cpp"
    det.data_dir = str(build)
    for js in list(build.glob("*.framework.js*")) + list(build.glob("*.js")):
        try:
            match = VERSION_RE.search(js.read_bytes()[:4 << 20])
        except OSError:
            continue
        if match:
            det.unity_version = match.group(1).decode()
            break
    det.total_bytes = dir_size(root)
    return True


def version_tuple(version: str | None) -> tuple[int, int]:
    if not version:
        return (0, 0)
    major, minor = version.split(".")[:2]
    return int(major), int(minor)


def _verdict(det: Detection) -> None:
    reasons = det.reasons
    if det.platform == "webgl":
        det.verdict = "already-web"
        reasons.append("This is already a WebGL build: use 'unpack-webgl' to inspect it.")
        return
    major, minor = version_tuple(det.unity_version)
    blocked = False
    if not det.unity_version:
        reasons.append("Unity version not found: pass --unity-version when exporting.")
    elif (major, minor) < (5, 6):
        blocked = True
        reasons.append(f"Unity {det.unity_version} predates usable WebGL (5.6+). Upgrading the project is an adaptation, not a port.")
    if det.render_pipeline == "hdrp":
        blocked = True
        reasons.append("HDRP has no WebGL backend. Running it in a browser means converting to URP/built-in (an adaptation).")
    for mw in det.middleware:
        if mw["status"] == "blocker":
            reasons.append(f"{mw['name']}: {mw['advice']}")
    if det.backend == "il2cpp":
        det.verdict = "blocked" if blocked else "manual"
        reasons.append("IL2CPP build: method bodies are compiled to native code and cannot be recovered as C#. "
                       "Assets, scenes and prefabs export fine; game logic must be re-implemented from the "
                       "Cpp2IL/Il2CppDumper reference (or use a Mono build of the same game, e.g. another platform or an older version).")
    elif det.backend == "mono":
        det.verdict = "blocked" if blocked else "auto"
        reasons.append("Mono build: the original compiled game code is reused as-is (DLL export) and compiled to WebAssembly by IL2CPP.")
    else:
        det.verdict = "unknown"
        reasons.append("Scripting backend not recognised.")


def detect(game: Path) -> Detection:
    game = game.resolve()
    if not game.exists():
        raise HarnessError(f"not found: {game}")
    det = Detection(game=game.name, root=str(game))
    if game.is_file() and game.suffix.lower() in (".apk", ".xapk", ".zip"):
        _detect_android(game, det)
    elif game.is_file() and game.suffix.lower() == ".exe":
        det.root = str(game.parent)
        _detect_desktop(game.parent, det)
    elif game.is_dir() and _detect_webgl(game, det):
        pass
    elif game.is_dir():
        _detect_desktop(game, det)
    else:
        raise HarnessError(f"unsupported input: {game}")
    _verdict(det)
    return det


def describe(det: Detection) -> list[str]:
    lines = [
        f"game:            {det.game}",
        f"platform:        {det.platform}",
        f"unity:           {det.unity_version or '?'}",
        f"backend:         {det.backend}" + (f" (.NET {det.dotnet_profile})" if det.dotnet_profile else ""),
        f"render pipeline: {det.render_pipeline}",
        f"input:           {det.input_system}",
        f"game assemblies: {len(det.game_assemblies)}  {', '.join(det.game_assemblies[:8])}{' …' if len(det.game_assemblies) > 8 else ''}",
        f"unity packages:  {len(det.package_assemblies)}",
        f"native plugins:  {len(det.native_plugins)}",
        f"StreamingAssets: {human_size(det.streaming_assets_bytes)}   total: {human_size(det.total_bytes)}",
    ]
    for mw in det.middleware:
        lines.append(f"middleware:      {mw['name']} [{mw['status']}] - {mw['advice']}")
    lines.append(f"verdict:         {det.verdict.upper()}")
    lines += [f"  - {r}" for r in det.reasons]
    return lines
