"""Builds the end-to-end fixture: a small Windows/Mono Unity game compiled with the installed editor.

    python tests/e2e/make_test_game.py [--unity <Unity.exe>]

Output: work/_e2e/TestGameBuild/U2WTestGame.exe (+ _Data). Then run the real pipeline on it:

    python u2w.py port work/_e2e/TestGameBuild --work work/_e2e/workspace --allow-version-mismatch --serve
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from unity2web import unity  # noqa: E402
from unity2web.common import HarnessError, ok, step  # noqa: E402

E2E = ROOT / "work" / "_e2e"
SOURCE = Path(__file__).resolve().parent / "TestGameSource"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unity", help="Unity.exe to use (default: newest installed)")
    args = parser.parse_args()

    editor = unity.pick_editor(None, explicit=Path(args.unity) if args.unity else None)
    project = E2E / "TestGameProject"
    build = E2E / "TestGameBuild"

    if not (project / "ProjectSettings").is_dir():
        step(f"creating Unity {editor.version} project")
        project.parent.mkdir(parents=True, exist_ok=True)
        code = unity.batch(editor, project, E2E / "logs" / "create-project.log", ["-createProject", str(project)], build_target=None)
        if code != 0:
            raise HarnessError(f"project creation failed (exit {code})")

    shutil.copytree(SOURCE / "Assets", project / "Assets", dirs_exist_ok=True)
    if build.exists():
        shutil.rmtree(build)
    step("building the Windows/Mono test game")
    code = unity.batch(editor, project, E2E / "logs" / "build-test-game.log",
                       ["-executeMethod", "MakeTestGame.Build", "-testGameOut", str(build)], build_target="Win64")
    if code != 0 or not (build / "U2WTestGame.exe").exists():
        raise HarnessError(f"test game build failed (exit {code}); see {E2E / 'logs' / 'build-test-game.log'}")
    ok(f"test game: {build / 'U2WTestGame.exe'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
