r"""TDD test suite for Task 2.1: zero-token deterministic ingest-landing harness.

These tests are written FIRST and are expected to FAIL until the harness is
implemented (harness/ingest_landing.py).

Contract under test (new-vault-plan.md §10.2/§5.2/§5.3/§9.1/§5.1;
schema/log-events.md `ingest-landed` row):

- COPY external path/URL artifacts into raw/ (original left intact, Hard Rule 2).
- RELOCATE already-in-vault / promoted artifacts to canonical raw/<name>.<ext>,
  rewriting any in-note embed to a link.
- Binary -> two-file raw entity: immutable binary raw/<name>.<ext> + markdown
  shell raw/<name>.md, SHARED base name.
- Provisional minimal stub: frontmatter with `type:` placeholder + `created:`
  (deterministic landing date) + extracted-text body.
- Deterministic extractor adapters only: PDF text-layer, code-as-is,
  web-readability. Scanned/no-text-layer PDF -> deterministic-empty body,
  NO OCR, NO crash.
- Emit exactly ONE F3 line `[YYYY-MM-DD HH:MM] ingest-landed | <path>` appended
  to vault-root log.md, matching ^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] ingest-landed \| .
- Idempotent / safe: re-running does not double-append or corrupt.
- Zero LLM tokens.
"""

import io
import os
import re
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import ingest_landing as il  # noqa: E402

