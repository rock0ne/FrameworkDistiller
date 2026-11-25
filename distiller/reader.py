"""Bounded, sequential reading structure from a pinned document's bytes.

These are reading pages/segments, not a claim that a parser recovered every
visual element of the original document. Existing study cards remain separate.
"""

from __future__ import annotations

import io
import posixpath
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from .ingest import (IngestError, MAX_PDF_PAGES, MAX_TEXT_CHARS, MAX_UNITS,
                     _archive, _document_sections, _html_text, _member,
                     _plain_text, _xml_root, extract_units)


FRONT = re.compile(r"^(?:cover|title page|copyright|imprint|isbn|contents|table of contents|"
                   r"dedication|acknowledg(?:e)?ments?|foreword|preface|about the author|"
                   r"list of (?:figures|tables)|index)\b", re.I)
MAX_SEGMENT = 4000


@dataclass(frozen=True)
class ReadingPage:
    chapter: int
    number: int
    label: str
    source_ref: str
    source_link: str
    text: str
    front_matter: bool = False


@dataclass(frozen=True)
class ReadingChapter:
    number: int
    title: str
    pages: tuple[ReadingPage, ...]
    front_matter: bool = False


@dataclass(frozen=True)
class ReadingDocument:
    structure_kind: str
    chapters: tuple[ReadingChapter, ...]
    omitted_blank_pages: int = 0


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text.replace("\x00", ""))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _front(title: str, text: str = "") -> bool:
    if FRONT.search(title.strip()):
        return True
    first = text[:1200]
    return bool(first and len(re.findall(r"\.{4,}\s*\d+", first)) >= 4)


def _segments(text: str) -> list[str]:
    """Split long logical chapters on paragraph/word boundaries, without loss."""
    text = _clean(text)
    result = []
    while text:
        if len(text) <= MAX_SEGMENT:
            result.append(text)
            break
        cut = max(text.rfind("\n\n", 0, MAX_SEGMENT), text.rfind(" ", 0, MAX_SEGMENT))
        if cut < MAX_SEGMENT // 2:
            cut = MAX_SEGMENT
        result.append(text[:cut].strip())
        text = text[cut:].strip()
    return result


def _from_sections(sections: list[tuple[str, str]], kind: str,
                   source_url: str) -> ReadingDocument:
    chapters = []
    page_no = 0
    total = 0
    for title, text in sections:
        pieces = _segments(text)
        total += sum(len(piece) for piece in pieces)
        if total > MAX_TEXT_CHARS or page_no + len(pieces) > MAX_UNITS:
            raise IngestError("Reading structure exceeds the text or page bound")
        if not pieces:
            continue
        chapter_no = len(chapters) + 1
        front = _front(title, text)
        pages = []
        for segment_no, piece in enumerate(pieces, 1):
            page_no += 1
            pages.append(ReadingPage(chapter_no, page_no, f"Segment {segment_no}",
                                     f"chapter-{chapter_no}-segment-{segment_no}",
                                     source_url, piece, front))
        chapters.append(ReadingChapter(chapter_no, title[:240], tuple(pages), front))
    if not chapters:
        raise IngestError("No extractable reading sections were found")
    return ReadingDocument(kind, tuple(chapters))


def _markdown_sections(data: bytes) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = "Opening section"
    lines: list[str] = []
    for line in _plain_text(data).splitlines():
        match = re.match(r"^#{1,3}\s+(.+?)\s*#*\s*$", line)
        if match:
            if any(part.strip() for part in lines):
                sections.append((title, "\n".join(lines)))
            title, lines = match[1][:240], []
        else:
            lines.append(line)
    if any(part.strip() for part in lines):
        sections.append((title, "\n".join(lines)))
    return sections or [("Document text", _plain_text(data))]


def _docx_sections(data: bytes) -> list[tuple[str, str]]:
    with _archive(data) as archive:
        root = _xml_root(_member(archive, "word/document.xml"))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    sections: list[tuple[str, str]] = []
    title, lines = "Opening section", []
    for paragraph in root.iter(ns + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(ns + "t")).strip()
        if not text:
            continue
        style = paragraph.find(f"{ns}pPr/{ns}pStyle")
        style_name = style.attrib.get(ns + "val", "") if style is not None else ""
        if re.fullmatch(r"Heading[1-3]|Title", style_name, re.I):
            if lines:
                sections.append((title, "\n".join(lines)))
            title, lines = text[:240], []
        else:
            lines.append(text)
    if lines:
        sections.append((title, "\n".join(lines)))
    return sections


class _FirstHeading(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.capture = False
        self.heading = ""
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"h1", "h2", "h3"} and not self.heading and not self.hidden:
            self.capture = True

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"h1", "h2", "h3"}:
            self.capture = False

    def handle_data(self, data):
        if self.capture and not self.hidden:
            self.heading += data


def _html_heading(data: bytes) -> str:
    parser = _FirstHeading()
    parser.feed(data.decode("utf-8-sig", errors="replace"))
    return _clean(parser.heading)[:240]


