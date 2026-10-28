# Testing and validation

REU separates source checks, Unity compilation, browser rendering and game behavior. Each answers a different question.

## Automated suite

With Python and .NET 10 SDK, from the repository root:

```powershell
python -m unittest discover -s tests -v
```

The suite covers:

- Module imports and CLI parsing.
- Synthetic Mono/IL2CPP/WebGL detection.
- UnityWebData round trips, gzip inputs, packing limits and extraction traversal checks.
- HTTP compression, range responses and traversal rejection.
- Building a small original fake game, scanning and patching its managed assemblies, then running before and after patching.
- Compiling injected C# against installed Unity assemblies when available.

The .NET integration test skips without `dotnet`. The Unity compilation test skips without the SDK or installed Editor assemblies. Inspect skips as well as failures.

## Publication check

On 2026-10-28 the existing suite completed **22 tests in 30.935 seconds, without skips or failures**, on Windows with Python 3.13.11 and .NET SDK 10.0.300. Installed Unity assemblies were available, so the injected-source compilation test ran.

This confirms exercised source and synthetic fixture behavior. It does not establish a licensed WebGL build, commercial-game export correctness, browser rendering, multiplayer operation or gameplay acceptance.

## GitHub Actions

The [workflow](../.github/workflows/ci.yml) uses `windows-latest`, Python 3.13 and .NET 10. It does not install or activate Unity. The Editor-dependent test normally skips on a hosted runner. The README badge shows the real workflow status; it covers only these checks.

## Optional Unity fixture

An original small fixture is included. With the required Editor, Windows build support and license:

```powershell
python tests/e2e/make_test_game.py
python u2w.py port work/_e2e/TestGameBuild --work work/_e2e/workspace --serve
```

The first command builds a desktop Mono input; the second attempts the export/WebGL workflow. Neither alone records browser gameplay acceptance. This publication task did not run the full fixture pipeline.

## Browser and gameplay acceptance

Record the Editor, browser, settings and patch rules. Check loading, rendering, scene transitions, movement/input, audio, save/reload and performance. Test networking separately when needed.

State whether a result verifies source checks, a Unity build, HTTP serving, browser rendering, gameplay or a physical device. Do not infer one from another.
