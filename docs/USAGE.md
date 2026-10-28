# Usage and command reference

[English README](../README.md) · [README italiano](../README.it.md)

Run commands from the repository root. `python -m unity2web` is another entry point; examples retain `python u2w.py` for compatibility.

## Staged Mono workflow

```powershell
python u2w.py doctor
python u2w.py detect "D:\Games\MyUnityGame" --json
python u2w.py tools assetripper patcher shims ilverify
python u2w.py export "D:\Games\MyUnityGame" --work "work\my-game"
python u2w.py scan "work\my-game"
python u2w.py patch --work "work\my-game" --rules rules.example.json
python u2w.py verify --work "work\my-game"
python u2w.py build --work "work\my-game" --development
python u2w.py serve "work\my-game" --open
```

Review the exported project, originals, and patch report before building. A successful IL verification comparison does not certify game correctness. The integrated equivalent is `python u2w.py port "D:\Games\MyUnityGame" --work "work\my-game" --serve`.

## Command catalog

Use `python u2w.py <command> --help` for exact arguments.

| Command | Purpose |
| --- | --- |
| `doctor` | Discover Python, .NET, Editors, WebGL modules, Hub, license files and downloaded tools. A license-file finding is not build-time activation validation. |
| `tools [names ...]` | Download manifest versions and build .NET components. No names means all installers. Read warnings and verify readiness afterward. |
| `detect <game> [--json]` | Identify Unity version, backend, platform and dependency markers. Accepts folders, executables, APKs and WebGL builds. |
| `export <game> --work <ws>` | AssetRipper project reconstruction. Script modes: `dll`, `hybrid`, `decompiled`. Shader modes: `decompile`, `dummy`, `yaml`. |
| `scan <target> [--json <file>]` | Scan workspace, folder or DLL. `--all` includes BCL/Unity assemblies. |
| `patch --work <ws>` | Rewrite selected IL calls and inject runtime/project helpers, retaining originals. |
| `verify --work <ws>` | Compare ILVerify findings with the original baseline. |
| `build --work <ws>` | Unity WebGL build, optional packing and compression. |
| `port <game> --work <ws>` | Integrated workflow; `--serve` serves successful output. |
| `il2cpp <game> --work <ws>` | Il2CppDumper/Cpp2IL reference output for manual investigation. |
| `decompile --work <ws>` | ILSpy C# reference output; unused by the default DLL build path. |
| `pack <build> <StreamingAssets>` | Add StreamingAssets to UnityWebData. |
| `compress <build> [--gzip-only]` | Compressed sidecars; Brotli requires its optional Python package. |
| `serve <build-or-workspace>` | Local HTTP server; options include `--port`, `--host`, `--open`, `--isolate`. |
| `unpack-webgl <build-or-data> [--out <folder>]` | List package contents or extract when an output path is supplied. |
| `unity list` | List installed Editors. |
| `unity changeset <version>` | Look up the requested Editor changeset. |
| `unity install <version>` | Request Editor/WebGL installation through Hub. Activation is separate. |

## Patch rules

Copy the example before customization:

```powershell
Copy-Item rules.example.json work\my-game\rules.json
python u2w.py patch --work work\my-game --rules work\my-game\rules.json
```

The patcher accepts comments and trailing commas in rules. The example is not strict JSON for generic parsers.

| Key | Meaning |
| --- | --- |
| `allowNative` | Imports to preserve; the example permits `__Internal`. |
| `relinkInternal` | Libraries for which you supply a compatible WebGL implementation. |
| `stubReturns` | Constant results for selected native entry points; other stubs typically return defaults. |
| `disable` | Disable groups: `threads`, `process`, `folders`, `paths`, `urls`, `pinvoke`. |
| `skipThreadBodies` | Selected workers that must not execute on the main thread, such as infinite loops. |
| `renameAssemblies` | Rename reserved assemblies and update relevant project references. |

CLI overrides include `--internal LIB`, `--disable GROUP`, `--stub-return ENTRY=VALUE`, and `--resolve DIR`. Stubs change semantics: test the affected feature.

## Editor and build options

The default selection requires the detected Editor version. `--unity` selects an installation; `--unity-version` changes the requested version; `--allow-version-mismatch` allows another patch release in the same Unity line. Overrides do not establish asset compatibility.

| Option | Default or behavior |
| --- | --- |
| `--development` | Development build output. |
| `--max-memory MB` | 4096 MB maximum heap for Unity 2021.2+. |
| `--legacy-memory MB` | 1024 MB fixed heap; CLI help describes a 2032 MB maximum. |
| `--exceptions MODE` | `FullWithoutStacktrace`; see help for supported choices. |
| `--pack-limit-mb MB` | Skip packing above 1024 MB by default. |
| `--no-pack` | Keep assets outside `.data`; synchronous reads may require adaptation. |
| `--no-compress` | Skip compressed sidecars. |
| `--emscripten-args TEXT` | Additional builder compiler settings. |

`port --force-export` replaces an existing export. `--force-blocked` bypasses an early verdict; it cannot reconstruct missing IL2CPP logic or add unsupported rendering backends.

## Local files and serving

`--work work\my-game` contains export output, `original-assemblies/`, reports, logs, and default `build/webgl/` output. Otherwise the default is `work/<game-slug>/`.

`work/`, `tools/`, compiled binaries, caches and local secrets are ignored. Keep game-derived files in the workspace; inspect Git status if choosing a workspace elsewhere.

The server defaults to `127.0.0.1:8080`. `--isolate` adds COOP/COEP when needed. Remote publication is a separate operation.
