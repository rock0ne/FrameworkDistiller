from __future__ import annotations

import hashlib
import http.cookiejar
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from distiller import __version__
from distiller.ai import AIError, MAX_EXCERPT, MODELS, StudyAI, _post
from distiller.catalog import OFFICIAL, allowed_dropin_url
from distiller.distill import CORE_OVERRIDES, make_card
from distiller.ingest import (IngestError, Unit, _ai_subcategory_excerpt, _member, download_official,
                              download_public, extract_units, read_dropin, read_uploaded)
from distiller.server import _as_markdown, LocalApp, make_handler
from distiller.store import Store
from distiller.storage import load_paths
from run import default_data_root


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def authenticated_opener(base: str, token: str):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    launch = urllib.request.Request(base + "/session", data=b"", headers={
        "Origin": base, "X-Local-Client": "FrameworkDistiller",
        "X-Session-Token": token})
    with opener.open(launch, timeout=5) as response:
        assert response.status == 204
    return opener


class SourceTests(unittest.TestCase):
    def test_official_allowlist_has_four_nist_sources(self):
        self.assertEqual(len(OFFICIAL), 4)
        self.assertTrue(all(s.url.startswith("https://") for s in OFFICIAL.values()))
        self.assertTrue(all("nist" in s.url or "usnistgov" in s.url for s in OFFICIAL.values()))

    def test_no_arbitrary_download(self):
        with self.assertRaises(IngestError):
            download_official("https://example.org/evil")

    def test_dropin_url_restrictions(self):
        self.assertTrue(allowed_dropin_url("https://www.fca.org.uk/publications/policy-statements/ps21-3-building-operational-resilience"))
        self.assertFalse(allowed_dropin_url("http://www.fca.org.uk/x"))
        self.assertFalse(allowed_dropin_url("https://evil.example/x"))
        self.assertFalse(allowed_dropin_url("https://www.fca.org.uk.evil.example/x"))
        self.assertFalse(allowed_dropin_url("https://www.fca.org.uk/x?token=secret"))
        self.assertFalse(allowed_dropin_url("https://www.fca.org.uk:invalid/x"))

    def test_dropin_requires_matching_hash_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp)
            data = b"<html><body><h1>Policy</h1><p>Access is reviewed every quarter.</p></body></html>"
            (inbox / "policy.html").write_bytes(data)
            meta = {"title":"Policy", "source_url":"https://www.fca.org.uk/test",
                    "version":"2026", "publication_date":"2026-01-01",
                    "sha256":hashlib.sha256(data).hexdigest()}
            (inbox / "policy.html.source.json").write_text(json.dumps(meta), encoding="utf-8")
            result, record = read_dropin(inbox, "policy.html")
            self.assertEqual(result, data)
            self.assertEqual(record["provenance"], "owner_supplied_hash_checked_not_url_verified")
            self.assertEqual(record["sha256"], meta["sha256"])
            meta["sha256"] = "0" * 64
            (inbox / "policy.html.source.json").write_text(json.dumps(meta), encoding="utf-8")
            with self.assertRaises(IngestError):
                read_dropin(inbox, "policy.html")

    def test_dropin_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(IngestError):
                read_dropin(Path(tmp), "../private.pdf")

    def test_dropin_without_sidecar_is_local_only_and_hash_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp)
            data = b"# Local reading\n\nAn auditor checks the source population and evidence."
            (inbox / "study.md").write_bytes(data)
            saved, record = read_dropin(inbox, "study.md")
            self.assertEqual(saved, data)
            self.assertEqual(record["title"], "study")
            self.assertEqual(record["version"], "Local copy")
            self.assertEqual(record["publication_date"], "Unknown")
            self.assertEqual(record["source_url"], "")
            self.assertEqual(record["provenance"], "owner_uploaded_local_hash_only")
            self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())

    def test_invalid_sidecar_does_not_silently_fall_back_to_local_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp)
            (inbox / "study.md").write_text("Study source", encoding="utf-8")
            (inbox / "study.md.source.json").write_text("not JSON", encoding="utf-8")
            with self.assertRaisesRegex(IngestError, "Invalid source metadata"):
                read_dropin(inbox, "study.md")

    def test_uploaded_file_is_hash_pinned_but_url_not_verified(self):
        data = b"<html><body><p>" + b"A" * 100 + b"</p></body></html>"
        meta = {"title":"Public policy", "source_url":"https://www.fca.org.uk/test",
                "version":"1", "publication_date":"2026-11", "sha256":""}
        saved, record = read_uploaded(data, "policy.html", meta)
        self.assertEqual(saved, data)
        self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(record["provenance"], "owner_uploaded_url_not_byte_verified")
        with self.assertRaises(IngestError):
            read_uploaded(data, "policy.html", {**meta, "sha256":"0" * 64})
        with self.assertRaises(IngestError):
            read_uploaded(data, "policy.html", {**meta, "source_url":"http://127.0.0.1/doc"})

    def test_local_file_does_not_require_external_url(self):
        data = b"Management reviews risk decisions and records the outcome."
        saved, record = read_uploaded(data, "notes.txt", {
            "title":"Private notes", "source_url":"", "version":"Local copy",
            "publication_date":"Unknown", "sha256":""})
        self.assertEqual(saved, data)
        self.assertEqual(record["provenance"], "owner_uploaded_local_hash_only")
        units = extract_units(data, record)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].source_link, "")

    def test_main_reading_formats_extract_text(self):
        phrase = "Account owners review access and record decisions for independent assurance. " * 3
        docx = zip_bytes({"word/document.xml": (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t>{phrase}</w:t></w:r></w:p></w:body></w:document>').encode()})
        odt = zip_bytes({"content.xml": (
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            f'<office:body><text:p>{phrase}</text:p></office:body></office:document-content>').encode()})
        pptx = zip_bytes({"ppt/slides/slide1.xml": (
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:p><a:r><a:t>{phrase}</a:t></a:r></a:p></p:sld>').encode()})
        epub = zip_bytes({
            "META-INF/container.xml": (
                '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>').encode(),
            "OEBPS/book.opf": (
                '<package><manifest><item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
                '</manifest><spine><itemref idref="c1"/></spine></package>').encode(),
            "OEBPS/chapter.xhtml": f"<html><body><p>{phrase}</p></body></html>".encode(),
        })
        samples = {
            "sample.docx": docx, "sample.odt": odt, "sample.pptx": pptx,
            "sample.epub": epub,
            "sample.rtf": ("{\\rtf1\\ansi " + phrase + "}").encode(),
            "sample.md": ("# Access review\n\n" + phrase).encode(),
            "sample.txt": phrase.encode(),
            "sample.html": f"<html><body><p>{phrase}</p></body></html>".encode(),
        }
        for name, data in samples.items():
            with self.subTest(name=name):
                _, record = read_uploaded(data, name, {"title":"Sample", "source_url":"",
                    "version":"Local copy", "publication_date":"Unknown", "sha256":""})
                units = extract_units(data, record)
                self.assertTrue(units)
                self.assertIn("Account owners review access", " ".join(unit.text for unit in units))

    def test_archive_bound_and_unsupported_file_are_refused(self):
        oversized = zip_bytes({"word/document.xml": b"A" * (10 * 1024 * 1024 + 1)})
        _, record = read_uploaded(oversized, "large.docx", {"title":"Large", "source_url":"",
            "version":"Local copy", "publication_date":"Unknown", "sha256":""})
        with self.assertRaises(IngestError):
            extract_units(oversized, record)
        with self.assertRaises(IngestError):
            read_uploaded(b"not a document", "old.doc", {"title":"Old", "source_url":"",
                "version":"Local copy", "publication_date":"Unknown", "sha256":""})

    def test_archive_stream_limit_ignores_false_small_header(self):
        class FalseHeaderArchive:
            _fd_actual_bytes = 0
            def open(self, name):
                return io.BytesIO(b"X" * (10 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(IngestError, "safe extraction bound"):
            _member(FalseHeaderArchive(), "word/document.xml")

    def test_archive_actual_cumulative_limit(self):
        class FalseHeaderArchive:
            _fd_actual_bytes = 36 * 1024 * 1024
            def open(self, name):
                return io.BytesIO(b"Y" * (4 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(IngestError, "safe extraction bound"):
            _member(FalseHeaderArchive(), "word/document.xml")

    def test_office_xml_rejects_external_entity_and_dtd(self):
        malicious = zip_bytes({"word/document.xml": (
            '<!DOCTYPE doc [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>').encode()})
        _, record = read_uploaded(malicious, "malicious.docx", {"title":"Malicious",
            "source_url":"", "version":"Local copy", "publication_date":"Unknown", "sha256":""})
        with self.assertRaisesRegex(IngestError, "XML is unreadable"):
            extract_units(malicious, record)

    def test_long_text_is_chunked_without_silent_cutoff(self):
        data = ("A unique audit sentence about risk and evidence. " * 150).encode()
        _, record = read_uploaded(data, "long.txt", {"title":"Long", "source_url":"",
            "version":"Local copy", "publication_date":"Unknown", "sha256":""})
        units = extract_units(data, record)
        self.assertGreater(len(units), 1)
        self.assertEqual(" ".join(unit.text for unit in units), data.decode().strip())

    def test_public_link_only_fetches_official_https_and_checks_hash(self):
        data = b"<html><body><p>" + b"B" * 100 + b"</p></body></html>"
        class FakeResponse:
            url = "https://www.fca.org.uk/public/doc.html"
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return data[:limit]
        class FakeOpener:
            def open(self, request, timeout): return FakeResponse()
        with patch("distiller.ingest.urllib.request.build_opener", return_value=FakeOpener()):
            saved, record = download_public(FakeResponse.url, "Public policy", "1", "2026-11")
        self.assertEqual(saved, data)
        self.assertEqual(record["provenance"], "downloaded_from_approved_public_url")
        with self.assertRaises(IngestError):
            download_public("https://127.0.0.1/doc.pdf", "Private", "1", "2026-11")
        with self.assertRaises(IngestError):
            download_public("https://www.fca.org.uk/doc?token=secret", "Private", "1", "2026-11")

    def test_known_nist_link_uses_structured_parser_id(self):
        url = OFFICIAL["nist-ai-rmf-1"].url
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return b"%PDF-1.7 fake for routing test"
            @property
            def url(self): return url
        class FakeOpener:
            def open(self, request, timeout): return FakeResponse()
        with patch("distiller.ingest.urllib.request.build_opener", return_value=FakeOpener()):
            _, record = download_public(url, "User entered title", "unknown", "unknown")
        self.assertEqual(record["source_id"], "nist-ai-rmf-1")
        self.assertEqual(record["title"], "NIST AI RMF 1.0")

    def test_oscal_extracts_csf_subcategories(self):
        data = json.dumps({"catalog":{"groups":[{"title":"GOVERN","controls":[
            {"id":"GV.SC", "controls":[{"id":"GV.SC-01", "parts":[
                {"name":"statement","prose":"A supply chain risk programme is established."}]}]}]}]}}).encode()
        record = {"format":"oscal-json", "source_id":"nist-csf-2",
                  "authority_url":"https://csrc.nist.gov/projects/cybersecurity-framework/filters"}
        units = extract_units(data, record)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].ref, "GV.SC-01")
        self.assertIn("supply chain", units[0].text)

    def test_withdrawn_csf_entries_not_carded(self):
        data = json.dumps({"catalog":{"groups":[{"title":"IDENTIFY","controls":[
            {"id":"ID.BE", "controls":[
                {"id":"ID.BE-01", "props":[{"name":"status","value":"withdrawn"}],
                 "parts":[{"name":"statement","prose":"Old outcome."}]},
                {"id":"ID.BE-02", "parts":[{"name":"statement","prose":"Current outcome."}]}]}]}]}}).encode()
        record = {"format":"oscal-json", "source_id":"nist-csf-2",
                  "authority_url":"https://csrc.nist.gov/projects/cybersecurity-framework/filters"}
        units = extract_units(data, record)
        self.assertEqual([u.ref for u in units], ["ID.BE-02"])

    def test_html_extracts_text_not_script(self):
        record = {"format":"html", "source_url":"https://www.fca.org.uk/test"}
        data = b"<script>ignore malicious script text</script><p>" + b"A" * 100 + b"</p>"
        units = extract_units(data, record)
        self.assertEqual(len(units), 1)
        self.assertNotIn("malicious", units[0].text)

    def test_ai_subcategory_stops_before_next_category(self):
        excerpt = _ai_subcategory_excerpt(
            "Decommissioning does not increase risks. GOVERN 2: Accountability structures are in place."
        )
        self.assertEqual(excerpt, "Decommissioning does not increase risks.")


class CardTests(unittest.TestCase):
    def setUp(self):
        self.unit = Unit("AC-2", "Account Management", "Manage information system accounts and review their privileges.",
                         None, "https://csrc.nist.gov/x", "Access Control")

    def test_curated_control_is_plain_and_cited(self):
        card = make_card(self.unit, "nist-sp-800-53-r5")
        self.assertEqual(card.core_rule, CORE_OVERRIDES["AC-2"])
        self.assertEqual(card.theme, "IAM/PAM")
        self.assertEqual(card.citation, self.unit.source_link)
        self.assertIn("TOD", card.audit_test)
        self.assertIn("TOE", card.audit_test)

    def test_unselected_80053_control_is_not_carded(self):
        unit = Unit("AC-99", "Other", "Some control statement.", None, self.unit.source_link, "Access")
        self.assertIsNone(make_card(unit, "nist-sp-800-53-r5"))

    def test_governance_is_not_mislabeled_as_backup_or_soc(self):
        for ref in ("GV.OC-01", "GV.OV-01", "GV.PO-01"):
            unit = Unit(ref, "Governance", "Management reviews the risk strategy.", None,
                        self.unit.source_link, "GOVERN")
            self.assertEqual(make_card(unit, "nist-csf-2").theme, "Governance & Assurance")
        unit = Unit("IR-4", "Incident Handling", "Incidents are handled.", None,
                    self.unit.source_link, "Incident Response")
        self.assertEqual(make_card(unit, "nist-sp-800-53-r5").theme, "Logging & SOC")

    def test_source_limit_and_summary_not_three_sentences(self):
        unit = Unit("GV.SC-01", "Supplier", "First short sentence. Second short sentence. Third sentence.",
                    None, self.unit.source_link, "GOVERN")
        card = make_card(unit, "nist-csf-2")
        self.assertNotIn("Third sentence", card.core_rule)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "study.sqlite3")
        self.record = {"source_id":"nist-sp-800-53-r5", "title":"NIST SP 800-53 Rev. 5",
                       "source_url":"https://raw.githubusercontent.com/usnistgov/oscal-content/main/x",
                       "authority_url":"https://csrc.nist.gov/x", "version":"5.2.0",
                       "publication_date":"2025-08-27", "sha256":"a"*64,
                       "format":"oscal-json", "provenance":"downloaded_from_approved_nist_url"}
        self.unit = Unit("AC-2", "Account Management", "Manage accounts and review privileges.",
                         None, "https://csrc.nist.gov/x", "Access Control")

    def tearDown(self):
        self.temp.cleanup()

    def test_import_progress_search_export_and_edit(self):
        result = self.store.import_document(self.record, [self.unit], "a.json")
        self.assertEqual(result["cards"], 1)
        self.assertFalse(result["already_imported"])
        self.assertTrue(self.store.import_document(self.record, [self.unit], "a.json")["already_imported"])
        card = self.store.cards()[0]
        self.assertEqual(card["state"], "unread")
        self.assertEqual(len(self.store.cards(query="AC-2")), 1)
        self.store.set_progress(card["id"], "read", "I would reconcile all accounts.")
        self.assertEqual(self.store.cards(state="read")[0]["answer"], "I would reconcile all accounts.")
        self.store.edit_card(card["id"], {"core_rule":"Review all accounts.","risk":"Excess access.",
                                          "audit_test":"TOD and TOE.","trap":"No population.",
                                          "challenge":"What would you ask?"})
        edited = self.store.cards()[0]
        self.assertEqual(edited["reviewed"], 1)
        self.assertIn("Review all accounts", _as_markdown([edited]))
        self.assertIn(self.record["sha256"], _as_markdown([edited]))

    def test_invalid_progress_and_edits_refused(self):
        self.store.import_document(self.record, [self.unit], "a.json")
        card_id = self.store.cards()[0]["id"]
        with self.assertRaises(ValueError):
            self.store.set_progress(card_id, "qualified")
        with self.assertRaises(ValueError):
            self.store.edit_card(card_id, {"core_rule":"Three. Sentences. Here.",
                                           "risk":"x", "audit_test":"x", "trap":"x", "challenge":"x"})

    def test_local_app_creates_only_local_data_locations(self):
        app = LocalApp(self.root / "data")
        self.assertTrue(app.inbox.is_dir())
        self.assertTrue(app.sources.is_dir())
        self.assertTrue(app.store.path.is_file())

    def test_storage_and_inbox_choices_persist_without_moving_old_library(self):
        old = self.root / "old-library"
        new = self.root / "new-library"
        inbox = self.root / "my-imports"
        settings = self.root / "config" / "settings.json"
        inbox.mkdir()
        (inbox / "reading.md").write_text("# Reading\n\nA source excerpt.", encoding="utf-8")
        app = LocalApp(old, settings_file=settings)
        app.store.import_document(self.record, [self.unit], "a.json")
        choice = app.set_storage(str(new), str(inbox))
        self.assertEqual(choice["data_root"], str(new.resolve()))
        self.assertTrue(choice["inbox_custom"])
        self.assertEqual(app.store.cards(), [])
        self.assertEqual(app.inbox_names()[0]["name"], "reading.md")
        self.assertFalse(app.inbox_names()[0]["has_sidecar"])
        self.assertEqual(load_paths(settings, old), (new.resolve(), inbox.resolve()))
        self.assertEqual(Store(old / "study.sqlite3").cards()[0]["ref"], "AC-2")
        with self.assertRaisesRegex(ValueError, "absolute"):
            app.set_storage("relative-folder", "")
        self.assertEqual(load_paths(settings, old), (new.resolve(), inbox.resolve()))
        app.set_storage(str(old), "")
        self.assertEqual(len(app.store.cards()), 1)
        self.assertFalse(app.storage_status()["inbox_custom"])

    def test_android_uses_private_storage_and_system_picker(self):
        app = LocalApp(self.root / "android", platform="android")
        self.assertFalse(app.storage_status()["editable"])
        with self.assertRaisesRegex(ValueError, "system file picker"):
            app.set_storage(str(self.root / "elsewhere"), "")

    def test_missing_selected_folder_is_not_silently_recreated(self):
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"version":1,
            "data_root":str(self.root / "missing-library"), "inbox_root":""}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Storage settings need correction"):
            load_paths(settings, self.root / "default")
        self.assertFalse((self.root / "missing-library").exists())


class AITests(unittest.TestCase):
    def test_session_key_off_default_forget_and_never_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "data")
            self.assertFalse(app.ai.status()["enabled"])
            app.ai.use_key("openai", "sk-test-session-key")
            self.assertTrue(app.ai.status()["enabled"])
            self.assertNotIn("sk-test-session-key", json.dumps(app.ai.status()))
            self.assertNotIn(b"sk-test-session-key", app.store.path.read_bytes())
            self.assertFalse(LocalApp(Path(tmp) / "data").ai.status()["enabled"])
            self.assertFalse(app.ai.forget("openai")["enabled"])
            with self.assertRaises(AIError):
                app.ai.ask("openai", "source", "Explain this")
            with self.assertRaises(AIError):
                app.ai.use_key("openai", "has a space")

    def test_three_provider_requests_are_fixed_and_send_only_excerpt_question(self):
        for provider in MODELS:
            calls = []
            def fake_post(url, headers, body):
                calls.append((url, headers, body))
                if provider == "openai":
                    return {"output":[{"content":[{"type":"output_text","text":"Audit answer"}]}],
                            "usage":{"input_tokens":20,"output_tokens":4}}
                if provider == "anthropic":
                    return {"content":[{"type":"text","text":"Audit answer"}],
                            "usage":{"input_tokens":20,"output_tokens":4}}
                return {"candidates":[{"content":{"parts":[{"text":"Audit answer"}]}}],
                        "usageMetadata":{"promptTokenCount":20,"candidatesTokenCount":4}}
            ai = StudyAI(fake_post)
            ai.use_key(provider, "test-provider-key")
            reply = ai.ask(provider, "SOURCE " + "A" * (MAX_EXCERPT + 100), "What evidence?")
            self.assertEqual(reply["answer"], "Audit answer")
            self.assertEqual(reply["input_tokens"], 20)
            self.assertEqual(len(calls), 1)
            url, headers, body = calls[0]
            self.assertEqual(url, MODELS[provider][2])
            self.assertTrue(url.startswith("https://"))
            payload = json.dumps(body)
            self.assertIn("What evidence?", payload)
            self.assertIn("SOURCE ", payload)
            self.assertNotIn("PRIVATE NOTES", payload)
            self.assertNotIn("test-provider-key", payload)
            self.assertNotIn("A" * 4100, payload)
            self.assertIn("test-provider-key", json.dumps(headers))

    def test_no_key_no_call_and_invalid_inputs(self):
        calls = []
        ai = StudyAI(lambda *parts: calls.append(parts))
        with self.assertRaises(AIError):
            ai.ask("openai", "source", "Question")
        ai.use_key("openai", "test-provider-key")
        for provider, question in (("unknown", "Q"), ("openai", ""), ("openai", "Q" * 501)):
            with self.assertRaises(AIError):
                ai.ask(provider, "source", question)
        self.assertEqual(calls, [])

    def test_model_override_is_validated_session_only_and_uses_fixed_provider_host(self):
        seen = []
        ai = StudyAI(lambda url, headers, body: (seen.append((url, body)) or
            {"candidates":[{"content":{"parts":[{"text":"Audit answer"}]}}]}))
        for bad in ("https://evil.example", "../../other", "model?key=secret", "bad model", "X" * 81):
            with self.subTest(bad=bad), self.assertRaisesRegex(AIError, "Model ID"):
                ai.use_key("gemini", "test-provider-key", bad)
        self.assertFalse(ai.status()["enabled"])
        ai.use_key("gemini", "test-provider-key", "gemini-custom-1")
        self.assertEqual(ai.status()["models"]["gemini"], "gemini-custom-1")
        self.assertEqual(ai.ask("gemini", "excerpt", "Question")["model"], "gemini-custom-1")
        self.assertEqual(seen[0][0],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-custom-1:generateContent")
        ai.forget("gemini")
        self.assertEqual(ai.status()["models"]["gemini"], MODELS["gemini"][1])

    def test_provider_redirect_does_not_forward_key(self):
        seen = []
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/second")
                self.end_headers()
            def do_GET(self):
                seen.append("redirect-followed")
                self.send_response(200); self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with self.assertRaises(AIError):
                _post(f"http://127.0.0.1:{server.server_port}/first",
                      {"Authorization":"Bearer fake-test-key"}, {"input":"test"})
            self.assertEqual(seen, ["/first"])
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=5)


class ServerTests(unittest.TestCase):
    def test_frozen_default_data_uses_local_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("run.sys.frozen", True, create=True), patch.dict("run.os.environ", {"LOCALAPPDATA": tmp}):
                self.assertEqual(default_data_root(), Path(tmp) / "FrameworkDistiller")

    def test_http_storage_change_and_inbox_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox = root / "imports"
            inbox.mkdir()
            (inbox / "local.md").write_text("# Audit\n\nReview evidence.", encoding="utf-8")
            app = LocalApp(root / "default", settings_file=root / "settings.json")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            opener = authenticated_opener(base, app.session_token)
            try:
                request = urllib.request.Request(base + "/api/storage", data=json.dumps({
                    "data_root":str(root / "chosen"), "inbox_root":str(inbox)}).encode(), headers={
                    "Content-Type":"application/json", "Origin":base,
                    "X-Local-Client":"FrameworkDistiller"})
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["inbox_root"], str(inbox.resolve()))
                with opener.open(base + "/api/status", timeout=5) as response:
                    payload = json.load(response)
                    self.assertEqual(payload["storage"]["data_root"], str((root / "chosen").resolve()))
                    self.assertEqual(payload["inbox"][0]["name"], "local.md")
                with opener.open(base + "/api/storage", timeout=5) as response:
                    self.assertTrue(json.load(response)["editable"])
            finally:
                server.shutdown(); server.server_close(); worker.join(timeout=5)

    def test_desktop_quit_stops_local_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "data")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            opener = authenticated_opener(base, app.session_token)
            try:
                request = urllib.request.Request(base + "/api/app/quit", data=b"{}", headers={
                    "Content-Type":"application/json", "Origin":base,
                    "X-Local-Client":"FrameworkDistiller"})
                with opener.open(request, timeout=5) as response:
                    self.assertTrue(json.load(response)["quitting"])
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
            finally:
                server.shutdown(); server.server_close()

    def test_local_page_api_export_and_cross_origin_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "data")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"Framework Distiller", response.read())
                for route in ("/api/status", "/api/cards", "/api/export", "/api/ai/status"):
                    with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(base + route, timeout=5)
                    self.assertEqual(denied.exception.code, 403)
                    denied.exception.close()
                fake_launch = urllib.request.Request(base + "/session", data=b"", headers={
                    "Origin":base, "X-Local-Client":"FrameworkDistiller", "X-Session-Token":"wrong"})
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(fake_launch, timeout=5)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                opener = authenticated_opener(base, app.session_token)
                with opener.open(base + "/api/status", timeout=5) as response:
                    payload = json.load(response)
                    self.assertEqual(payload["mode"], "local-with-optional-byok")
                    self.assertEqual(payload["version"], __version__)
                    self.assertFalse(payload["model_calls"])
                    self.assertEqual(len(payload["official"]), 4)
                with opener.open(base + "/api/export", timeout=5) as response:
                    self.assertIn(b"Framework study notes", response.read())
                meta = {"name":"policy.html", "title":"Public policy",
                        "source_url":"https://www.fca.org.uk/test", "version":"1",
                        "publication_date":"2026-11", "sha256":""}
                uploaded = urllib.request.Request(
                    base + "/api/ingest/upload?" + urllib.parse.urlencode(meta),
                    data=b"<html><body><p>" + b"A" * 100 + b"</p></body></html>",
                    headers={"Content-Type":"application/octet-stream", "Origin":base,
                             "X-Local-Client":"FrameworkDistiller"})
                with opener.open(uploaded, timeout=5) as response:
                    self.assertEqual(json.load(response)["cards"], 1)
                with opener.open(base + "/api/cards", timeout=5) as response:
                    self.assertEqual(json.load(response)["count"], 1)
                local_link = urllib.request.Request(base + "/api/ingest/url", data=json.dumps({
                    "source_url":"https://127.0.0.1/private.pdf", "title":"Private",
                    "version":"1", "publication_date":"2026-11"}).encode(), headers={
                    "Content-Type":"application/json", "Origin":base,
                    "X-Local-Client":"FrameworkDistiller"})
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    opener.open(local_link, timeout=5)
                self.assertEqual(blocked.exception.code, 400)
                blocked.exception.close()
                bad_host = urllib.request.Request(base + "/api/status", headers={"Host":"evil.example"})
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    opener.open(bad_host, timeout=5)
                self.assertEqual(blocked.exception.code, 403)
                blocked.exception.close()
                wrong_origin = urllib.request.Request(base + "/api/progress", data=b"{}", headers={
                    "Content-Type":"application/json", "Origin":"https://evil.example",
                    "X-Local-Client":"FrameworkDistiller"})
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    opener.open(wrong_origin, timeout=5)
                self.assertEqual(blocked.exception.code, 403)
                blocked.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_ai_http_flow_never_sends_private_answer_or_exposes_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "data")
            seen = []
            def fake_post(url, headers, body):
                seen.append((url, headers, body))
                return {"output":[{"content":[{"type":"output_text","text":"Check the evidence."}]}]}
            app.ai = StudyAI(fake_post)
            app.import_uploaded(b"<html><body><p>Review access quarterly and retain approvals.</p></body></html>",
                                "policy.html", {"title":"Policy", "source_url":"", "version":"1",
                                                "publication_date":"2026-11", "sha256":""})
            card_id = app.store.cards()[0]["id"]
            app.store.set_progress(card_id, "read", "PRIVATE NOTES DO NOT SEND")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            blocked_key = urllib.request.Request(base + "/api/ai/key",
                data=b'{"provider":"openai","key":"sk-session-only-test"}', headers={
                "Content-Type":"application/json", "Origin":base,
                "X-Local-Client":"FrameworkDistiller"})
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(blocked_key, timeout=5)
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()
            opener = authenticated_opener(base, app.session_token)
            def post(route, body):
                request = urllib.request.Request(base + route, data=json.dumps(body).encode(), headers={
                    "Content-Type":"application/json", "Origin":base,
                    "X-Local-Client":"FrameworkDistiller"})
                with opener.open(request, timeout=5) as response:
                    return json.load(response)
            try:
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    post("/api/ai/ask", {"provider":"openai", "card_id":card_id, "question":"Explain"})
                self.assertEqual(missing.exception.code, 400)
                missing.exception.close()
                self.assertEqual(seen, [])
                status = post("/api/ai/key", {"provider":"openai", "key":"sk-session-only-test"})
                self.assertEqual(status["configured"], ["openai"])
                self.assertNotIn("sk-session-only-test", json.dumps(status))
                with opener.open(base + "/api/ai/status", timeout=5) as response:
                    self.assertNotIn("sk-session-only-test", response.read().decode())
                reply = post("/api/ai/ask", {"provider":"openai", "card_id":card_id,
                                             "question":"What audit evidence?"})
                self.assertEqual(reply["answer"], "Check the evidence.")
                self.assertEqual(len(seen), 1)
                self.assertNotIn("PRIVATE NOTES DO NOT SEND", json.dumps(seen[0]))
                self.assertIn("Review access quarterly", json.dumps(seen[0][2]))
                with opener.open(base + "/api/export", timeout=5) as response:
                    self.assertNotIn(b"sk-session-only-test", response.read())
                self.assertFalse(post("/api/ai/forget", {"provider":"openai"})["enabled"])
                self.assertNotIn(b"sk-session-only-test", app.store.path.read_bytes())
            finally:
                server.shutdown(); server.server_close(); worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