def _epub_sections(data: bytes) -> list[tuple[str, str]]:
    with _archive(data) as archive:
        container = _xml_root(_member(archive, "META-INF/container.xml"))
        rootfile = next((node.attrib.get("full-path") for node in container.iter()
                         if node.tag.endswith("rootfile") and node.attrib.get("full-path")), None)
        if not rootfile:
            raise IngestError("EPUB has no package document")
        opf = _xml_root(_member(archive, rootfile))
        manifest = {node.attrib.get("id"): node.attrib.get("href")
                    for node in opf.iter() if node.tag.endswith("item") and node.attrib.get("id")}
        spine = [node.attrib.get("idref") for node in opf.iter()
                 if node.tag.endswith("itemref") and node.attrib.get("idref")]
        sections = []
        for index, identifier in enumerate(spine, 1):
            href = manifest.get(identifier, "")
            content_path = unquote(href.split("#", 1)[0]) if href else ""
            if not content_path or Path(content_path).suffix.lower() not in {".xhtml", ".html", ".htm"}:
                continue
            path = posixpath.join(posixpath.dirname(rootfile), content_path)
            chapter_bytes = _member(archive, path)
            title = _html_heading(chapter_bytes) or f"Spine section {index}"
            sections.append((title, _html_text(chapter_bytes)))
    return sections


def _pdf_reader(data: bytes, source_url: str) -> ReadingDocument:
    try:
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(data), strict=False)
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise IngestError("PDF exceeds the 500-page extraction bound")
        bookmarks: list[tuple[int, str]] = []
        for item in (pdf.outline or [])[:MAX_PDF_PAGES]:
            if isinstance(item, list):
                continue  # top-level bookmarks define the chapter boundary
            try:
                page = pdf.get_destination_page_number(item) + 1
                title = _clean(str(item.title))[:240]
            except (AttributeError, KeyError, ValueError, IndexError):
                continue
            if 1 <= page <= len(pdf.pages) and title:
                bookmarks.append((page, title))
        bookmarks = sorted(dict(bookmarks).items())
        chapters = []
        omitted = 0
        total = 0
        ranges = []
        if bookmarks:
            if bookmarks[0][0] > 1:
                ranges.append((1, bookmarks[0][0] - 1, "Opening pages"))
            for index, (start, title) in enumerate(bookmarks):
                end = bookmarks[index + 1][0] - 1 if index + 1 < len(bookmarks) else len(pdf.pages)
                if end >= start:
                    ranges.append((start, end, title))
            kind = "pdf_bookmarks"
        else:
            ranges = [(start, min(start + 9, len(pdf.pages)),
                       f"Pages {start}–{min(start + 9, len(pdf.pages))} · no PDF TOC")
                      for start in range(1, len(pdf.pages) + 1, 10)]
            kind = "pdf_page_ranges"
        for start, end, title in ranges:
            chapter_no = len(chapters) + 1
            chapter_front = _front(title) or (title == "Opening pages")
            pages = []
            for page_no in range(start, end + 1):
                try:
                    raw = pdf.pages[page_no - 1].extract_text() or ""
                except Exception as exc:
                    raise IngestError(f"PDF page {page_no} could not be read") from exc
                text = _clean(raw)
                total += len(text)
                if total > MAX_TEXT_CHARS:
                    raise IngestError("Extracted reading text exceeds the 2-million-character bound")
                if not text:
                    omitted += 1
                    continue
                page_front = chapter_front or (page_no <= 15 and _front("", text))
                link = f"{source_url}#page={page_no}" if source_url else ""
                pages.append(ReadingPage(chapter_no, page_no, f"Page {page_no}",
                                         f"p.{page_no}", link, text, page_front))
            if pages:
                chapters.append(ReadingChapter(chapter_no, title, tuple(pages), chapter_front))
        if not chapters:
            raise IngestError("PDF has no extractable reading text")
        if sum(len(ch.pages) for ch in chapters) > MAX_UNITS:
            raise IngestError("Reading structure exceeds the page bound")
        return ReadingDocument(kind, tuple(chapters), omitted)
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError("PDF reading structure is unreadable") from exc


def extract_reading(data: bytes, record: dict) -> ReadingDocument:
    """Extract a sequential reader without changing existing card definitions."""
    fmt = record["format"]
    source_url = record.get("source_url", "")
    if fmt == "pdf":
        return _pdf_reader(data, source_url)
    if fmt == "epub":
        return _from_sections(_epub_sections(data), "epub_spine", source_url)
    if fmt == "docx":
        return _from_sections(_docx_sections(data), "docx_headings", source_url)
    if fmt == "md":
        return _from_sections(_markdown_sections(data), "markdown_headings", source_url)
    if fmt == "oscal-json":
        units = extract_units(data, record)
        groups: list[tuple[str, str]] = []
        for unit in units:
            title = unit.family or "Catalog sections"
            text = f"{unit.ref} — {unit.title}\n{unit.text}"
            if groups and groups[-1][0] == title:
                groups[-1] = (title, groups[-1][1] + "\n\n" + text)
            else:
                groups.append((title, text))
        return _from_sections(groups, "catalog_order", source_url)
    sections = _document_sections(data, fmt, source_url)
    if fmt == "html":
        heading = _html_heading(data)
        if heading and sections:
            sections[0] = (heading, sections[0][1], sections[0][2], sections[0][3])
    return _from_sections([(title, text) for title, text, _, _ in sections],
                          "source_sections" if len(sections) > 1 else "logical_segments", source_url)