F3_INGEST_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] ingest-landed \| ")


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for sub in ("raw", "raw/daily-work", "raw/daily-journal",
                "raw/writing", "raw/discussions", "schema", "_system", "wiki"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    (vault / "log.md").write_text("", encoding="utf-8")
    return vault


def _make_text_pdf(path: Path, text: str) -> None:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(text.splitlines() or [text]):
        c.drawString(72, 720 - 14 * i, line)
    c.save()
    path.write_bytes(buf.getvalue())


def _make_image_only_pdf(path: Path) -> None:
    """A PDF with no text layer (a drawn rectangle only) -> scanned-like."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.rect(72, 600, 200, 120, fill=1)
    c.save()
    path.write_bytes(buf.getvalue())


def _read_log_lines(vault: Path):
    return [ln for ln in (vault / "log.md").read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# PDF with text layer
# ---------------------------------------------------------------------------

def test_pdf_with_text_layer_lands_binary_shell_and_one_log_line(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "external" / "Sample Paper.pdf"
    src.parent.mkdir(parents=True)
    _make_text_pdf(src, "Hello deterministic world\nSecond line of text")

    result = il.land(str(src), str(vault))

    # binary + shell at SHARED base name in raw/
    binary = vault / "raw" / "Sample Paper.pdf"
    shell = vault / "raw" / "Sample Paper.md"
    assert binary.exists(), "immutable binary must land in raw/"
    assert shell.exists(), "markdown shell must land in raw/ with shared base name"
    assert binary.stem == shell.stem == "Sample Paper"

    # original left intact (COPY for external)
    assert src.exists(), "external original must be left intact (Hard Rule 2)"

    # deterministic extracted body
    body = shell.read_text(encoding="utf-8")
    assert "Hello deterministic world" in body
    assert "Second line of text" in body

    # provisional frontmatter: type placeholder + created
    assert body.startswith("---\n")
    fm = body.split("---\n", 2)[1]
    assert re.search(r"^type:\s*", fm, re.M)
    assert re.search(r"^created:\s*\d{4}-\d{2}-\d{2}\s*$", fm, re.M)

    # exactly one F3 ingest-landed line
    lines = _read_log_lines(vault)
    landed = [ln for ln in lines if "ingest-landed" in ln]
    assert len(landed) == 1, f"expected exactly one ingest-landed line, got {landed}"
    assert F3_INGEST_RE.match(landed[0]), f"log line not F3-shaped: {landed[0]!r}"
    # primary-target = the landed path, no key:value pairs (schema: keys = none)
    assert landed[0].rstrip().endswith("Sample Paper.md") or \
        landed[0].rstrip().endswith("Sample Paper.pdf")
    assert result["log_line"] == landed[0]
    assert result["llm_tokens"] == 0


# ---------------------------------------------------------------------------
# Code / text file
# ---------------------------------------------------------------------------

def test_code_file_landing_stores_body_as_is(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "external" / "snippet.py"
    src.parent.mkdir(parents=True)
    code = "def f(x):\n    return x * 2  # pipe | not escaped here\n"
    src.write_text(code, encoding="utf-8")

    il.land(str(src), str(vault))

    binary = vault / "raw" / "snippet.py"
    shell = vault / "raw" / "snippet.md"
    assert binary.exists() and shell.exists()
    body = shell.read_text(encoding="utf-8")
    # code stored as-is, verbatim, inside the body
    assert code.strip() in body
    assert src.exists(), "external original untouched"
    landed = [ln for ln in _read_log_lines(vault) if "ingest-landed" in ln]
    assert len(landed) == 1 and F3_INGEST_RE.match(landed[0])


# ---------------------------------------------------------------------------
# URL / web readability
# ---------------------------------------------------------------------------

def test_url_web_landing_extracts_readable_text(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    html = b"""<html><head><title>My Article</title>
        <style>.x{color:red}</style></head>
        <body><nav>nav junk</nav>
        <article><h1>Main Heading</h1>
        <p>First real paragraph of content.</p>
        <script>var x=1;</script>
        <p>Second real paragraph.</p></article>
        <footer>footer junk</footer></body></html>"""

    def fake_fetch(url):
        return html, "My Article.html"

    monkeypatch.setattr(il, "_http_fetch", fake_fetch)

    il.land("https://example.com/my-article", str(vault))

    shell = vault / "raw" / "My Article.md"
    binary = vault / "raw" / "My Article.html"
    assert shell.exists(), "web shell must exist"
    assert binary.exists(), "raw fetched HTML must be stored verbatim as the immutable artifact"
    body = shell.read_text(encoding="utf-8")
    assert "First real paragraph of content." in body
    assert "Second real paragraph." in body
    # readability strips script/style/nav/footer noise
    assert "var x=1" not in body
    assert "color:red" not in body
    landed = [ln for ln in _read_log_lines(vault) if "ingest-landed" in ln]
    assert len(landed) == 1 and F3_INGEST_RE.match(landed[0])


# ---------------------------------------------------------------------------
# Copy vs relocate branching
# ---------------------------------------------------------------------------

def test_external_path_is_copied_original_intact(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "elsewhere" / "doc.txt"
    src.parent.mkdir(parents=True)
    src.write_text("external content", encoding="utf-8")

    res = il.land(str(src), str(vault))

    assert src.exists(), "COPY: external original must remain"
    assert (vault / "raw" / "doc.txt").exists()
    assert res["mode"] == "copy"


def test_in_vault_artifact_is_relocated_and_embed_rewritten(tmp_path):
    vault = _make_vault(tmp_path)
    # artifact pasted inside a daily note, embedded
    asset = vault / "raw" / "daily-work" / "assets" / "2026-05" / "20260518120000.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    note = vault / "raw" / "daily-work" / "2026-05-18.md"
    note.write_text(
        "Notes for today\n\n"
        "![[raw/daily-work/assets/2026-05/20260518120000.png]]\n\n"
        "more text\n",
        encoding="utf-8",
    )

    res = il.land(
        str(asset), str(vault),
        mode="relocate",
        embed_in=str(note),
        dest_name="pasted-diagram",
    )

    assert res["mode"] == "relocate"
    # original moved out of the assets bucket
    assert not asset.exists(), "RELOCATE must move the in-vault artifact"
    assert (vault / "raw" / "pasted-diagram.png").exists()
    assert (vault / "raw" / "pasted-diagram.md").exists()
    # in-note embed rewritten to a LINK (not an embed)
    note_txt = note.read_text(encoding="utf-8")
    assert "![[raw/daily-work/assets/2026-05/20260518120000.png]]" not in note_txt
    assert "[[raw/pasted-diagram.md]]" in note_txt or "[[raw/pasted-diagram]]" in note_txt
    assert not note_txt.count("![[")  # the embed bang must be gone


# ---------------------------------------------------------------------------
# Scanned PDF: deterministic-empty body, no OCR, no crash
# ---------------------------------------------------------------------------

def test_scanned_pdf_deterministic_empty_body_no_ocr_no_crash(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "external" / "scanned.pdf"
    src.parent.mkdir(parents=True)
    _make_image_only_pdf(src)

    res = il.land(str(src), str(vault))  # must NOT raise

    shell = vault / "raw" / "scanned.md"
    assert shell.exists()
    body = shell.read_text(encoding="utf-8")
    # recorded as deterministic-empty, NOT a failure, NOT OCR'd
    assert res["extraction"] == "deterministic-empty"
    assert "<!-- ingest: deterministic extraction produced no text layer" in body
    landed = [ln for ln in _read_log_lines(vault) if "ingest-landed" in ln]
    assert len(landed) == 1 and F3_INGEST_RE.match(landed[0])


# ---------------------------------------------------------------------------
# Idempotency / safety
# ---------------------------------------------------------------------------

def test_rerun_does_not_double_append_or_corrupt(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "external" / "snippet.py"
    src.parent.mkdir(parents=True)
    src.write_text("print('x')\n", encoding="utf-8")

    il.land(str(src), str(vault))
    first = (vault / "log.md").read_text(encoding="utf-8")
    first_shell = (vault / "raw" / "snippet.md").read_text(encoding="utf-8")

    # re-run the SAME landing
    il.land(str(src), str(vault))
    second = (vault / "log.md").read_text(encoding="utf-8")
    second_shell = (vault / "raw" / "snippet.md").read_text(encoding="utf-8")

    landed = [ln for ln in second.splitlines() if "ingest-landed" in ln]
    assert len(landed) == 1, "re-running the same landing must NOT double-append"
    assert first == second, "log.md must be unchanged on idempotent re-run"
    assert second_shell == first_shell, "immutable shell must not be rewritten"


def test_log_line_format_strict_regex(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "external" / "a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("hi", encoding="utf-8")
    res = il.land(str(src), str(vault))
    line = res["log_line"]
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] ingest-landed \| ", line)
    # schema/log-events.md: keys column = none -> no ' key:value' trailer,
    # and the F3 separator after the primary-target must not introduce raw pipes
    after_anchor = line.split("ingest-landed | ", 1)[1]
    assert "|" not in after_anchor, "ingest-landed has no key:value pairs; target must not contain raw pipes"


def test_append_only_preserves_existing_log_content(tmp_path):
    vault = _make_vault(tmp_path)
    (vault / "log.md").write_text(
        "[2026-05-01 09:00] eod-summary | daily-work/2026-05-01 | proposed:1 approved:1 modified:0 rejected:0\n",
        encoding="utf-8",
    )
    src = tmp_path / "external" / "b.txt"
    src.parent.mkdir(parents=True)
    src.write_text("hi", encoding="utf-8")
    il.land(str(src), str(vault))
    content = (vault / "log.md").read_text(encoding="utf-8")
    assert content.startswith("[2026-05-01 09:00] eod-summary | "), "must be append-only, never rotate/rewrite"
    assert "ingest-landed" in content


# ---------------------------------------------------------------------------
# FIX M1 — the emitted <path> is the raw SHELL path via the single-source fn
# ---------------------------------------------------------------------------

def test_landed_path_is_shell_path_via_single_source_function(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "external" / "Sample Paper.pdf"
    src.parent.mkdir(parents=True)
    _make_text_pdf(src, "Body text here")

    res = il.land(str(src), str(vault))

    # the documented single source of truth Task 4.3 reuses
    assert hasattr(il, "raw_shell_logpath"), \
        "M1: a single named path-derivation function must exist for Task 4.3 reuse"
    expected = il.raw_shell_logpath(vault, vault / "raw" / "Sample Paper.md")
    assert expected == "raw/Sample Paper.md", \
        f"M1: shell logpath must be the posix raw/<name>.md flat-list/index.md entry, got {expected!r}"

    landed = [ln for ln in _read_log_lines(vault) if "ingest-landed" in ln]
    assert len(landed) == 1
    target = landed[0].split("ingest-landed | ", 1)[1].rstrip()
    assert target == "raw/Sample Paper.md", \
        f"M1: emitted <path> must be the SHELL path (§5.2a flat-list entry), got {target!r}"
    assert target == expected, "M1: emitted token must come from raw_shell_logpath"

    # contract docstring surfaced (delta-1 Cluster C / §15.2 prerequisite)
    doc = il.__doc__ or ""
    assert "ingest-complete" in doc and "orphan-sweep" in doc and "§10.2" in doc, \
        "M1: module docstring must state the cross-task <path> contract"


# ---------------------------------------------------------------------------
# FIX M2.1 — copy re-invocation of the SAME artifact is a safe no-op
# ---------------------------------------------------------------------------

def test_copy_reinvocation_same_artifact_is_noop(tmp_path):
    vault = _make_vault(tmp_path)
    src = tmp_path / "external" / "doc.txt"
    src.parent.mkdir(parents=True)
    src.write_text("same content", encoding="utf-8")

    r1 = il.land(str(src), str(vault))
    log1 = (vault / "log.md").read_text(encoding="utf-8")
    shell1 = (vault / "raw" / "doc.md").read_text(encoding="utf-8")

    r2 = il.land(str(src), str(vault))  # must NOT crash, NOT double-append
    log2 = (vault / "log.md").read_text(encoding="utf-8")
    shell2 = (vault / "raw" / "doc.md").read_text(encoding="utf-8")

    landed = [ln for ln in log2.splitlines() if "ingest-landed" in ln]
    assert len(landed) == 1, "copy re-invocation of same artifact must not double-append"
    assert log1 == log2, "log.md unchanged on idempotent copy re-run"
    assert shell1 == shell2, "immutable shell not rewritten on idempotent copy re-run"
    assert r2["raw_shell"] == r1["raw_shell"]


# ---------------------------------------------------------------------------
# FIX M2.1 — relocate re-invocation is a safe no-op (no crash, no double-append)
# ---------------------------------------------------------------------------

def test_relocate_reinvocation_is_safe_noop(tmp_path):
    vault = _make_vault(tmp_path)
    asset = vault / "raw" / "daily-work" / "assets" / "2026-05" / "20260518120000.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    note = vault / "raw" / "daily-work" / "2026-05-18.md"
    note.write_text(
        "Notes\n\n![[raw/daily-work/assets/2026-05/20260518120000.png]]\n\nmore\n",
        encoding="utf-8",
    )

    r1 = il.land(str(asset), str(vault), mode="relocate",
                 embed_in=str(note), dest_name="pasted-diagram")
    log1 = (vault / "log.md").read_text(encoding="utf-8")
    shell1 = (vault / "raw" / "pasted-diagram.md").read_text(encoding="utf-8")
    assert not asset.exists(), "first relocate moved the source"

    # source already moved away -> must be a safe no-op, NOT FileNotFoundError
    r2 = il.land(str(asset), str(vault), mode="relocate",
                 embed_in=str(note), dest_name="pasted-diagram")
    log2 = (vault / "log.md").read_text(encoding="utf-8")
    shell2 = (vault / "raw" / "pasted-diagram.md").read_text(encoding="utf-8")

    landed = [ln for ln in log2.splitlines() if "ingest-landed" in ln]
    assert len(landed) == 1, "relocate re-invocation must NOT double-append"
    assert log1 == log2, "log.md unchanged on idempotent relocate re-run"
    assert shell1 == shell2, "immutable shell not rewritten on relocate re-run"
    assert r2["raw_shell"] == r1["raw_shell"]


# ---------------------------------------------------------------------------
# FIX M2.2 — genuine base-name collision, DIFFERENT content: never overwrite
# the existing immutable raw entity (§5.5); deterministic numeric suffix.
# ---------------------------------------------------------------------------

def test_genuine_name_collision_different_content_suffixes_no_overwrite(tmp_path):
    vault = _make_vault(tmp_path)
    src1 = tmp_path / "a" / "report.txt"
    src1.parent.mkdir(parents=True)
    src1.write_text("FIRST artifact bytes", encoding="utf-8")
    src2 = tmp_path / "b" / "report.txt"
    src2.parent.mkdir(parents=True)
    src2.write_text("SECOND different artifact bytes", encoding="utf-8")

    r1 = il.land(str(src1), str(vault))
    orig_bin = vault / "raw" / "report.txt"
    orig_shell = vault / "raw" / "report.md"
    assert orig_bin.read_text(encoding="utf-8") == "FIRST artifact bytes"

    r2 = il.land(str(src2), str(vault))

    # §5.5: the existing immutable raw entity must be byte-intact, never clobbered
    assert orig_bin.read_text(encoding="utf-8") == "FIRST artifact bytes", \
        "§5.5 VIOLATION: existing immutable raw binary was overwritten"
    assert "FIRST artifact bytes" in orig_shell.read_text(encoding="utf-8"), \
        "§5.5 VIOLATION: existing immutable raw shell was overwritten"

    # the second landed to a deterministic numeric-suffixed entity
    assert r2["raw_binary"] != r1["raw_binary"]
    sfx_bin = Path(r2["raw_binary"])
    sfx_shell = Path(r2["raw_shell"])
    assert sfx_bin.exists() and sfx_shell.exists()
    assert sfx_bin.stem == sfx_shell.stem and sfx_bin.stem != "report", \
        "collision must produce a distinct suffixed shared base name"
    assert "SECOND different artifact bytes" in sfx_bin.read_text(encoding="utf-8")

    # deterministic: same inputs -> same suffixed destination
    r3 = il.land(str(src2), str(vault))
    assert r3["raw_binary"] == r2["raw_binary"], \
        "suffix must be deterministic (same inputs -> same result)"

    # exactly two distinct ingest-landed lines (new + collision), no extras
    landed = [ln for ln in _read_log_lines(vault) if "ingest-landed" in ln]
    assert len(landed) == 2, f"expected 2 distinct landed lines, got {landed}"
    targets = sorted(ln.split("ingest-landed | ", 1)[1].rstrip() for ln in landed)
    assert targets == sorted({"raw/report.md", sfx_shell.relative_to(vault).as_posix()})


# ---------------------------------------------------------------------------
# FIX m1 — exact-token F3 prefix match (no `xingest-landed` false positive)
# ---------------------------------------------------------------------------

def test_exact_token_f3_match_no_false_positive(tmp_path):
    vault = _make_vault(tmp_path)
    # a decoy line whose event-kind merely ENDS WITH "ingest-landed"
    (vault / "log.md").write_text(
        "[2026-05-01 09:00] xingest-landed | raw/a.md\n",
        encoding="utf-8",
    )
    src = tmp_path / "external" / "a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("hi", encoding="utf-8")

    # rel target collides with the decoy's target token on purpose
    res = il.land(str(src), str(vault), dest_name="a")
    content = (vault / "log.md").read_text(encoding="utf-8")
    real = [ln for ln in content.splitlines()
            if F3_INGEST_RE.match(ln)]
    assert len(real) == 1, \
        "m1: a genuine ingest-landed line must still be written despite the xingest-landed decoy"
    assert content.startswith("[2026-05-01 09:00] xingest-landed | raw/a.md"), \
        "decoy line preserved (append-only)"
