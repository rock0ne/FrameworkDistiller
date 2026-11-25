"""127.0.0.1-only UI. Optional explicit BYOK AI, no lab/SOC/MCP/shell routes."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .ai import AIError, StudyAI, MODELS
from .catalog import OFFICIAL, official_list
from .distill import THEMES
from .ingest import (FORMAT_BY_EXTENSION, MAX_BYTES, IngestError, download_official,
                     download_public, extract_units, read_dropin, read_uploaded)
from .store import Store
from .reader import extract_reading
from .study import (ACTIONS, LENSES, ai_prompt, artifact_markdown, artifact_svg,
                    cache_key, challenge_prompt,
                    choose_scope, offline_body, parse_ai_answer, preview)
from .storage import folder_path, save_paths


ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(os.environ.get("FRAMEWORK_DISTILLER_STATIC", str(ROOT / "static")))
MAX_JSON_BODY = 32_768


class LocalApp:
    def __init__(self, data_root: Path, *, inbox_root: Path | None = None,
                 settings_file: Path | None = None, platform: str = "desktop"):
        self._lock = threading.RLock()
        self.settings_file = settings_file
        self.platform = platform
        self.data_root = folder_path(data_root, create=True)
        self.sources = self.data_root / "sources"
        self.inbox_custom = inbox_root is not None
        self.inbox = folder_path(inbox_root, create=True) if inbox_root else self.data_root / "inbox"
        self.sources.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_root / "study.sqlite3")
        self.ai = StudyAI()
        # A fresh, process-local capability for the browser/WebView session.
        self.session_token = secrets.token_urlsafe(32)

    def storage_status(self) -> dict:
        return {"data_root": str(self.data_root), "inbox_root": str(self.inbox),
                "inbox_custom": self.inbox_custom,
                "editable": self.platform == "desktop" and self.settings_file is not None,
                "platform": self.platform,
                "notice": "Changing folders does not move or delete the previous library."}

    def set_storage(self, data_root: str, inbox_root: str) -> dict:
        if self.platform != "desktop" or self.settings_file is None:
            raise ValueError("This device uses its system file picker and private app storage")
        if not isinstance(data_root, str) or not isinstance(inbox_root, str):
            raise ValueError("Folder paths must be text")
        with self._lock:
            selected_root = folder_path(data_root, create=True)
            selected_inbox = (folder_path(inbox_root, create=True) if inbox_root.strip()
                              else selected_root / "inbox")
            selected_inbox.mkdir(parents=True, exist_ok=True)
            selected_sources = selected_root / "sources"
            selected_sources.mkdir(parents=True, exist_ok=True)
            selected_store = Store(selected_root / "study.sqlite3")
            save_paths(self.settings_file, selected_root,
                       None if selected_inbox == selected_root / "inbox" else selected_inbox)
            self.data_root = selected_root
            self.inbox = selected_inbox
            self.inbox_custom = selected_inbox != selected_root / "inbox"
            self.sources = selected_sources
            self.store = selected_store
            return self.storage_status()

    def _commit_source(self, data: bytes, record: dict, units: list) -> dict:
        extension = ".json" if record["format"] == "oscal-json" else f".{record['format']}"
        local_name = record["sha256"] + extension
        target = self.sources / local_name
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=self.sources, prefix=".incoming-", delete=False) as temp:
                temp.write(data)
                temp_name = temp.name
            try:
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        result = self.store.import_document(record, units, local_name)
        result["sha256"] = record["sha256"]
        result["source_url"] = record["source_url"]
        result["version"] = record["version"]
        result["provenance"] = record["provenance"]
        return result

    def import_official(self, source_id: str) -> dict:
        data, record = download_official(source_id)
        units = extract_units(data, record)
        return self._commit_source(data, record, units)

    def import_dropin(self, name: str, *, max_batch_bytes: int = MAX_BYTES) -> dict:
        data, record = read_dropin(self.inbox, name, max_batch_bytes=max_batch_bytes)
        units = extract_units(data, record)
        result = self._commit_source(data, record, units)
        result["source_bytes"] = len(data)
        return result

    def import_uploaded(self, data: bytes, name: str, meta: dict) -> dict:
        data, record = read_uploaded(data, name, meta)
        units = extract_units(data, record)
        return self._commit_source(data, record, units)

    def import_url(self, body: dict) -> dict:
        data, record = download_public(body.get("source_url", ""), body.get("title", ""),
                                       body.get("version", ""), body.get("publication_date", ""),
                                       body.get("sha256", ""))
        units = extract_units(data, record)
        return self._commit_source(data, record, units)

    def inbox_names(self) -> list[dict]:
        files = (p for p in self.inbox.iterdir()
                 if p.is_file() and not p.is_symlink()
                 and p.suffix.lower() in FORMAT_BY_EXTENSION)
        return [{"name": p.name, "size": p.stat().st_size,
                 "has_sidecar": (self.inbox / f"{p.name}.source.json").is_file(),
                 "importable": p.stat().st_size <= MAX_BYTES}
                for p in sorted(files, key=lambda item: item.name.lower())[:200]]

    def ensure_reader(self, document_id: str) -> None:
        """Build once from the exact pinned bytes; never trust a mutable inbox copy."""
        if not isinstance(document_id, str) or len(document_id) > 256:
            raise ValueError("Invalid document ID")
        doc = self.store.document(document_id)
        if self.store.reader_ready(document_id):
            return
        local_name = doc["local_name"]
        if (not isinstance(local_name, str) or not re.fullmatch(r"[a-f0-9]{64}\.[a-z0-9-]+", local_name)
                or not local_name.startswith(doc["sha256"] + ".")):
            raise ValueError("Pinned source filename is invalid")
        target = self.sources / local_name
        if target.is_symlink() or not target.is_file():
            raise ValueError("Pinned source copy is unavailable")
        with target.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != doc["sha256"]:
            raise ValueError("Pinned source copy failed its SHA-256 check")
        self.store.save_reader(document_id, extract_reading(data, doc))

    def study_generate(self, body: dict) -> dict:
        document_id = body.get("document_id", "")
        action = body.get("action", "")
        lens = body.get("lens", "general")
        mode = body.get("mode", "offline")
        if lens not in LENSES or action not in ACTIONS or mode not in {"offline", "ai"}:
            raise ValueError("Choose a supported action, lens and mode")
        question = body.get("question", "") if action.startswith("ask_") else ""
        if action.startswith("ask_") and (mode != "ai" or not isinstance(question, str)
                                           or not 1 <= len(question.strip()) <= 500):
            raise ValueError("Ask AI needs a question of 1–500 characters")
        question = question.strip() if isinstance(question, str) else ""
        self.ensure_reader(document_id)
        doc, pages, scope_id = choose_scope(self.store, document_id, action,
                                           body.get("chapter_no"), body.get("page_no"))
        if not pages:
            raise ValueError("No extractable text in this scope")
        source_digest = hashlib.sha256("\n".join(page["source_text"] for page in pages).encode()).hexdigest()
        provider = body.get("provider", "") if mode == "ai" else "offline"
        if mode == "ai" and provider not in MODELS:
            raise ValueError("Choose a supported AI provider")
        model = self.ai.status()["models"][provider] if mode == "ai" else "structural-v1"
        key = cache_key(doc["sha256"], action, scope_id, lens, provider, model,
                        source_digest, question)
        cached = self.store.cached_artifact(key)
        if cached:
            return cached
        if mode == "offline":
            structure = self.store.reader_structure(document_id) if action == "file_map" else None
            result = offline_body(doc, pages, action, scope_id, structure)
            input_chars = sum(len(page["source_text"]) for page in pages)
        else:
            exact = preview(self.store, document_id, action, lens, provider, model,
                            body.get("chapter_no"), body.get("page_no"), question)
            if body.get("approval_hash") != exact["approval_hash"]:
                raise ValueError("Review the exact AI text preview before sending")
            answer = self.ai.study(provider, exact["text"], exact["instruction"])
            result = parse_ai_answer(answer["answer"], action, pages, set(exact["source_refs"]))
            result["coverage"] = exact["coverage"]
            result["disclosure"] += " " + exact["coverage_disclosure"]
            if exact["coverage"]["truncated_pages"]:
                result["title"] = "Excerpt-only interpretation · " + result["title"]
            input_chars = exact["characters"]
        if question:
            result["question"] = question
        return self.store.save_artifact({"cache_key": key, "document_id": document_id,
            "action": action, "scope_id": scope_id, "lens": lens, "provider": provider,
            "model": model, "source_digest": source_digest, "input_chars": input_chars,
            "body": result})


def _as_markdown(cards: list[dict]) -> str:
    lines = ["# Framework study notes", "", "Local draft export. Verify every distilled summary against its pinned primary source.", ""]
    for card in cards:
        lines.extend([
            f"## {card['document_title']} — {card['ref']}: {card['title']}", "",
            f"- Theme: {card['theme']} · Depth: {card['depth']} · Self-marked: {card['state']}",
            f"- Source version/date: {card['version']} · {card['publication_date']}",
            f"- Source SHA-256: `{card['sha256']}`", "",
            "Distilled summary — verify authoritative wording against pinned source citation.", "",
            f"1. **Core rule:** {card['core_rule']}",
            f"2. **Risk if ignored (auditor interpretation):** {card['risk']}",
            f"3. **Senior auditor test (auditor interpretation):** {card['audit_test']}",
            f"4. **Classic auditee trap (scenario prompt):** {card['trap']}",
            f"5. **Source:** {card['ref']} — {card['source_link'] or 'local file; verify SHA-256 above'}", "",
            f"**Challenge:** {card['challenge']}", "",
        ])
        if card["answer"]:
            lines.extend(["**My answer (self-reported, not graded):**", "", card["answer"], ""])
    return "\n".join(lines)


def make_handler(app: LocalApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"FrameworkDistiller/{__version__}"

        def _allowed_host(self) -> bool:
            host = self.headers.get("Host", "")
            return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def _allowed_origin(self) -> bool:
            return self.headers.get("Origin", "") in {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }

        def _has_session(self) -> bool:
            cookies = self.headers.get("Cookie", "")
            if len(cookies) > 4096:
                return False
            for part in cookies.split(";"):
                name, separator, value = part.strip().partition("=")
                if name == "fd_session" and separator and hmac.compare_digest(
                        value.encode("utf-8"), app.session_token.encode("ascii")):
                    return True
            return False

        def _start_session(self) -> None:
            if (not self._allowed_origin() or
                    self.headers.get("X-Local-Client") != "FrameworkDistiller" or
                    not hmac.compare_digest(self.headers.get("X-Session-Token", "").encode("utf-8"),
                                            app.session_token.encode("ascii")) or
                    self.headers.get("Content-Length", "0") not in {"0", ""}):
                self._json(403, {"error": "Local app launch required"})
                return
            self.send_response(204)
            self.send_header("Set-Cookie", f"fd_session={app.session_token}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def _json(self, status: int, payload: dict | list) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _bytes(self, status: int, data: bytes, content_type: str, download: str = "") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; connect-src 'self'")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        def _params(self) -> tuple[str, dict[str, str]]:
            parsed = urlsplit(self.path)
            values = {key: val[0] for key, val in parse_qs(parsed.query, keep_blank_values=True).items()}
            return parsed.path, values

        def do_GET(self) -> None:
            with app._lock:
                self._handle_get()

        def _handle_get(self) -> None:
            if not self._allowed_host():
                self._json(403, {"error": "Local host only"})
                return
            route, params = self._params()
            if route.startswith("/api/") and not self._has_session():
                self._json(403, {"error": "Open Framework Distiller from its app launcher"})
                return
            try:
                if route == "/api/status":
                    self._json(200, {"mode": "local-with-optional-byok", "version": __version__, "model_calls": app.ai.status()["enabled"],
                                     "official": official_list(), "documents": app.store.documents(),
                                     "themes": THEMES, "inbox": app.inbox_names(),
                                     "inbox_path": str(app.inbox),
                                     "storage": app.storage_status()})
                    return
                if route == "/api/storage":
                    self._json(200, app.storage_status())
                    return
                if route == "/api/ai/status":
                    self._json(200, app.ai.status())
                    return
                if route == "/api/cards":
                    cards = app.store.cards(params.get("theme", ""), params.get("depth", ""),
                                            params.get("q", ""), params.get("state", ""),
                                            params.get("document_id", ""))
                    self._json(200, {"cards": cards, "count": len(cards)})
                    return
                if route == "/api/reader":
                    document_id = params.get("document_id", "")
                    app.ensure_reader(document_id)
                    self._json(200, app.store.reader_structure(document_id))
                    return
                if route == "/api/reader/page":
                    document_id = params.get("document_id", "")
                    app.ensure_reader(document_id)
                    try:
                        page_no = int(params.get("page_no", "0"))
                    except ValueError:
                        raise ValueError("Choose a valid page") from None
                    if not 1 <= page_no <= 2000:
                        raise ValueError("Choose a valid page")
                    pages = app.store.reader_pages(document_id, page_no=page_no)
                    if not pages:
                        raise ValueError("Unknown page")
                    page = pages[0]
                    page["challenge"] = challenge_prompt(params.get("lens", "general"), page)
                    self._json(200, page)
                    return
                if route == "/api/study/export":
                    key, fmt = params.get("key", ""), params.get("format", "")
                    if not re.fullmatch(r"[a-f0-9]{64}", key) or fmt not in {"md", "svg"}:
                        raise ValueError("Invalid study export")
                    item = app.store.cached_artifact(key)
                    if item is None:
                        raise ValueError("Study result is unavailable")
                    if fmt == "md":
                        payload = artifact_markdown(item).encode("utf-8")
                        content_type = "text/markdown; charset=utf-8"
                    else:
                        payload = artifact_svg(item).encode("utf-8")
                        content_type = "image/svg+xml; charset=utf-8"
                    self._bytes(200, payload, content_type, f"framework-study-{key[:12]}.{fmt}")
                    return
                if route == "/api/export":
                    cards = app.store.cards(params.get("theme", ""), params.get("depth", ""),
                                            params.get("q", ""), params.get("state", ""),
                                            params.get("document_id", ""))
                    self._bytes(200, _as_markdown(cards).encode("utf-8"),
                                "text/markdown; charset=utf-8", "framework-study-notes.md")
                    return
                files = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/style.css": ("style.css", "text/css; charset=utf-8"),
                         "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
                         "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
                         "/offline.html": ("offline.html", "text/html; charset=utf-8"),
                         "/icon-192.png": ("icon-192.png", "image/png"),
                         "/icon-512.png": ("icon-512.png", "image/png")}
                if route in files:
                    name, content_type = files[route]
                    self._bytes(200, (STATIC / name).read_bytes(), content_type)
                    return
                self._json(404, {"error": "Not found"})
            except (ValueError, OSError) as exc:
                self._json(400, {"error": str(exc)})

        def do_POST(self) -> None:
            with app._lock:
                self._handle_post()

        def _handle_post(self) -> None:
            if not self._allowed_host():
                self._json(403, {"error": "Local host only"})
                return
            route, params = self._params()
            if route == "/session":
                self._start_session()
                return
            if not route.startswith("/api/") or not self._has_session():
                self._json(403, {"error": "Open Framework Distiller from its app launcher"})
                return
            if not self._allowed_origin() or self.headers.get("X-Local-Client") != "FrameworkDistiller":
                self._json(403, {"error": "Same-origin local client required"})
                return
            if route == "/api/ingest/upload":
                try:
                    if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/octet-stream":
                        raise ValueError("Binary file upload required")
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > MAX_BYTES:
                        raise ValueError("File is empty or exceeds 25 MB")
                    meta = {key: params.get(key, "") for key in
                            ("title", "source_url", "version", "publication_date", "sha256")}
                    result = app.import_uploaded(self.rfile.read(length), params.get("name", ""), meta)
                    self._json(200, result)
                except (ValueError, IngestError, OSError) as exc:
                    self._json(400, {"error": str(exc)})
                except Exception as exc:
                    self.log_error("upload failed: %s", type(exc).__name__)
                    self._json(500, {"error": "Operation failed; check local server log"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self._json(415, {"error": "JSON required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > MAX_JSON_BODY:
                    raise ValueError("Request body too large")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                if route == "/api/ingest/official":
                    source_id = body.get("source_id", "")
                    if not isinstance(source_id, str) or source_id not in OFFICIAL:
                        raise ValueError("Unknown official source")
                    result = app.import_official(source_id)
                elif route == "/api/ingest/dropin":
                    name = body.get("name", "")
                    if not isinstance(name, str):
                        raise ValueError("Invalid inbox filename")
                    result = app.import_dropin(name, max_batch_bytes=body.get("max_batch_bytes", MAX_BYTES))
                elif route == "/api/ingest/url":
                    result = app.import_url(body)
                elif route == "/api/storage":
                    result = app.set_storage(body.get("data_root"), body.get("inbox_root", ""))
                elif route == "/api/app/quit":
                    if app.platform != "desktop":
                        raise ValueError("Use the Android app controls to leave this screen")
                    self._json(200, {"quitting": True})
                    threading.Thread(target=self.server.shutdown, daemon=True,
                                     name="framework-distiller-quit").start()
                    return
                elif route == "/api/progress":
                    card_id, state = body.get("card_id"), body.get("state")
                    if not isinstance(card_id, str) or not isinstance(state, str):
                        raise ValueError("Invalid progress request")
                    answer = body.get("answer")
                    if answer is not None and not isinstance(answer, str):
                        raise ValueError("Answer must be text")
                    app.store.set_progress(card_id, state, answer)
                    result = {"saved": True}
                elif route == "/api/card/edit":
                    card_id, fields = body.get("card_id"), body.get("fields")
                    if not isinstance(card_id, str) or not isinstance(fields, dict):
                        raise ValueError("Invalid card edit")
                    app.store.edit_card(card_id, fields)
                    result = {"saved": True}
                elif route == "/api/ai/key":
                    result = app.ai.use_key(body.get("provider"), body.get("key"), body.get("model"))
                elif route == "/api/ai/test":
                    result = app.ai.test_connection(body.get("provider"))
                elif route == "/api/ai/forget":
                    result = app.ai.forget(body.get("provider"))
                elif route == "/api/ai/ask":
                    card_id = body.get("card_id")
                    if not isinstance(card_id, str) or len(card_id) > 256:
                        raise ValueError("Invalid card ID")
                    excerpt = app.store.source_excerpt(card_id)
                    result = app.ai.ask(body.get("provider"), excerpt, body.get("question"))
                elif route == "/api/study/preview":
                    document_id = body.get("document_id", "")
                    app.ensure_reader(document_id)
                    provider = body.get("provider", "")
                    if provider not in MODELS:
                        raise ValueError("Choose a supported AI provider")
                    model = app.ai.status()["models"][provider]
                    result = preview(app.store, document_id, body.get("action", ""),
                                     body.get("lens", "general"), provider, model,
                                     body.get("chapter_no"), body.get("page_no"),
                                     body.get("question", ""))
                elif route == "/api/study/generate":
                    result = app.study_generate(body)
                else:
                    self._json(404, {"error": "Not found"})
                    return
                self._json(200, result)
            except (ValueError, AIError, IngestError, OSError, KeyError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                # No stack trace, source text, file content or token in browser output.
                self.log_error("request failed: %s", type(exc).__name__)
                self._json(500, {"error": "Operation failed; check local server log"})

        def log_message(self, format, *args):
            # Only route/status; never log request body or extracted document text.
            route = urlsplit(self.path).path
            if sys.stderr is not None:
                try:
                    sys.stderr.write(f"{self.command} {route}\n")
                except (OSError, ValueError):
                    # A windowless packaged EXE may inherit a stream that closes
                    # after launcher detachment. Logging must not drop requests.
                    pass

    return Handler


def serve(data_root: Path, port: int = 8776, *, inbox_root: Path | None = None,
          settings_file: Path | None = None, platform: str = "desktop") -> ThreadingHTTPServer:
    if port != 0 and not 1024 <= port <= 65535:
        raise ValueError("Port must be zero (ephemeral) or between 1024 and 65535")
    app = LocalApp(data_root, inbox_root=inbox_root,
                   settings_file=settings_file, platform=platform)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.local_app = app
    server.daemon_threads = True
    return server
