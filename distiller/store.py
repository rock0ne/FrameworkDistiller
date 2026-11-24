"""SQLite persistence for pinned documents, draft cards and private study state."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .distill import THEMES, make_card
from .ingest import Unit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT NOT NULL,
                    source_url TEXT NOT NULL, authority_url TEXT NOT NULL,
                    version TEXT NOT NULL, publication_date TEXT NOT NULL,
                    sha256 TEXT NOT NULL, format TEXT NOT NULL, provenance TEXT NOT NULL,
                    local_name TEXT NOT NULL, imported_at TEXT NOT NULL,
                    UNIQUE(source_id, sha256)
                );
                CREATE TABLE IF NOT EXISTS units (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    ref TEXT NOT NULL, title TEXT NOT NULL, source_text TEXT NOT NULL,
                    page INTEGER, source_link TEXT NOT NULL, family TEXT NOT NULL,
                    UNIQUE(document_id, ref)
                );
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units(id),
                    theme TEXT NOT NULL, depth TEXT NOT NULL, priority INTEGER NOT NULL,
                    core_rule TEXT NOT NULL, risk TEXT NOT NULL, audit_test TEXT NOT NULL,
                    trap TEXT NOT NULL, challenge TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS progress (
                    card_id TEXT PRIMARY KEY REFERENCES cards(id),
                    state TEXT NOT NULL CHECK(state IN ('unread','read','mastered')),
                    answer TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
                    card_id UNINDEXED, title, ref, theme, core_rule, risk, audit_test, trap
                );
                CREATE INDEX IF NOT EXISTS idx_cards_theme ON cards(theme, depth, priority);
                CREATE INDEX IF NOT EXISTS idx_units_doc ON units(document_id);
            """)

    def import_document(self, record: dict, units: list[Unit], local_name: str) -> dict:
        doc_id = f"{record['source_id']}@{record['sha256'][:16]}"
        count = 0
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone()
            if exists:
                # Preserve old notes while hiding OSCAL entries later marked withdrawn.
                current_refs = {unit.ref for unit in units}
                for unit in units:
                    db.execute("""UPDATE units SET title=?,source_text=?,page=?,source_link=?,family=?
                                  WHERE document_id=? AND ref=?""",
                               (unit.title, unit.text, unit.page, unit.source_link,
                                unit.family, doc_id, unit.ref))
                    draft = make_card(unit, record["source_id"])
                    if draft is not None:
                        unit_id = f"{doc_id}:{unit.ref}"
                        row = db.execute("SELECT reviewed FROM cards WHERE id=?", (unit_id,)).fetchone()
                        if row is not None and not row["reviewed"]:
                            db.execute("""UPDATE cards SET theme=?,depth=?,priority=?,core_rule=?,risk=?,
                                          audit_test=?,trap=?,challenge=? WHERE id=?""",
                                       (draft.theme, draft.depth, draft.priority, draft.core_rule,
                                        draft.risk, draft.audit_test, draft.trap, draft.challenge, unit_id))
                            db.execute("DELETE FROM cards_fts WHERE card_id=?", (unit_id,))
                            db.execute("INSERT INTO cards_fts VALUES (?,?,?,?,?,?,?,?)", (
                                unit_id, unit.title, unit.ref, draft.theme, draft.core_rule,
                                draft.risk, draft.audit_test, draft.trap))
                for row in db.execute("""SELECT c.id,u.ref FROM cards c JOIN units u ON c.unit_id=u.id
                                         WHERE u.document_id=?""", (doc_id,)).fetchall():
                    if row["ref"] not in current_refs:
                        db.execute("UPDATE cards SET priority=99 WHERE id=?", (row["id"],))
                count = db.execute("""SELECT COUNT(*) FROM cards c JOIN units u ON c.unit_id=u.id
                                      WHERE u.document_id=? AND c.priority<99""", (doc_id,)).fetchone()[0]
                return {"document_id": doc_id, "cards": count, "already_imported": True}
            db.execute("""INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                doc_id, record["source_id"], record["title"], record["source_url"],
                record["authority_url"], record["version"], record["publication_date"],
                record["sha256"], record["format"], record["provenance"],
                local_name, utc_now(),
            ))
            for unit in units:
                unit_id = f"{doc_id}:{unit.ref}"
                db.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?,?)", (
                    unit_id, doc_id, unit.ref, unit.title, unit.text, unit.page,
                    unit.source_link, unit.family,
                ))
                draft = make_card(unit, record["source_id"])
                if draft is None:
                    continue
                db.execute("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,0)", (
                    unit_id, unit_id, draft.theme, draft.depth, draft.priority,
                    draft.core_rule, draft.risk, draft.audit_test, draft.trap, draft.challenge,
                ))
                db.execute("INSERT INTO cards_fts VALUES (?,?,?,?,?,?,?,?)", (
                    unit_id, unit.title, unit.ref, draft.theme, draft.core_rule,
                    draft.risk, draft.audit_test, draft.trap,
                ))
                count += 1
        return {"document_id": doc_id, "cards": count, "already_imported": False}

    def documents(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT d.*, COUNT(CASE WHEN c.priority<99 THEN c.id END) AS card_count
                FROM documents d LEFT JOIN units u ON u.document_id=d.id
                LEFT JOIN cards c ON c.unit_id=u.id
                GROUP BY d.id ORDER BY d.imported_at DESC, d.title
            """).fetchall()
            return [dict(row) for row in rows]

    def cards(self, theme: str = "", depth: str = "", query: str = "",
              state: str = "", document_id: str = "", limit: int = 500) -> list[dict]:
        if theme and theme not in THEMES:
            return []
        if state and state not in {"unread", "read", "mastered"}:
            return []
        query = query.strip()[:100]
        terms = re.findall(r"[A-Za-z0-9]{2,}", query)[:8]
        where = ["c.priority<99"]
        params: list[object] = []
        if theme:
            where.append("c.theme=?")
            params.append(theme)
        if depth:
            where.append("c.depth=?")
            params.append(depth)
        if document_id:
            where.append("d.id=?")
            params.append(document_id)
        if state:
            where.append("COALESCE(p.state,'unread')=?")
            params.append(state)
        if re.fullmatch(r"[A-Za-z]{2,}\.[A-Za-z]{2,}(?:-[0-9]{2})?", query) or re.fullmatch(r"[A-Za-z]{2,}-\d+", query):
            where.append("u.ref=?")
            params.append(query.upper())
        elif terms:
            where.append("c.id IN (SELECT card_id FROM cards_fts WHERE cards_fts MATCH ?)")
            params.append(" OR ".join(f'"{term}"*' for term in terms))
        clause = " WHERE " + " AND ".join(where) if where else ""
        sql = """SELECT c.*, u.ref, u.title, u.source_text, u.source_link, u.page,
                        d.id AS document_id, d.title AS document_title, d.version,
                        d.publication_date, d.source_url, d.sha256, d.provenance,
                        COALESCE(p.state,'unread') AS state, COALESCE(p.answer,'') AS answer
                 FROM cards c JOIN units u ON u.id=c.unit_id
                 JOIN documents d ON d.id=u.document_id
                 LEFT JOIN progress p ON p.card_id=c.id""" + clause + " ORDER BY c.priority, d.title, u.ref LIMIT ?"
        params.append(min(max(limit, 1), 500))
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def source_excerpt(self, card_id: str) -> str:
        """Only source text for one active card; never answers or private notes."""
        with self.connect() as db:
            row = db.execute("""SELECT u.source_text FROM cards c JOIN units u ON u.id=c.unit_id
                                WHERE c.id=? AND c.priority<99""", (card_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown card")
        return row["source_text"]

    def set_progress(self, card_id: str, state: str, answer: str | None = None) -> None:
        if state not in {"unread", "read", "mastered"}:
            raise ValueError("Invalid reading state")
        if answer is not None and len(answer) > 10_000:
            raise ValueError("Answer is too long")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone():
                raise ValueError("Unknown card")
            old = db.execute("SELECT answer FROM progress WHERE card_id=?", (card_id,)).fetchone()
            final_answer = answer if answer is not None else (old[0] if old else "")
            db.execute("""INSERT INTO progress VALUES (?,?,?,?)
                          ON CONFLICT(card_id) DO UPDATE SET state=excluded.state,
                          answer=excluded.answer, updated_at=excluded.updated_at""",
                       (card_id, state, final_answer, utc_now()))

    def edit_card(self, card_id: str, fields: dict) -> None:
        allowed = {"core_rule", "risk", "audit_test", "trap", "challenge"}
        if set(fields) != allowed or any(not isinstance(v, str) or not v.strip() or len(v) > 1500 for v in fields.values()):
            raise ValueError("Supply all five non-empty card fields (max 1500 characters each)")
        if len(re.split(r"(?<=[.!?])\s+", fields["core_rule"].strip())) > 2:
            raise ValueError("Core rule must be at most two sentences")
        with self.connect() as db:
            row = db.execute("""SELECT u.title,u.ref,c.theme FROM cards c JOIN units u ON u.id=c.unit_id WHERE c.id=?""", (card_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown card")
            db.execute("""UPDATE cards SET core_rule=?,risk=?,audit_test=?,trap=?,challenge=?,reviewed=1 WHERE id=?""",
                       (fields["core_rule"].strip(), fields["risk"].strip(),
                        fields["audit_test"].strip(), fields["trap"].strip(),
                        fields["challenge"].strip(), card_id))
            db.execute("DELETE FROM cards_fts WHERE card_id=?", (card_id,))
            db.execute("INSERT INTO cards_fts VALUES (?,?,?,?,?,?,?,?)", (
                card_id, row["title"], row["ref"], row["theme"],
                fields["core_rule"], fields["risk"], fields["audit_test"], fields["trap"],
            ))
