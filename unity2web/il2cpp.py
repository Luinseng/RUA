"""Reference dumps for IL2CPP games: type/method signatures, addresses and lifted ISIL.

IL2CPP compiles method bodies to native code, so no tool returns the original C#. These dumps are
the material for re-implementing the logic (by hand or with an assistant): names, fields, signatures,
RVAs (Il2CppDumper) and Cpp2IL's ISIL, which lifts most native bodies into a readable instruction form.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import tools
from .common import HarnessError, info, ok, run, step, warn


def _find_inputs(root: Path) -> tuple[Path, Path]:
    binaries = [root / n for n in ("GameAssembly.dll", "GameAssembly.so", "GameAssembly.dylib")]
    binaries += list(root.rglob("libil2cpp.so"))
    binary = next((b for b in binaries if b.exists()), None)
    metadata = next(iter(root.rglob("global-metadata.dat")), None)
    if binary is None or metadata is None:
        raise HarnessError("IL2CPP inputs not found (need GameAssembly.dll/libil2cpp.so and global-metadata.dat); "
                           "for an APK, unzip it first and pass the folder")
    return binary, metadata


def _configure_il2cppdumper(exe: Path) -> None:
    config = exe.parent / "config.json"
    if config.exists():
        data = json.loads(config.read_text(encoding="utf-8-sig"))
        if data.get("RequireAnyKey", True):
            data["RequireAnyKey"] = False
            config.write_text(json.dumps(data, indent=2), encoding="utf-8")


def dump(game_root: Path, output: Path, log_dir: Path, *, formats: list[str] | None = None) -> dict[str, Path]:
    binary, metadata = _find_inputs(game_root)
    info(f"binary:   {binary}")
    info(f"metadata: {metadata}")
    results: dict[str, Path] = {}

    step("Il2CppDumper: signatures, RVAs, dummy DLLs, IDA/Ghidra scripts")
    dumper = tools.il2cppdumper()
    _configure_il2cppdumper(dumper)
    target = output / "il2cppdumper"
    target.mkdir(parents=True, exist_ok=True)
    result = run([dumper, binary, metadata, target], cwd=dumper.parent, log=log_dir / "il2cppdumper.log",
                 check=False, input_text="\n")
    if result.returncode == 0 and (target / "dump.cs").exists():
        ok(f"{target / 'dump.cs'}")
        results["il2cppdumper"] = target
    else:
        warn("Il2CppDumper failed (newer metadata versions need a fork); continuing with Cpp2IL")

    cpp2il = tools.cpp2il()
    game_dir = binary.parent if binary.name.startswith("GameAssembly") else game_root
    for fmt in formats or ["dummydll", "isil", "diffable-cs"]:
        step(f"Cpp2IL: {fmt}")
        target = output / f"cpp2il-{fmt}"
        result = run([cpp2il, "--game-path", game_dir, "--output-as", fmt, "--output-to", target],
                     log=log_dir / f"cpp2il-{fmt}.log", check=False)
        if result.returncode == 0 and target.exists():
            ok(str(target))
            results[f"cpp2il-{fmt}"] = target
        else:
            warn(f"Cpp2IL {fmt} failed; see {log_dir / f'cpp2il-{fmt}.log'}")
    if not results:
        raise HarnessError("no IL2CPP dump succeeded")
    return results
