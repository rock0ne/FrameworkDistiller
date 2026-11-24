"""Assemble the public, plain-text notice from pinned upstream license files."""

from __future__ import annotations

from pathlib import Path


COMPONENTS = (
    ("pypdf 6.10.0 (Windows and Android document parser)", "pypdf-6.10.0-LICENSE.txt"),
    ("striprtf 0.0.35 (Windows and Android document parser)", "striprtf-0.0.35-LICENSE.txt"),
    ("defusedxml 0.7.1 (Windows and Android XML parser)", "defusedxml-0.7.1-LICENSE.txt"),
    ("CPython 3.14.5 (Windows executable runtime)", "CPython-3.14.5-LICENSE.txt"),
    ("PyInstaller 6.22.3 (Windows executable bootloader and runtime hooks)", "PyInstaller-6.22.3-COPYING.txt"),
    ("CPython 3.12.15 (Android runtime through Chaquopy 17.2.0)", "CPython-3.12.15-LICENSE.txt"),
    ("Chaquopy 17.2.0 (Android Python runtime)", "Chaquopy-17.2.0-LICENSE.txt"),
)


def write_notices(root: Path) -> Path:
    licenses = root / "third_party_licenses"
    parts = [
        "FRAMEWORK DISTILLER — THIRD-PARTY NOTICES\n",
        "Framework Distiller's own MIT license is in the separate LICENSE file.\n",
        "The following upstream license texts are reproduced in full from the",
        "pinned files, with only line endings normalised. Build tools not embedded",
        "in a released binary are not",
        "listed here. This file accompanies the Windows ZIP and Android APK.\n",
    ]
    for title, filename in COMPONENTS:
        source = licenses / filename
        notice = source.read_text(encoding="utf-8").rstrip("\r\n")
        parts += ["=" * 76, title, f"Source file: third_party_licenses/{filename}", "=" * 76, notice, ""]
    target = root / "THIRD-PARTY-NOTICES.txt"
    target.write_text("\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    return target


if __name__ == "__main__":
    print(write_notices(Path(__file__).resolve().parent))
