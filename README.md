# Framework Distiller (local MVP)

A deliberately small study desk for official public framework material. It runs a Python standard-library web server bound **only to `127.0.0.1`** and stores documents/progress in a local SQLite database. It makes no SOC, Proxmox, MCP or lab calls. Network downloads happen only when you click an official NIST source or pull a direct public link from an approved primary-source host. Cloud AI calls are **off by default** and occur only when you add your own provider key and explicitly send a card excerpt.

## Start

Double-click `Start Framework Distiller.cmd`, or from PowerShell:

```powershell
cd .\FrameworkDistiller
python run.py
```

The launcher opens a local browser session on `127.0.0.1:8776`. Stop with Ctrl+C (or close the launcher window). Python 3.11+ and the **pinned** document parsers in `requirements.txt` are needed for imports. Install those requirements in a local virtual environment. The parser versions are pinned for reproducible builds. OSCAL JSON import does not require extra packages.

The packaged Windows `.exe` includes Python and the document parsers, so its users do not need to install them. It has **no command window**: double-click the `.exe`, use the page in your browser, and choose **Storage and import location → Quit desktop app** when finished. Closing a terminal used to launch the `.exe` does not stop it. It binds to `127.0.0.1` only. By default it saves documents and progress under `%LOCALAPPDATA%\FrameworkDistiller`, **not** beside the executable. It is not a cloud service. The launcher gives the browser a fresh process-local session token via a URL fragment; the page immediately exchanges it for an HttpOnly, SameSite cookie. All `/api/` routes require that cookie. A manually opened bare loopback URL cannot access study data. This protects against other local apps guessing the port, but is **not** a defence against malware running as the same desktop user. Avoid confidential workpapers, secrets and sensitive answers on an untrusted computer. API keys entered for AI help live in this process's memory only.

On Windows, open **Storage and import location** to choose an absolute study-library folder and a separate import-inbox folder. The app saves this choice in the current Windows user's local app settings; every user's choice is independent. It switches immediately. It does **not** move or delete the previous library: choose the old folder again to reopen it. A folder on a network/removable drive must be available when the app starts. Command-line `--data` overrides the saved choice for that run only. On Android, the working SQLite library remains in private app storage; Android's system picker lets you import from folders you can access and choose an export destination. Android cannot use an arbitrary filesystem path for its working database.

## The four official source buttons

