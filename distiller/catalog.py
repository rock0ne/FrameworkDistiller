"""Exact NIST sources and approved public-host allowlist for URL imports."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class OfficialSource:
    source_id: str
    title: str
    url: str
    format: str
    version: str
    publication_date: str
    authority_url: str


OFFICIAL = {
    "nist-csf-2": OfficialSource(
        "nist-csf-2", "NIST CSF 2.0", 
        "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json",
        "oscal-json", "2.0 (OSCAL release pinned on import)", "2024-02-26",
        "https://csrc.nist.gov/projects/cybersecurity-framework/filters",
    ),
    "nist-ai-rmf-1": OfficialSource(
        "nist-ai-rmf-1", "NIST AI RMF 1.0",
        "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
        "pdf", "1.0", "2023-01",
        "https://airc.nist.gov/airmf-resources/airmf/",
    ),
    "nist-sp-800-53-r5": OfficialSource(
        "nist-sp-800-53-r5", "NIST SP 800-53 Rev. 5",
        "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json",
        "oscal-json", "Rev. 5 (OSCAL release pinned on import)", "2020-09",
        "https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final",
    ),
    "nist-sp-800-161-r1": OfficialSource(
        "nist-sp-800-161-r1", "NIST SP 800-161 Rev. 1, Update 1",
        "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-161r1-upd1.pdf",
        "pdf", "Rev. 1, Update 1", "2024-11-01",
        "https://csrc.nist.gov/pubs/sp/800/161/r1/upd1/final",
    ),
}


DROPIN_OFFICIAL_HOSTS = frozenset({
    "bankofengland.co.uk", "www.bankofengland.co.uk", "fca.org.uk", "www.fca.org.uk",
    "ncsc.gov.uk", "www.ncsc.gov.uk", "theiia.org", "www.theiia.org",
    "owasp.org", "www.owasp.org", "nist.gov", "www.nist.gov",
    "csrc.nist.gov", "nvlpubs.nist.gov",
})


def allowed_dropin_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in DROPIN_OFFICIAL_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.query
        )
    except ValueError:
        return False


def official_list() -> list[dict[str, str]]:
    return [vars(source).copy() for source in OFFICIAL.values()]
