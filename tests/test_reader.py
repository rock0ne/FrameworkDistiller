"""Reader-first and BYOK contract tests; all transports are offline fakes."""

from __future__ import annotations

import io
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from distiller.ai import MODELS, StudyAI
from distiller.reader import ReadingChapter, ReadingDocument, ReadingPage, extract_reading
from distiller.server import LocalApp, make_handler
from distiller.study import artifact_markdown, preview, parse_ai_answer


def archive_bytes(entries: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in entries.items():
            archive.writestr(name, text.encode("utf-8"))
    return output.getvalue()


class ReaderTests(unittest.TestCase):
    def test_help_is_embedded_and_accessible_from_header(self):
        html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="open-faq"', html)
        self.assertIn('<dialog class="faq-panel" id="faq"', html)
        self.assertIn('id="close-faq"', html)
        self.assertIn('id="faq"', html)
        self.assertIn('aria-labelledby="faq-title"', html)
        self.assertIn("How do I pull a document from a web link?", html)
        self.assertIn("header's <strong>Install as app</strong> button", html)
        self.assertIn('id="install-help"', html)
        self.assertIn("Install-FrameworkDistiller.ps1", html)
        for action in ("file_points", "chapter_points", "page_points", "file_map",
                       "chapter_map", "page_map"):
            self.assertIn(f'data-study-action="{action}"', html)
        self.assertIn('id="document-outline"', html)
        self.assertIn('id="ask-question"', html)

    def test_folder_choice_and_reviewed_email_are_visible_but_sync_is_not_claimed(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        android = (root / "android" / "app" / "src" / "main" / "java" / "uk" /
                   "vijaukatt" / "frameworkdistiller" / "MainActivity.java").read_text(encoding="utf-8")
        self.assertIn('id="storage-settings" open', html)
        self.assertIn('id="inbox-setting" type="text"', html)
        self.assertIn('id="email-share"', html)
        self.assertIn('id="share-review" hidden', html)
        self.assertIn('id="share-open-email"', html)
        self.assertIn('Current libraries are device-local', html)
        self.assertIn('Intent.ACTION_SENDTO, url', android)

    def test_android_folder_url_uses_min_sdk_compatible_encoder(self):
        root = Path(__file__).resolve().parents[1]
        android = (root / "android" / "app" / "src" / "main" / "java" / "uk" /
                   "vijaukatt" / "frameworkdistiller" / "MainActivity.java").read_text(encoding="utf-8")
        self.assertIn('URLEncoder.encode(value, "UTF-8")', android)
        self.assertNotIn('URLEncoder.encode(value, StandardCharsets.UTF_8)', android)

    def test_private_vidatta_brief_is_not_packaged(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "build_windows.py").read_text(encoding="utf-8")
        self.assertNotIn('bundle.write(root / "VIDATTA-SYNC-PLAN.md"', script)
        self.assertNotIn('root / "VIDATTA-SYNC-PLAN.md"', script)

    def test_markdown_keeps_heading_order_and_segments(self):
        data = ("# Contents\n\nChapter One .......................... 1\n"
                "# First lesson\n\n" + "A source-based explanation connects evidence and decisions. " * 110 +
                "\n# Second lesson\n\nThe next section follows the first in reading order.").encode()
        reading = extract_reading(data, {"format": "md", "source_url": ""})
        self.assertEqual([chapter.title for chapter in reading.chapters],
                         ["Contents", "First lesson", "Second lesson"])
        self.assertTrue(reading.chapters[0].front_matter)
        self.assertFalse(reading.chapters[1].front_matter)
        self.assertGreater(len(reading.chapters[1].pages), 1)
        self.assertEqual([page.number for chapter in reading.chapters for page in chapter.pages],
                         list(range(1, sum(len(ch.pages) for ch in reading.chapters) + 1)))

    def test_epub_uses_spine_not_archive_order(self):
        data = archive_bytes({
            "META-INF/container.xml": '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>',
            "OEBPS/book.opf": '<package><manifest><item id="a" href="one.xhtml"/><item id="b" href="two.xhtml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>',
            "OEBPS/one.xhtml": '<html><body><h1>First in archive</h1><p>One follows two by spine order.</p></body></html>',
            "OEBPS/two.xhtml": '<html><body><h1>First in spine</h1><p>This is the first reading section.</p></body></html>',
        })
        reading = extract_reading(data, {"format": "epub", "source_url": ""})
        self.assertEqual([chapter.title for chapter in reading.chapters],
                         ["First in spine", "First in archive"])

    def test_docx_uses_headings(self):
        ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        def para(text, style=""):
            prop = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
            return f'<w:p>{prop}<w:r><w:t>{text}</w:t></w:r></w:p>'
        data = archive_bytes({"word/document.xml":
            f'<w:document xmlns:w="{ns}"><w:body>' +
            para("A title", "Title") + para("Opening text with useful context.") +
            para("First chapter", "Heading1") + para("First chapter evidence.") +
            para("Second chapter", "Heading1") + para("Second chapter follows.") +
            '</w:body></w:document>'})
        reading = extract_reading(data, {"format": "docx", "source_url": ""})
        self.assertEqual([chapter.title for chapter in reading.chapters],
                         ["A title", "First chapter", "Second chapter"])

    def test_reader_and_offline_artifact_are_pinned_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "library")
            data = ("# Access\n\nThe control owner reviews access against the approved list each quarter. "
                    "An auditor samples approvals and checks the completeness of the population.\n\n"
                    "# Change\n\nThe operator records each change and its approver before deployment. "
                    "The reviewer compares the change ticket with deployment evidence.").encode()
            record = app.import_uploaded(data, "lesson.md", {
                "title": "A lesson", "version": "1", "publication_date": "2026-11-25"})
            doc_id = record["document_id"]
            app.ensure_reader(doc_id)
            structure = app.store.reader_structure(doc_id)
            self.assertEqual(structure["structure_kind"], "markdown_headings")
            self.assertEqual(len(structure["chapters"]), 2)
            self.assertIn("Access", structure["chapters"][0]["title"])
            request = {"document_id": doc_id, "action": "file_points", "lens": "general", "mode": "offline"}
            first = app.study_generate(request)
            second = app.study_generate(request)
            self.assertTrue(second["cached"])
            self.assertEqual(first["cache_key"], second["cache_key"])
            self.assertTrue(first["body"]["items"])
            self.assertTrue(all(item["source_ref"] for item in first["body"]["items"]))
            self.assertEqual(app.store.reader_pages(doc_id, page_no=1)[0]["chapter_no"], 1)
            self.assertNotIn("api_key", json.dumps(first))

    def test_six_source_scoped_actions_and_whole_file_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "library")
            data = ("# First\n\n" + "The first section explains the system's boundary and intended purpose. " * 8 +
                    "\n# Second\n\n" + "The second section shows evidence collection and an operational example. " * 8 +
                    "\n# Third\n\n" + "The third section describes a failure mode and a decision limit. " * 8).encode()
            record = app.import_uploaded(data, "lesson.md", {
                "title": "Lesson", "version": "1", "publication_date": "2026-11-25"})
            doc_id = record["document_id"]
            app.ensure_reader(doc_id)
            common = {"document_id": doc_id, "lens": "general", "mode": "offline",
                      "chapter_no": 1, "page_no": 1}
            for action in ("file_points", "chapter_points", "page_points",
                           "file_map", "chapter_map", "page_map"):
                with self.subTest(action=action):
                    result = app.study_generate({**common, "action": action})
                    self.assertTrue(result["body"]["items"] or result["body"]["map"])
            whole = app.study_generate({**common, "action": "file_map"})["body"]
            self.assertEqual([node["label"].split(". ", 1)[1] for node in whole["map"]["children"]],
                             ["First", "Second", "Third"])
            self.assertEqual([node["page_no"] for node in whole["map"]["children"]], [1, 2, 3])
            self.assertIn("structural map of 3 of 3", whole["disclosure"])

    def test_file_question_uses_diverse_relevant_pages_and_exact_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "library")
            chapters = []
            for index in range(1, 21):
                text = "The control owner records evidence and explains a decision. " * 10
                if index == 8:
                    text += "Quarantine isolates the affected asset pending a documented review. " * 4
                chapters.append(f"# Chapter {index}\n\n{text}")
            record = app.import_uploaded("\n".join(chapters).encode(), "book.md", {
                "title": "Book", "version": "1", "publication_date": "2026-11-25"})
            doc_id = record["document_id"]
            app.ensure_reader(doc_id)
            calls = []
            def fake_post(url, headers, body):
                calls.append(body)
                return {"output": [{"content": [{"type": "output_text", "text":
                    '{"title":"Answer","items":[{"text":"The source gives a bounded example.","source_ref":"chapter-8-segment-1"}]}'}]}]}
            app.ai = StudyAI(fake_post)
            app.ai.use_key("openai", "fake-key-only-not-real")
            question = "How does quarantine work?"
            first = preview(app.store, doc_id, "ask_file", "general", "openai",
                            MODELS["openai"][1], question=question)
            other = preview(app.store, doc_id, "ask_file", "general", "openai",
                            MODELS["openai"][1], question="What is the approval path?")
            self.assertNotEqual(first["approval_hash"], other["approval_hash"])
            self.assertIn("Quarantine isolates", first["text"])
            self.assertIn("chapter-8-segment-1", first["source_refs"])
            self.assertGreater(first["omitted_pages"], 0)
            self.assertIn("How does quarantine work?", first["instruction"])
            request = {"document_id": doc_id, "action": "ask_file", "lens": "general",
                       "mode": "ai", "provider": "openai", "question": question}
            with self.assertRaisesRegex(ValueError, "Review the exact"):
                app.study_generate({**request, "approval_hash": other["approval_hash"]})
            self.assertFalse(calls)
            result = app.study_generate({**request, "approval_hash": first["approval_hash"]})
            self.assertEqual(result["body"]["question"], question)
            self.assertEqual(result["body"]["items"][0]["source_ref"], "chapter-8-segment-1")
            with self.assertRaisesRegex(ValueError, "Ask AI needs"):
                app.study_generate({**request, "mode": "offline"})

    def test_stale_approval_for_page_chapter_lens_and_question_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "library")
            record = app.import_uploaded(
                b"# First\n\nThe first owner checks evidence before approving a change.\n"
                b"# Second\n\nThe second owner records a separate review decision.",
                "two.md", {"title": "Two chapters", "version": "1", "publication_date": "2026-11-25"})
            doc_id = record["document_id"]
            app.ensure_reader(doc_id)
            calls = []
            def fake_post(url, headers, body):
                calls.append(body)
                return {"output": [{"content": [{"type": "output_text", "text":
                    '{"title":"Answer","items":[{"text":"Grounded point.","source_ref":"chapter-1-segment-1"}]}'}]}]}
            app.ai = StudyAI(fake_post)
            app.ai.use_key("openai", "fake-key-only-not-real")
            model = MODELS["openai"][1]
            page_one = preview(app.store, doc_id, "page_points", "general", "openai", model, 1, 1)
            chapter_one = preview(app.store, doc_id, "chapter_points", "general", "openai", model, 1, 1)
            question_one = preview(app.store, doc_id, "ask_page", "general", "openai", model,
                                   1, 1, "What does the first owner check?")
            base = {"document_id": doc_id, "lens": "general", "mode": "ai", "provider": "openai"}
            stale = [
                {**base, "action": "page_points", "chapter_no": 2, "page_no": 2,
                 "approval_hash": page_one["approval_hash"]},
                {**base, "action": "chapter_points", "chapter_no": 2, "page_no": 2,
                 "approval_hash": chapter_one["approval_hash"]},
                {**base, "action": "page_points", "chapter_no": 1, "page_no": 1,
                 "lens": "audit", "approval_hash": page_one["approval_hash"]},
                {**base, "action": "ask_page", "chapter_no": 1, "page_no": 1,
                 "question": "What does the second owner record?",
                 "approval_hash": question_one["approval_hash"]},
            ]
            for request in stale:
                with self.subTest(request=request):
                    with self.assertRaisesRegex(ValueError, "Review the exact"):
                        app.study_generate(request)
            self.assertEqual(calls, [])

    def test_long_single_page_discloses_excerpt_in_preview_result_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "library")
            source = ("A material control relies on a complete population and independent review. " * 145
                      + "TAIL SENTINEL: this control has a material exception after the excerpt.")
            record = app.import_uploaded(source.encode(), "long.txt", {
                "title": "Long page", "version": "1", "publication_date": "2026-11-25"})
            doc_id = record["document_id"]
            page = ReadingPage(1, 1, "Page 1", "p.1", "", source)
            app.store.save_reader(doc_id, ReadingDocument("synthetic_pdf_page", (
                ReadingChapter(1, "One page", (page,)),)))
            calls = []
            def fake_post(url, headers, body):
                calls.append(body)
                return {"output": [{"content": [{"type": "output_text", "text":
                    '{"title":"Bounded answer","items":[{"text":"The source discusses review.","source_ref":"p.1"}]}'}]}]}
            app.ai = StudyAI(fake_post)
            app.ai.use_key("openai", "fake-key-only-not-real")
            exact = preview(app.store, doc_id, "page_points", "general", "openai",
                            MODELS["openai"][1], 1, 1)
            self.assertEqual(exact["sampled_pages"], 1)
            self.assertEqual(exact["omitted_pages"], 0)
            self.assertEqual(exact["coverage"]["source_characters"], len(source))
            self.assertLess(exact["coverage"]["sent_source_characters"], len(source))
            self.assertEqual(exact["coverage"]["excerpt_only_refs"], ["p.1"])
            self.assertIn("EXCERPT ONLY", exact["text"])
            self.assertIn("EXCERPT ONLY", exact["coverage_disclosure"])
            self.assertNotIn("TAIL SENTINEL", exact["text"])
            self.assertLessEqual(exact["characters"], 4000)
            request = {"document_id": doc_id, "action": "page_points", "lens": "general",
                       "mode": "ai", "provider": "openai", "chapter_no": 1, "page_no": 1,
                       "approval_hash": exact["approval_hash"]}
            result = app.study_generate(request)
            self.assertEqual(len(calls), 1)
            self.assertIn("EXCERPT ONLY", result["body"]["disclosure"])
            self.assertEqual(result["body"]["coverage"]["excerpt_only_refs"], ["p.1"])
            exported = artifact_markdown(result)
            self.assertIn("EXCERPT ONLY", exported)
            self.assertIn("p.1 — excerpt only", exported)
            self.assertNotIn("TAIL SENTINEL", json.dumps(calls))

    def test_ai_preview_approval_is_exact_and_key_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "library")
            record = app.import_uploaded(
                b"# Lesson\n\nRisk owners review evidence before approving the control. "
                b"Reviewers check whether the evidence is complete.", "lesson.md",
                {"title": "A lesson", "version": "1", "publication_date": "2026-11-25"})
            doc_id = record["document_id"]
            app.ensure_reader(doc_id)
            calls = []
            def fake_post(url, headers, body):
                calls.append((url, headers, body))
                return {"output": [{"content": [{"type": "output_text", "text":
                    '{"title":"Review lesson","items":[{"text":"Check evidence completeness.","source_ref":"chapter-1-segment-1"}]}'}]}]}
            app.ai = StudyAI(fake_post)
            app.ai.use_key("openai", "test-key-only-not-real", MODELS["openai"][1])
            exact = preview(app.store, doc_id, "chapter_points", "general", "openai",
                            MODELS["openai"][1], 1, 1)
            self.assertEqual(exact["characters"], len(exact["text"]))
            self.assertLessEqual(exact["characters"], 8000)
            self.assertEqual(exact["source_refs"], ["chapter-1-segment-1"])
            request = {"document_id": doc_id, "action": "chapter_points", "lens": "general",
                       "mode": "ai", "provider": "openai", "chapter_no": 1, "page_no": 1}
            with self.assertRaisesRegex(ValueError, "Review the exact"):
                app.study_generate(request)
            self.assertEqual(calls, [])
            result = app.study_generate({**request, "approval_hash": exact["approval_hash"]})
            self.assertEqual(len(calls), 1)
            self.assertIn(exact["text"], calls[0][2]["input"])
            self.assertNotIn("test-key-only-not-real", json.dumps(result))
            self.assertEqual(result["body"]["items"][0]["source_ref"], "chapter-1-segment-1")
            app.ai.forget_all()
            self.assertTrue(app.study_generate(request)["cached"])

    def test_ai_citation_rejects_unknown_reference(self):
        page = {"source_ref": "p.1", "page_no": 1}
        result = parse_ai_answer('{"title":"x","items":[{"text":"Claim","source_ref":"p.999"}]}',
                                 "file_points", [page])
        self.assertEqual(result["items"][0]["source_ref"], "")
        unseen = parse_ai_answer('{"title":"x","items":[{"text":"Claim","source_ref":"p.2"}]}',
                                 "file_points", [page, {"source_ref":"p.2", "page_no":2}], {"p.1"})
        self.assertEqual(unseen["items"][0]["source_ref"], "")

    def test_all_provider_study_and_connection_shapes_are_offline(self):
        for provider in MODELS:
            with self.subTest(provider=provider):
                calls = []
                def fake_post(url, headers, body):
                    calls.append((url, headers, body))
                    text = '{"title":"Source lesson","items":[{"text":"Read the evidence.","source_ref":"p.1"}]}'
                    if provider == "openai":
                        return {"output": [{"content": [{"type":"output_text", "text": text}]}],
                                "usage": {"input_tokens": 10, "output_tokens": 15}}
                    if provider == "anthropic":
                        return {"content": [{"type":"text", "text": text}],
                                "usage": {"input_tokens": 10, "output_tokens": 15}}
                    return {"candidates": [{"content": {"parts": [{"text": text}]}}],
                            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 15}}
                client = StudyAI(fake_post)
                client.use_key(provider, "fake-key-not-a-secret")
                result = client.study(provider, "[p.1 | Page 1]\nSource claim.", "Summarise source only.")
                self.assertIn("Read the evidence", result["answer"])
                self.assertEqual(result["input_tokens"], 10)
                check = client.test_connection(provider)
                self.assertTrue(check["ok"])
                self.assertEqual(len(calls), 2)
                self.assertNotIn("fake-key-not-a-secret", json.dumps(check))
                self.assertIn("Harmless connection check", json.dumps(calls[1][2]))
                self.assertNotIn("Source claim", json.dumps(calls[1][2]))
                self.assertEqual(calls[0][0], calls[1][0])
                client.forget_all()
                self.assertFalse(client.status()["enabled"])

    def test_authenticated_reader_http_flow_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LocalApp(Path(tmp) / "library")
            record = app.import_uploaded(
                b"# First\n\nThe operator checks the control evidence before approving a change. "
                b"The reviewer records the population and selects a representative sample.",
                "lesson.md", {"title": "Lesson", "version": "1", "publication_date": "2026-11-25"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                route = "/api/reader?" + urllib.parse.urlencode({"document_id": record["document_id"]})
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + route, timeout=5)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                launch = urllib.request.Request(base + "/session", data=b"", headers={
                    "Origin": base, "X-Local-Client": "FrameworkDistiller",
                    "X-Session-Token": app.session_token})
                with opener.open(launch, timeout=5) as response:
                    self.assertEqual(response.status, 204)
                with opener.open(base + route, timeout=5) as response:
                    reading = json.load(response)
                self.assertEqual(reading["chapters"][0]["title"], "First")
                self.assertEqual(len(reading["chapters"][0]["pages"]), 1)
                page_route = "/api/reader/page?" + urllib.parse.urlencode({
                    "document_id": record["document_id"], "page_no": 1, "lens": "general"})
                with opener.open(base + page_route, timeout=5) as response:
                    page = json.load(response)
                self.assertIn("representative sample", page["source_text"])
                self.assertIn("Explain this idea", page["challenge"])
                payload = {"document_id": record["document_id"], "action": "page_map",
                           "lens": "general", "mode": "offline", "chapter_no": 1, "page_no": 1}
                request = urllib.request.Request(base + "/api/study/generate",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json",
                    "Origin": base, "X-Local-Client": "FrameworkDistiller"})
                with opener.open(request, timeout=5) as response:
                    item = json.load(response)
                self.assertIsNotNone(item["body"]["map"])
                for fmt, expected in [("md", "text/markdown"), ("svg", "image/svg+xml")]:
                    export = "/api/study/export?" + urllib.parse.urlencode({
                        "key": item["cache_key"], "format": fmt})
                    with opener.open(base + export, timeout=5) as response:
                        self.assertIn(expected, response.headers["Content-Type"])
                        self.assertTrue(response.read())
            finally:
                server.shutdown(); server.server_close(); worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
