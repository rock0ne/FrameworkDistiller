"""Copy only the shared Python core and static UI into the Android build tree."""

from __future__ import annotations

import shutil
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    python_root = root / "android" / "app" / "src" / "main" / "python" / "distiller"
    static_root = root / "android" / "app" / "src" / "main" / "assets" / "static"
    python_root.mkdir(parents=True, exist_ok=True)
    static_root.mkdir(parents=True, exist_ok=True)
    for source in sorted((root / "distiller").glob("*.py")):
        shutil.copy2(source, python_root / source.name)
    for source in sorted((root / "static").iterdir()):
        if source.is_file():
            shutil.copy2(source, static_root / source.name)
    print(f"Android core staged: {python_root}")
    print(f"Android UI staged: {static_root}")


if __name__ == "__main__":
    main()
