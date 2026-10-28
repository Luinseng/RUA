"""Unit and integration tests for the harness. Run: python -m unittest discover -s tests -v"""

from __future__ import annotations

import gzip
import http.client
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unity2web import detect, serve, webdata  # noqa: E402
from unity2web.common import HarnessError  # noqa: E402


def make_webdata(path: Path, files: dict[str, bytes]) -> None:
    webdata.write([(name, data) for name, data in files.items()], path)


class ImportTests(unittest.TestCase):
    def test_every_module_imports_and_cli_parses(self):
        import importlib
        for name in ("common", "detect", "tools", "assetripper", "il2cpp", "project", "unity", "webdata", "serve", "cli"):
            importlib.import_module(f"unity2web.{name}")
        from unity2web.cli import build_parser
        args = build_parser().parse_args(["build", "--work", "w", "--pack-limit-mb", "10", "--legacy-memory", "512"])
        self.assertEqual((args.pack_limit_mb, args.legacy_memory), (10, 512))


class WebDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="u2w-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        data = self.tmp / "game.data"
        files = {"data.unity3d": b"UnityFS\0" + b"x" * 1000, "Il2CppData/Metadata/global-metadata.dat": b"\xaf\x1b\xb1\xfa" * 50}
        make_webdata(data, files)
        out = self.tmp / "out"
        entries = webdata.unpack(data, out)
        self.assertEqual([e.name for e in entries], list(files))
        for name, content in files.items():
            self.assertEqual((out / name).read_bytes(), content)

    def test_reads_gzip_compressed_build(self):
        data = self.tmp / "game.data"
        make_webdata(data, {"a.txt": b"hello"})
        gz = self.tmp / "game.data.gz"
        gz.write_bytes(gzip.compress(data.read_bytes()))
        self.assertIn("a.txt", "\n".join(webdata.describe(gz)))

    def test_pack_streaming_assets(self):
        build = self.tmp / "build"
        (build / "Build").mkdir(parents=True)
        data = build / "Build" / "game.data"
        make_webdata(data, {"data.unity3d": b"engine", "Resources/unity_builtin_extra": b"res"})
        streaming = self.tmp / "StreamingAssets"
        (streaming / "sub").mkdir(parents=True)
        (streaming / "hello.txt").write_text("hi")
        (streaming / "sub" / "bank.bin").write_bytes(b"\x00\x01" * 3000)

        self.assertEqual(webdata.pack_streaming_assets(build, streaming), 2)
        # Packing twice replaces the previous copy instead of duplicating it.
        self.assertEqual(webdata.pack_streaming_assets(build, streaming), 2)

        out = self.tmp / "unpacked"
        names = [e.name for e in webdata.unpack(data, out)]
        self.assertEqual(names.count("u2wdata/StreamingAssets/hello.txt"), 1)
        self.assertIn(webdata.PACKED_MARKER, names)
        self.assertEqual((out / "data.unity3d").read_bytes(), b"engine")
        self.assertEqual((out / "u2wdata/StreamingAssets/sub/bank.bin").read_bytes(), b"\x00\x01" * 3000)

    def test_pack_skips_editor_files_and_respects_limit(self):
        build = self.tmp / "build"
        (build / "Build").mkdir(parents=True)
        make_webdata(build / "Build" / "game.data", {"data.unity3d": b"engine"})
        streaming = self.tmp / "StreamingAssets"
        streaming.mkdir()
        (streaming / "level.bin").write_bytes(b"x" * 10)
        (streaming / "level.bin.meta").write_text("guid: 1")
        (streaming / ".DS_Store").write_bytes(b"junk")
        self.assertEqual(webdata.pack_streaming_assets(build, streaming), 1)
        (streaming / "big.bin").write_bytes(b"0" * (2 << 20))
        self.assertEqual(webdata.pack_streaming_assets(build, streaming, limit_mb=1), -1)

    def test_pack_reads_gzip_unityweb(self):
        build = self.tmp / "legacy"
        (build / "Build").mkdir(parents=True)
        plain = self.tmp / "plain.data"
        make_webdata(plain, {"data.unity3d": b"engine"})
        (build / "Build" / "game.data.unityweb").write_bytes(gzip.compress(plain.read_bytes()))
        streaming = self.tmp / "SA"
        streaming.mkdir()
        (streaming / "a.txt").write_text("a")
        self.assertEqual(webdata.pack_streaming_assets(build, streaming), 1)
        names = [line.split()[-1] for line in webdata.describe(build / "Build" / "game.data.unityweb")]
        self.assertIn("u2wdata/StreamingAssets/a.txt", names)

    def test_rejects_non_webdata(self):
        bad = self.tmp / "bad.data"
        bad.write_bytes(b"not unity")
        with self.assertRaises(HarnessError):
            webdata.unpack(bad, self.tmp / "o")

    def test_rejects_path_traversal(self):
        data = self.tmp / "evil.data"
        make_webdata(data, {"../escape.txt": b"x"})
        with self.assertRaises(HarnessError):
            webdata.unpack(data, self.tmp / "o")


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="u2w-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _game(self, *, il2cpp: bool, hdrp: bool = False) -> Path:
        root = self.tmp / "MyGame"
        data = root / "MyGame_Data"
        data.mkdir(parents=True)
        (root / "MyGame.exe").write_bytes(b"MZ")
        (root / "UnityPlayer.dll").write_bytes(b"MZ")
        (root / "MonoBleedingEdge").mkdir()
        header = b"\x00" * 20 + b"2021.3.45f1\x00" + b"\x00" * 100
        (data / "globalgamemanagers").write_bytes(header)
        (data / "Plugins" / "x86_64").mkdir(parents=True)
        (data / "Plugins" / "x86_64" / "steam_api64.dll").write_bytes(b"MZ")
        (data / "StreamingAssets").mkdir()
        (data / "StreamingAssets" / "x.bin").write_bytes(b"1234")
        if il2cpp:
            (root / "GameAssembly.dll").write_bytes(b"MZ")
            meta = data / "il2cpp_data" / "Metadata"
            meta.mkdir(parents=True)
            (meta / "global-metadata.dat").write_bytes(b"\xaf\x1b\xb1\xfa" + (b"Unity.RenderPipelines.HighDefinition.Runtime.dll" if hdrp else b""))
        else:
            managed = data / "Managed"
            managed.mkdir()
            for name in ("Assembly-CSharp.dll", "UnityEngine.CoreModule.dll", "mscorlib.dll", "Unity.TextMeshPro.dll",
                         "Rewired_Core.dll", "Unity.RenderPipelines.Universal.Runtime.dll"):
                (managed / name).write_bytes(b"MZ")
        return root

    def test_mono_game(self):
        det = detect.detect(self._game(il2cpp=False))
        self.assertEqual(det.unity_version, "2021.3.45f1")
        self.assertEqual(det.backend, "mono")
        self.assertEqual(det.platform, "windows")
        self.assertEqual(det.dotnet_profile, "4.x")
        self.assertEqual(det.render_pipeline, "urp")
        self.assertEqual(det.verdict, "auto")
        self.assertIn("Assembly-CSharp.dll", det.game_assemblies)
        self.assertNotIn("UnityEngine.CoreModule.dll", det.game_assemblies)
        self.assertIn("Unity.TextMeshPro.dll", det.package_assemblies)
        names = {m["name"] for m in det.middleware}
        self.assertEqual(names, {"Steamworks", "Rewired"})
        self.assertEqual(det.streaming_assets_bytes, 4)

    def test_exe_path_is_accepted(self):
        det = detect.detect(self._game(il2cpp=False) / "MyGame.exe")
        self.assertEqual(det.backend, "mono")

    def test_il2cpp_game_needs_manual_work(self):
        det = detect.detect(self._game(il2cpp=True))
        self.assertEqual(det.backend, "il2cpp")
        self.assertEqual(det.verdict, "manual")

    def test_hdrp_is_blocked(self):
        det = detect.detect(self._game(il2cpp=True, hdrp=True))
        self.assertEqual(det.render_pipeline, "hdrp")
        self.assertEqual(det.verdict, "blocked")

    def test_webgl_build(self):
        build = self.tmp / "web" / "Build"
        build.mkdir(parents=True)
        (build / "web.loader.js").write_text("// loader")
        (build / "web.framework.js").write_text('var unityVersion="2022.3.10f1";')
        make_webdata(build / "web.data", {"data.unity3d": b"x"})
        det = detect.detect(self.tmp / "web")
        self.assertEqual(det.platform, "webgl")
        self.assertEqual(det.unity_version, "2022.3.10f1")
        self.assertEqual(det.verdict, "already-web")

    def test_not_unity(self):
        (self.tmp / "random").mkdir()
        with self.assertRaises(HarnessError):
            detect.detect(self.tmp / "random")

    def test_version_regex(self):
        for raw, expected in ((b"\x005.6.7f1\x00", "5.6.7f1"), (b"\x002019.4.40f1\x00", "2019.4.40f1"), (b"6000.0.58f2", "6000.0.58f2")):
            self.assertEqual(detect.VERSION_RE.search(raw).group(1).decode(), expected)
        self.assertIsNone(detect.VERSION_RE.search(b"12019.4.40f1"))


class ServeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="u2w-serve-"))
        (cls.tmp / "Build").mkdir()
        (cls.tmp / "index.html").write_text("<html></html>")
        cls.payload = b"\x00asm" + bytes(range(256)) * 40
        (cls.tmp / "Build" / "game.wasm").write_bytes(cls.payload)
        webdata.compress(cls.tmp, brotli_too=False)
        (cls.tmp / "Build" / "unity.data.gz").write_bytes(gzip.compress(b"data"))
        cls.server = serve.make_server(cls.tmp, port=0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _get(self, path: str, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def test_negotiates_gzip_sidecar(self):
        response, body = self._get("/Build/game.wasm", {"Accept-Encoding": "gzip, br"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/wasm")
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(body), self.payload)

    def test_plain_without_accept_encoding(self):
        response, body = self._get("/Build/game.wasm")
        self.assertIsNone(response.getheader("Content-Encoding"))
        self.assertEqual(body, self.payload)

    def test_unity_precompressed_file(self):
        response, body = self._get("/Build/unity.data.gz")
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(response.getheader("Content-Type"), "application/octet-stream")
        self.assertEqual(gzip.decompress(body), b"data")

    def test_range_request(self):
        response, body = self._get("/Build/game.wasm", {"Range": "bytes=4-9"})
        self.assertEqual(response.status, 206)
        self.assertEqual(body, self.payload[4:10])
        self.assertEqual(response.getheader("Content-Range"), f"bytes 4-9/{len(self.payload)}")

    def test_traversal_blocked(self):
        response, _ = self._get("/../../etc/passwd")
        self.assertIn(response.status, (403, 404))


@unittest.skipUnless(shutil.which("dotnet"), "dotnet SDK not installed")
class PatcherIntegrationTests(unittest.TestCase):
    """Compiles a fake game, patches it with u2w-patcher and checks the runtime behaviour of every rewrite."""

    def test_patch_and_run(self):
        out = Path(tempfile.mkdtemp(prefix="u2w-patch-"))
        try:
            def build(project: str, name: str) -> Path:
                target = out / "bin" / name
                subprocess.run(["dotnet", "build", str(ROOT / "dotnet" / project), "-c", "Release", "-o", str(target),
                                "-nologo", "-v", "q"], check=True, capture_output=True)
                return target

            shims = build("Unity2Web.Shims", "shims") / "Unity2Web.Shims.dll"
            patcher = build("Unity2Web.Patcher", "patcher") / "u2w-patcher.dll"
            game = build("Tests/FakeGame", "game")
            runner = build("Tests/PatchRunner", "runner") / "PatchRunner.dll"

            original = out / "original"
            patched = out / "patched"
            for folder in (original, patched):
                folder.mkdir()
                for dll in ("Assembly-CSharp.dll", "UnityEngine.CoreModule.dll"):
                    shutil.copy(game / dll, folder / dll)
            shutil.copy(shims, patched / "Unity2Web.Shims.dll")

            rules = ROOT / "dotnet" / "Tests" / "test-rules.json"
            scan = subprocess.run(["dotnet", str(patcher), "scan", str(original), "--json", str(out / "scan.json"),
                                   "--rules", str(rules)], capture_output=True, text=True)
            self.assertEqual(scan.returncode, 0, scan.stderr)
            report = json.loads((out / "scan.json").read_text())
            self.assertEqual(report["Totals"]["threadLoops"], 1)
            self.assertGreater(report["Totals"]["patched"], 10)

            patch = subprocess.run(["dotnet", str(patcher), "patch", str(patched), "--backup", str(out / "backup"),
                                    "--rules", str(rules), "--report", str(out / "patch.json")], capture_output=True, text=True)
            self.assertEqual(patch.returncode, 0, patch.stdout + patch.stderr)

            unpatched_run = subprocess.run(["dotnet", str(runner), str(original), "original"], capture_output=True, text=True)
            self.assertEqual(unpatched_run.returncode, 0, unpatched_run.stdout)

            patched_run = subprocess.run(["dotnet", str(runner), str(patched), "patched"], capture_output=True, text=True)
            self.assertEqual(patched_run.returncode, 0, patched_run.stdout)
            self.assertIn("ALL PASS", patched_run.stdout)
        finally:
            shutil.rmtree(out, ignore_errors=True)


def _newest_editor_managed() -> Path | None:
    try:
        from unity2web import unity
        editors = unity.editors()
    except Exception:
        return None
    for editor in reversed(editors):
        modules = editor.root / "Data" / "Managed" / "UnityEngine"
        if modules.is_dir():
            return modules
    return None


@unittest.skipUnless(shutil.which("dotnet") and _newest_editor_managed(), "needs dotnet and an installed Unity 2017.2+ editor")
class UnityCompileTests(unittest.TestCase):
    """The injected runtime/editor C# must compile against a real Unity's API (no Unity licence needed)."""

    def test_injected_sources_compile(self):
        project = ROOT / "tests" / "unity_compile"
        out = Path(tempfile.mkdtemp(prefix="u2w-ucc-"))
        try:
            shims = out / "shims"
            subprocess.run(["dotnet", "build", str(ROOT / "dotnet" / "Unity2Web.Shims"), "-c", "Release", "-o", str(shims),
                            "-nologo", "-v", "q"], check=True, capture_output=True)
            defines = "%3B".join(["UNITY_WEBGL", "UNITY_5_5_OR_NEWER", "UNITY_5_6_OR_NEWER", "UNITY_2018_1_OR_NEWER",
                                  "UNITY_2019_2_OR_NEWER", "UNITY_2020_1_OR_NEWER"])
            result = subprocess.run(["dotnet", "build", str(project), "-nologo", "-v", "q", "-o", str(out / "bin"),
                                     f"-p:UnityManaged={_newest_editor_managed()}", f"-p:ShimsDll={shims / 'Unity2Web.Shims.dll'}",
                                     "-p:WithEditor=true", f"-p:UnityDefines={defines}"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:])
        finally:
            shutil.rmtree(out, ignore_errors=True)
            shutil.rmtree(project / "obj", ignore_errors=True)
            shutil.rmtree(project / "bin", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