1. NIST CSF 2.0 — NIST's [OSCAL CSF catalog](https://github.com/usnistgov/oscal-content/blob/main/nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json). The import pins the actual OSCAL release version and byte hash; CSF subcategory outcomes become cards.
2. NIST AI RMF 1.0 — [official NIST PDF](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf). Core-function subcategory statements become cards with page links. NIST notes that AI RMF 1.0 is under revision; this app never silently upgrades a pinned copy.
3. NIST SP 800-53 Rev. 5 — [official NIST OSCAL catalog](https://github.com/usnistgov/oscal-content/blob/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json). The app imports the catalog but drafts cards for a focused set of 28 ITGC, monitoring, resilience and supplier controls. This is not the whole publication or a control baseline.
4. NIST SP 800-161 Rev. 1 — [official November 2024 update PDF](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-161r1-upd1.pdf). Nine page-level study leads cover acquisition, supplier practices, information-sharing and measures. These are reading leads, not invented control IDs.

The file downloaded from the exact approved URL is retained under `data/sources/` by its SHA-256 name; version/date, source URL, hash and provenance are stored in `data/study.sqlite3`. Re-importing changed official bytes creates another pinned version rather than silently replacing your notes.

After an app parser/card-template update, stop the server and run `python refresh_local.py` to regenerate unreviewed drafts from the already pinned files **without a network download**. Your edited drafts and study progress are retained.

## Add local files or pull a public link

Open **Add a document** in the app and choose a local **PDF, EPUB, DOCX, ODT, PPTX, RTF, Markdown, TXT or HTML** file. Title defaults to the filename; version defaults to “Local copy” and publication date to “Unknown”. A source URL is optional for local files. If provided, it must be an approved official HTTPS URL, but the app does not claim the file's bytes came from that URL. Every import receives a local SHA-256 pin.

For **Pull official link**, enter title, version, date and a direct public URL. Link pulls accept the same reading formats when the approved host serves a recognisable document. They are limited to HTTPS on NIST, Bank of England, FCA, NCSC, IIA and OWASP hosts; no private addresses, credentials, arbitrary sites, query-string tokens or out-of-scope redirects. A publisher-provided expected SHA-256 can be entered for either route. The app does not fetch arbitrary websites or follow links found inside a document.

The advanced inbox flow is also available:

Put a supported document in the inbox shown in the app. Press **Refresh inbox** after copying files there, then import the ones you choose. A sidecar is optional. Without one, the app labels the source **Local copy / Unknown date**, computes its own SHA-256, and makes no publisher or URL claim. To supply reviewed provenance, add a sidecar named `<filename>.source.json`, for example `SS2-21.pdf.source.json`:

```json
{
  "title": "PRA SS2/21",
  "source_url": "https://www.bankofengland.co.uk/prudential-regulation/publication/2021/march/outsourcing-and-third-party-risk-management-ss",
  "version": "March 2026 update",
  "publication_date": "2026-03",
  "sha256": "REPLACE_WITH_THE_64_CHARACTER_SHA256_OF_THE_LOCAL_FILE"
}
```

The app checks the local file hash against a sidecar when present; a malformed sidecar fails instead of silently becoming a local-only import. The sidecar URL is optional; when present, it must use an approved HTTPS primary-source domain. It **does not** verify that the URL serves identical bytes. Do not use paywalled or licensed copies unless your use is permitted. Generic files become chunked, text-based draft cards that need editorial review before serious use. Images, scanned pages without an OCR text layer, charts and some embedded content are not extracted; a file with no extractable text is refused. Inputs are bounded to 25 MB, archives to 40 MB uncompressed, PDFs to 500 pages, and extracted text to 2 million characters/2,000 cards. The app raises an error at a bound instead of silently claiming full coverage.

## Study flow and limits

- Default view is **Senior / Executive**, showing a ten-card unread focus set spread across the available themes. Switch Study set to **All matching** for the full filtered queue. Filter by audit theme, depth, reading state and search. Cards from similar themes appear together, but this is a *reading map*, not a legal equivalence crosswalk. **Export all filtered** includes the full matching queue, not just the ten visible focus cards.
- Each five-part card shows its extracted source excerpt, section/page reference, source version/date and hash. Official link imports show a source link; local-only files show their hash without inventing a public citation. The core rule is a short source-derived draft or a small curated paraphrase; risk, test and auditee trap are explicitly **auditor interpretations/scenario prompts**, not standards text.
- “Challenge me” saves your open-ended answer locally. “Mastered” is self-reported and never a competence score. Edit a draft to improve its wording; the source excerpt is immutable in the database.
- Export the filtered cards as Markdown and save that download into your audit workbench. It contains the citation and hash for re-performance.

Study progress is self-reported, not a qualification or validated competence. The app makes no claim that reading is 20× faster; measure time-to-explain and workpaper quality against your own baseline. This private candidate still needs independent release review before public publication.

## Optional AI study help (bring your own key)

The provider selector starts **Off**. You may choose OpenAI, Anthropic or Gemini and enter your **own API key**. No key is shipped in the source, `.exe` or APK, loaded from the environment, written to SQLite/JSON/Markdown, or retained after the app process closes. “Forget key” removes it from app memory immediately. API subscriptions and consumer-chat subscriptions are not necessarily interchangeable; your provider account bears any API charge. The provider-documented default model IDs are `gpt-6-luna`, `claude-haiku-5-5` and `gemini-3.8-flash`, respectively. You can edit the model ID in AI Settings for the current app session. Availability still depends on the provider and your API account; no live cloud request was made as part of this private build.

On a card, **Ask AI about this excerpt** shows the exact source excerpt to be sent (first 4,000 characters if longer) and an editable question, with a separate **Send** button. Only that excerpt and question go to the selected provider. Your saved notes, progress, answers, other documents and full files are never sent automatically. No provider calls occur during import or offline card generation. The app never invokes provider tools or follows instructions found in imported text; AI output is labelled as an interpretation and must be checked against the pinned primary source. Do not send confidential employer policies or personal/regulated data through a personal provider key without authorisation. The provider's own terms and retention policy apply to the request.

## Check

```powershell
python -m unittest discover -s tests -v
```

No test touches the live SOC, Proxmox, either MCP service, or a cloud AI provider. Provider request shapes are tested with a fake transport. The app has no routes for shell, agent control, remediation or external publication.

## Android build

The same Python core and UI run in an Android WebView via Chaquopy. Run `python prepare_android.py` before building, then use the Gradle wrapper in `android/` for `:app:assembleDebug` or `:app:assembleRelease`. The APK is **arm64-v8a only**. The debug APK is for local testing; a public Android release needs an owner-controlled signing key and independent review. The `android/sign-apk.sh` script signs an aligned unsigned release APK with an owner-held keystore and prompts locally for its password. Never commit the keystore, password or signing output to the source archive. The app requests only `INTERNET` permission for optional official-source pulls and explicit BYOK requests. Imported files are chosen through Android's system file picker; the internal study database is private to the app. The debug APK has been exercised on one Android test device, not on a general device matrix.

The Android release must be accompanied by [third-party notices](THIRD-PARTY-NOTICES.txt) alongside the APK download. The notice names the bundled parser and runtime versions and includes their full upstream license texts; the project's own terms are in [LICENSE](LICENSE). Keep these companion files with the signed APK when publishing it.

## Build a shareable Windows executable

On Windows, install `requirements-build.txt` in a disposable virtual environment and run `python build_windows.py`. The build writes an `.exe`, a versioned Windows ZIP and a versioned source ZIP under this project's `dist/` folder; `dist/` and `.build/` are Git-ignored. Both ZIPs include the project [MIT license](LICENSE) and the full [third-party notices](THIRD-PARTY-NOTICES.txt). [PyInstaller's official documentation](https://pyinstaller.org/en/stable/) explains the one-file bundle and its temporary extraction directory. The executable is currently **unsigned**; do not present it as a trusted signed publisher build. Publishing it on GitHub is a separate, owner-authorised release action after signing and review.
