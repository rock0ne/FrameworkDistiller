"""Download exact NIST files or ingest locally supplied public documents."""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from . import __version__
from .catalog import OFFICIAL, allowed_dropin_url


MAX_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_UNITS = 2000
MAX_TEXT_CHARS = 2_000_000
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
FORMAT_BY_EXTENSION = {
    ".pdf": "pdf", ".html": "html", ".htm": "html", ".epub": "epub",
    ".docx": "docx", ".odt": "odt", ".pptx": "pptx", ".rtf": "rtf",
    ".md": "md", ".markdown": "md", ".txt": "txt", ".text": "txt",
}


class IngestError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    ref: str
    title: str
    text: str
    page: int | None
    source_link: str
    family: str


class _BoundedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {
            "raw.githubusercontent.com", "nvlpubs.nist.gov"
        }:
            raise IngestError("Official download redirected outside the approved source hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_official(source_id: str) -> tuple[bytes, dict]:
    source = OFFICIAL.get(source_id)
    if source is None:
        raise IngestError("Unknown official source")
    request = urllib.request.Request(source.url, headers={
        "User-Agent": f"Mozilla/5.0 FrameworkDistiller/{__version__} (local study tool)",
        "Accept": "application/json, application/pdf",
    })
    opener = urllib.request.build_opener(_BoundedRedirect())
    with opener.open(request, timeout=45) as response:
        final = urlparse(response.url)
        if final.scheme != "https" or final.hostname not in {
            "raw.githubusercontent.com", "nvlpubs.nist.gov"
        }:
            raise IngestError("Unexpected source host")
        data = response.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise IngestError("Source is empty or exceeds the 25 MB bound")
    if source.format == "pdf" and not data.startswith(b"%PDF-"):
        raise IngestError("Official source did not return a PDF")
    if source.format == "oscal-json":
        try:
            parsed = json.loads(data)
            metadata = parsed["catalog"]["metadata"]
        except (ValueError, KeyError, TypeError) as exc:
            raise IngestError("Official source did not return an OSCAL catalog") from exc
        version = f"{source.version}; OSCAL {metadata.get('version', 'unknown')}"
        published = metadata.get("published") or source.publication_date
    else:
        version = source.version
        published = source.publication_date
    return data, {
        "source_id": source.source_id, "title": source.title,
        "source_url": source.url, "authority_url": source.authority_url,
        "format": source.format, "version": version,
        "publication_date": published, "sha256": hashlib.sha256(data).hexdigest(),
        "provenance": "downloaded_from_approved_nist_url",
    }


def read_dropin(inbox: Path, name: str, *, max_batch_bytes: int = MAX_BYTES) -> tuple[bytes, dict]:
    _document_format(name)
    if type(max_batch_bytes) is not int or not 1 <= max_batch_bytes <= 1024 * 1024 * 1024:
        raise IngestError("Invalid remaining batch budget")
    path = inbox / name
    if path.is_symlink() or not path.is_file() or path.parent.resolve() != inbox.resolve():
        raise IngestError("File is not a regular inbox item")
    sidecar = inbox / f"{name}.source.json"
    limit = min(MAX_BYTES, max_batch_bytes)
    if path.stat().st_size > limit:
        raise IngestError("Drop-in exceeds the 25 MB file or remaining batch limit")
    if sidecar.is_symlink():
        raise IngestError("Source metadata must be a regular file")
    if sidecar.exists():
        if not sidecar.is_file() or sidecar.stat().st_size > 16_384:
            raise IngestError("Source metadata is not a regular file or is too large")
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise IngestError("Invalid source metadata JSON") from exc
        if not isinstance(meta, dict) or "sha256" not in meta:
            raise IngestError("A sidecar must provide the published or locally verified sha256")
        provenance = "owner_supplied_hash_checked_not_url_verified"
    else:
        # A bare file is usable for private study, but makes no publisher/version claim.
        meta = {"title": Path(name).stem[:160], "version": "Local copy",
                "publication_date": "Unknown", "source_url": "", "sha256": ""}
        provenance = "owner_uploaded_local_hash_only"
    # The directory listing is only a preflight: the file may grow before it is read.
    # Never load more than the remaining batch budget plus one refusal byte.
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise IngestError("Drop-in exceeds the 25 MB file or remaining batch limit")
    return read_uploaded(data, name, meta, provenance=provenance)


def read_uploaded(data: bytes, name: str, meta: dict,
                  provenance: str = "owner_uploaded_url_not_byte_verified") -> tuple[bytes, dict]:
    """Validate a selected document; a supplied URL is a claim, not proof of bytes."""
    fmt = _document_format(name)
    required = ("title", "version", "publication_date")
    if any(not isinstance(meta.get(key), str) or not meta[key].strip()
           or len(meta[key]) > 2048 for key in required):
        raise IngestError("Title, version and date are required")
    source_url = meta.get("source_url", "")
    if not isinstance(source_url, str) or len(source_url) > 2048:
        raise IngestError("Source URL must be text")
    source_url = source_url.strip()
    if source_url and not allowed_dropin_url(source_url):
        raise IngestError("Source URL must be HTTPS on an approved primary-source host")
    expected = meta.get("sha256", "")
    if not isinstance(expected, str) or (expected and not re.fullmatch(r"[0-9a-fA-F]{64}", expected)):
        raise IngestError("Expected sha256 must be a 64-character hex digest")
    if not data or len(data) > MAX_BYTES:
        raise IngestError("File is empty or exceeds 25 MB")
    if fmt == "pdf" and not data.startswith(b"%PDF-"):
        raise IngestError("File is not a PDF")
    if fmt == "html" and not _looks_html(data):
        raise IngestError("File is not HTML")
    if fmt in {"epub", "docx", "odt", "pptx"} and not zipfile.is_zipfile(io.BytesIO(data)):
        raise IngestError(f"File is not a readable {fmt.upper()} archive")
    if fmt == "rtf" and not data.lstrip().startswith(b"{\\rtf"):
        raise IngestError("File is not RTF")
    if fmt in {"md", "txt"} and (b"\x00" in data or
            sum(byte < 9 or 13 < byte < 32 for byte in data[:4096]) > 20):
        raise IngestError("File does not look like plain text")
    digest = hashlib.sha256(data).hexdigest()
    if expected and digest.lower() != expected.lower():
        raise IngestError("File hash does not match the expected sha256")
    record = {key: meta[key].strip() for key in required}
    record["source_url"] = source_url
    record.update({
        "source_id": "dropin-" + hashlib.sha256((source_url + name + digest).encode()).hexdigest()[:16],
        "sha256": digest,
        "format": fmt, "authority_url": source_url,
        "provenance": provenance if source_url else "owner_uploaded_local_hash_only",
    })
    return data, record


def _document_format(name: str) -> str:
    if (not isinstance(name, str) or len(name) > 160 or not name or name.startswith(".")
            or "/" in name or "\\" in name or ".." in name):
        raise IngestError("Use a plain document filename without a path")
    fmt = FORMAT_BY_EXTENSION.get(Path(name).suffix.lower())
    if not fmt:
        raise IngestError("Supported files: PDF, EPUB, DOCX, ODT, PPTX, RTF, Markdown, TXT and HTML")
    return fmt


def _looks_html(data: bytes) -> bool:
    head = data[:2048].lstrip().lower()
    return b"<html" in head or b"<!doctype html" in head or b"<body" in head


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_dropin_url(newurl) or urlparse(newurl).query:
            raise IngestError("Public document redirected outside approved HTTPS hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_public(url: str, title: str, version: str, publication_date: str,
                    expected_sha256: str = "") -> tuple[bytes, dict]:
    """Download a public reading document from an approved official HTTPS host."""
    meta = {"title": title, "source_url": url, "version": version,
            "publication_date": publication_date, "sha256": expected_sha256}
    if not all(isinstance(meta[key], str) for key in meta):
        raise IngestError("Source metadata must be text")
    if not allowed_dropin_url(url) or len(url) > 2048:
        raise IngestError("Use a direct public HTTPS link on an approved primary-source host, without query parameters")
    request = urllib.request.Request(url, headers={
        "User-Agent": f"Mozilla/5.0 FrameworkDistiller/{__version__} (local study tool)",
        "Accept": "application/pdf, text/html",
    })
    opener = urllib.request.build_opener(_PublicRedirect())
    try:
        with opener.open(request, timeout=45) as response:
            final_url = response.url
            if not allowed_dropin_url(final_url) or urlparse(final_url).query:
                raise IngestError("Final URL is outside approved HTTPS hosts")
            data = response.read(MAX_BYTES + 1)
            content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
    except urllib.error.HTTPError as exc:
        raise IngestError(f"Official link returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise IngestError(f"Official link download failed: {type(exc.reason).__name__}") from exc
    path_name = posixpath.basename(unquote(urlparse(final_url).path))
    fmt = FORMAT_BY_EXTENSION.get(Path(path_name).suffix.lower())
    if not fmt:
        if data.startswith(b"%PDF-"):
            fmt = "pdf"
        elif _looks_html(data):
            fmt = "html"
        elif "text/plain" in content_type.lower():
            fmt = "txt"
        else:
            raise IngestError("Link must resolve to a supported document format")
    # Reuse byte/hash/title validation, but bind the pinned record to the final URL.
    meta["source_url"] = final_url
    name = f"public-source.{fmt}"
    data, record = read_uploaded(data, name, meta, provenance="downloaded_from_approved_public_url")
    known = next((source for source in OFFICIAL.values() if source.url == final_url), None)
    if known is not None:
        record["source_id"] = known.source_id
        record["title"] = known.title
        record["version"] = known.version
        record["publication_date"] = known.publication_date
        record["authority_url"] = known.authority_url
    else:
        record["source_id"] = "web-" + hashlib.sha256((final_url + record["sha256"]).encode()).hexdigest()[:16]
    return data, record


def _flatten_parts(parts: list[dict]) -> str:
    lines: list[str] = []
    def visit(part: dict) -> None:
        prose = part.get("prose")
        if isinstance(prose, str) and prose.strip():
            lines.append(prose.strip())
        for child in part.get("parts", []):
            visit(child)
    for part in parts:
        visit(part)
    return " ".join(lines)


def _oscal_units(data: bytes, source_id: str, authority_url: str) -> list[Unit]:
    catalog = json.loads(data)["catalog"]
    units: list[Unit] = []
    for group in catalog.get("groups", []):
        family = group.get("title", "")
        for control in group.get("controls", []):
            if any(p.get("name") == "status" and p.get("value") == "withdrawn"
                   for p in control.get("props", [])):
                continue
            candidates = control.get("controls", []) if source_id == "nist-csf-2" else [control]
            for item in candidates:
                if any(p.get("name") == "status" and p.get("value") == "withdrawn"
                       for p in item.get("props", [])):
                    continue
                statement_parts = [p for p in item.get("parts", []) if p.get("name") == "statement"]
                text = _flatten_parts(statement_parts)
                if not text:
                    continue
                ref = item.get("id", "").upper()
                if not re.fullmatch(r"[A-Z0-9][A-Z0-9.()_-]{1,32}", ref):
                    continue
                units.append(Unit(ref, item.get("title") or ref, text, None,
                                  authority_url, family))
    return units


def _normalise_pdf_text(text: str) -> str:
    text = text.replace("\ufffd", "-")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _ai_subcategory_excerpt(text: str) -> str:
    """Stop at the next category header, not just the next subcategory."""
    text = text.split("Categories Subcategories", 1)[0]
    text = re.split(r"\b(?:GOVERN|MAP|MEASURE|MANAGE)\s+\d+\s*:", text, maxsplit=1, flags=re.I)[0]
    return text.strip()


def _pdf_units(data: bytes, source_id: str, source_url: str) -> list[Unit]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
    except (ImportError, Exception) as exc:
        raise IngestError("PDF extraction requires pypdf and a readable PDF") from exc
    if len(reader.pages) > MAX_PDF_PAGES:
        raise IngestError("PDF exceeds the 500-page extraction bound")
    units: list[Unit] = []
    if source_id == "nist-ai-rmf-1":
        pattern = re.compile(r"\b(GOVERN|MAP|MEASURE|MANAGE)\s+(\d+\.\d+)\s*:\s*", re.I)
        for page_no in range(24, min(len(reader.pages), 40)):
            text = _normalise_pdf_text(reader.pages[page_no].extract_text() or "")
            matches = list(pattern.finditer(text))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                excerpt = _ai_subcategory_excerpt(text[match.end():end])
                excerpt = excerpt[:1400]
                if 20 <= len(excerpt) and not any(u.ref == f"{match[1].upper()} {match[2]}" for u in units):
                    units.append(Unit(f"{match[1].upper()} {match[2]}", f"{match[1].title()} {match[2]}",
                                      excerpt, page_no + 1, f"{source_url}#page={page_no + 1}", match[1].title()))
    elif source_id == "nist-sp-800-161-r1":
        targets = {51: ("3.1.", "Acquisition"), 52: ("3.1.1.", "Acquisition strategy"),
                   54: ("3.1.2.", "Procurement role"),
                   56: ("3.2.", "Information sharing"),
                   58: ("3.3.", "Training and awareness"),
                   60: ("3.4.", "Key practices"),
                   62: ("3.4.2.", "Sustaining practices"),
                   63: ("3.4.3.", "Enhancing practices"),
                   66: ("3.5.1.", "Performance measures")}
        for page_number, (anchor, title) in targets.items():
            if page_number > len(reader.pages):
                continue
            text = _normalise_pdf_text(reader.pages[page_number - 1].extract_text() or "")
            match = re.search(r"(?<!\d)" + re.escape(anchor) + r"\s", text)
            if match:
                text = text[match.start():]
            if len(text) < 100:
                continue
            # The PDF is a long guidance document: these are page-level study leads,
            # not invented NIST control identifiers or a claim to cover the whole PDF.
            units.append(Unit(f"p.{page_number}", title, text[:2100], page_number,
                              f"{source_url}#page={page_number}", "Supply-chain guidance"))
    else:
        for page_number, page in enumerate(reader.pages, 1):
            try:
                text = _normalise_pdf_text(page.extract_text() or "")
            except Exception as exc:
                raise IngestError(f"PDF page {page_number} could not be read") from exc
            link = f"{source_url}#page={page_number}" if source_url else ""
            units.extend(_section_units([(f"Page {page_number}", text, page_number, link)],
                                        ref_prefix=f"p.{page_number}"))
    return units


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.chunks: list[str] = []
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag in {"h1", "h2", "h3", "p", "li", "br"}:
            self.chunks.append("\n")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


def _html_text(data: bytes) -> str:
    parser = _TextHTML()
    parser.feed(data.decode("utf-8-sig", errors="replace"))
    return "".join(parser.chunks)


def _plain_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _section_units(sections: list[tuple[str, str, int | None, str]],
                   ref_prefix: str = "section") -> list[Unit]:
    units: list[Unit] = []
    total = 0
    for section_index, (title, raw_text, page, link) in enumerate(sections, 1):
        clean = re.sub(r"\s+", " ", raw_text).strip()
        total += len(clean)
        if total > MAX_TEXT_CHARS:
            raise IngestError("Extracted text exceeds the 2-million-character bound")
        part = 0
        while clean:
            piece = clean[:1800]
            if len(clean) > 1800:
                break_at = piece.rfind(" ")
                if break_at >= 1200:
                    piece = piece[:break_at]
            clean = clean[len(piece):].lstrip()
            part += 1
            ref = f"{ref_prefix}-{section_index}" if ref_prefix == "section" else ref_prefix
            if part > 1:
                ref = f"{ref}-{part}"
            units.append(Unit(ref, title if part == 1 else f"{title} (part {part})",
                              piece, page, link, "Local document"))
            if len(units) > MAX_UNITS:
                raise IngestError("Document exceeds the 2,000-card extraction bound")
    return units


def _archive(data: bytes) -> zipfile.ZipFile:
    archive = None
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        members = archive.infolist()
        if (len(members) > 1000 or
                sum(member.file_size for member in members) > MAX_ARCHIVE_BYTES or
                any(member.file_size > 10 * 1024 * 1024 for member in members)):
            archive.close()
            raise IngestError("Archive exceeds the safe extraction bound")
        # Central-directory sizes are only a preflight. _member also enforces
        # these limits against bytes actually returned by decompression.
        archive._fd_actual_bytes = 0
        return archive
    except (zipfile.BadZipFile, OSError) as exc:
        raise IngestError("Document archive is unreadable") from exc


def _member(archive: zipfile.ZipFile, path: str) -> bytes:
    normal = posixpath.normpath(path)
    if normal.startswith("../") or normal.startswith("/") or normal in {".", ".."}:
        raise IngestError("Unsafe document archive member")
    try:
        used = archive._fd_actual_bytes
        remaining = min(10 * 1024 * 1024, MAX_ARCHIVE_BYTES - used)
        if remaining < 0:
            raise IngestError("Archive exceeds the safe extraction bound")
        with archive.open(normal) as stream:
            data = stream.read(remaining + 1)
        if len(data) > remaining:
            raise IngestError("Archive exceeds the safe extraction bound")
        archive._fd_actual_bytes = used + len(data)
        return data
    except (KeyError, zipfile.BadZipFile, EOFError, RuntimeError, OSError) as exc:
        raise IngestError("Required document section is missing or damaged") from exc


def _xml_root(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise IngestError("Document XML is unreadable") from exc


def _office_paragraphs(root: ET.Element, paragraph_tag: str, text_tag: str) -> str:
    paragraphs = []
    for paragraph in root.iter():
        if paragraph.tag != paragraph_tag:
            continue
        text = "".join(node.text or "" for node in paragraph.iter() if node.tag == text_tag)
        if text.strip():
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _document_sections(data: bytes, fmt: str, source_url: str) -> list[tuple[str, str, int | None, str]]:
    if fmt in {"txt", "md"}:
        return [("Document text", _plain_text(data), None, source_url)]
    if fmt == "html":
        return [("Document text", _html_text(data), None, source_url)]
    if fmt == "rtf":
        try:
            from striprtf.striprtf import rtf_to_text
            text = rtf_to_text(data.decode("latin1"), errors="ignore")
        except (ImportError, UnicodeError, ValueError) as exc:
            raise IngestError("RTF extraction requires striprtf and a readable file") from exc
        return [("Document text", text, None, source_url)]
    with _archive(data) as archive:
        if fmt == "docx":
            root = _xml_root(_member(archive, "word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            return [("Document text", _office_paragraphs(root, ns + "p", ns + "t"), None, source_url)]
        if fmt == "odt":
            root = _xml_root(_member(archive, "content.xml"))
            text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
            paragraphs = ["".join(node.itertext()) for node in root.iter()
                          if node.tag in {text_ns + "p", text_ns + "h"}]
            return [("Document text", "\n\n".join(paragraphs), None, source_url)]
        if fmt == "pptx":
            paths = sorted((name for name in archive.namelist()
                            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                           key=lambda name: int(re.search(r"slide(\d+)", name)[1]))
            ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
            return [(f"Slide {index}", _office_paragraphs(_xml_root(_member(archive, path)),
                                                           ns + "p", ns + "t"), index, source_url)
                    for index, path in enumerate(paths, 1)]
        if fmt == "epub":
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
                sections.append((f"Chapter {index}", _html_text(_member(archive, path)), None, source_url))
            if not sections:
                raise IngestError("EPUB has no readable spine chapters")
            return sections
    raise IngestError("Unsupported document format")


def extract_units(data: bytes, record: dict) -> list[Unit]:
    fmt = record["format"]
    if fmt == "oscal-json":
        units = _oscal_units(data, record["source_id"], record["authority_url"])
    elif fmt == "pdf":
        units = _pdf_units(data, record["source_id"], record["source_url"])
    elif fmt in {"html", "txt", "md", "rtf", "docx", "odt", "pptx", "epub"}:
        units = _section_units(_document_sections(data, fmt, record["source_url"]))
    else:
        raise IngestError("Unsupported format")
    if len(units) > MAX_UNITS or sum(len(unit.text) for unit in units) > MAX_TEXT_CHARS:
        raise IngestError("Document exceeds the extracted-text or card-count bound")
    if not units:
        raise IngestError("No extractable source sections were found")
    return units
