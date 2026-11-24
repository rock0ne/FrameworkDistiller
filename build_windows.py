"""Build a portable one-file Windows executable from the local source tree."""

from __future__ import annotations

import os
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

from distiller import __version__
from build_notices import write_notices


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Build the Windows executable on Windows")
    root = Path(__file__).resolve().parent
    notices = write_notices(root)
    scratch = root / ".build"
    output = root / "dist"
    scratch.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256()
    inputs = [root / "run.py", root / "requirements.txt", *sorted((root / "distiller").glob("*.py")),
              *sorted((root / "static").iterdir())]
    for path in inputs:
        fingerprint.update(path.name.encode("utf-8"))
        fingerprint.update(path.read_bytes())
    workpath = scratch / f"work-{fingerprint.hexdigest()[:12]}"
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
        "--name", f"FrameworkDistiller-v{__version__}", "--add-data", f"{root / 'static'}{os.pathsep}static",
        "--collect-all", "pypdf", "--collect-all", "striprtf",
        "--collect-all", "defusedxml", "--distpath", str(output),
        "--workpath", str(workpath), "--specpath", str(scratch),
        str(root / "run.py"),
    ]
    subprocess.run(command, cwd=root, check=True)
    executable = output / f"FrameworkDistiller-v{__version__}.exe"
    if not executable.is_file():
        raise SystemExit(f"PyInstaller did not create {executable.name}")
    archive = output / f"FrameworkDistiller-v{__version__}-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(executable, executable.name)
        bundle.write(root / "README.md", "README.md")
        bundle.write(root / "LICENSE", "LICENSE")
        bundle.write(notices, notices.name)
    source_archive = output / f"FrameworkDistiller-v{__version__}-source.zip"
    android = root / "android"
    android_sources = [android / name for name in ("settings.gradle", "build.gradle", "gradle.properties",
                                                    "gradlew", "gradlew.bat", "app/build.gradle",
                                                    "sign-apk.sh",
                                                    "app/src/main/AndroidManifest.xml",
                                                    "app/src/main/python/bridge.py")]
    android_sources += list((android / "gradle" / "wrapper").glob("*"))
    android_sources += list((android / "app" / "src" / "main" / "java").rglob("*.java"))
    android_sources += list((android / "app" / "src" / "main" / "res").rglob("*.xml"))
    source_files = [root / "README.md", root / "LICENSE", notices, root / "requirements.txt", root / "requirements-build.txt",
                    root / "run.py", root / "refresh_local.py", root / "build_windows.py",
                    root / "build_notices.py", root / "prepare_android.py",
                    root / "Start Framework Distiller.cmd", root / ".gitignore",
                    *sorted((root / "third_party_licenses").iterdir()),
                    *sorted((root / "distiller").glob("*.py")),
                    *sorted((root / "static").iterdir()),
                    *sorted((root / "tests").glob("*.py")), *android_sources]
    with zipfile.ZipFile(source_archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in source_files:
            bundle.write(path, path.relative_to(root).as_posix())
    print(f"Executable: {executable}")
    print(f"GitHub-ready ZIP: {archive}")
    print(f"Source ZIP: {source_archive}")


if __name__ == "__main__":
    main()
