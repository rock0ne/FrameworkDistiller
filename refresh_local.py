"""Rebuild draft cards from already pinned bytes; no network or new sources."""

from __future__ import annotations

import hashlib
from pathlib import Path

from distiller.ingest import extract_units
from distiller.server import LocalApp


def main() -> None:
    app = LocalApp(Path(__file__).resolve().parent / "data")
    for record in app.store.documents():
        path = app.sources / record["local_name"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"Pinned source hash mismatch: {record['title']}")
        units = extract_units(data, record)
        result = app.store.import_document(record, units, record["local_name"])
        print(f"{record['title']}: {result['cards']} active cards, pinned bytes verified")


if __name__ == "__main__":
    main()
