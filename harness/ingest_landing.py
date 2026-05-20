r"""Task 2.1 — zero-token deterministic ingest-landing harness.

Split-responsibility intake contract (new-vault-plan.md §10.2 / 5d Q1):

  The harness (THIS module) does, with ZERO LLM tokens:
    1. Move the artifact into raw/ (COPY external/URL, RELOCATE in-vault).
    2. Write a PROVISIONAL minimal raw stub (frontmatter + extracted-text body).
    3. Run deterministic extractor adapters (PDF text-layer, code-as-is,
       web-readability — adapters.py).
    4. Append exactly ONE F3 `ingest-landed | <path>` line to vault-root log.md.

  Explicitly NOT here (Task 4.3 LLM ingest skill):
    - descriptive rename-in-place, `type:`/`source-kind:` inference,
      frontmatter completion, LLM-fallback adapters (OCR/caption/transcript),
      the Phase-1 wiki source-summary stub, `ingest-complete`, index.md
      registration.

Contracts honored:
  - §5.2(a): ingested-source binary co-located in raw/ beside the markdown
    shell with a SHARED BASE NAME -> raw/<name>.<ext> + raw/<name>.md.
  - §5.1 / schema/frontmatter.md: provisional stub carries `type:` (placeholder,
    LLM completes) + `created:` (deterministic landing date). Not over-authored.
  - §9.1 F3: `[YYYY-MM-DD HH:MM] <event-kind> | <primary-target> | …`.
    schema/log-events.md `ingest-landed` row: emitter = zero-token harness,
    primary-target = <path>, keys = none. So the emitted line is exactly
    `[YYYY-MM-DD HH:MM] ingest-landed | <path>` (no key:value trailer).
  - Rot-A: log.md is append-only; never rotate/compact/prune.
  - Task 1.5 carry-forward: any log.md read splits on UNESCAPED pipes only
    (regex (?<!\\)\|); the emitted target contains no raw pipes.

CROSS-TASK <path> CONTRACT (M1 — surfaced, not redesigned):

  `ingest-landed` primary-target `<path>` = the raw markdown SHELL path
  `raw/<name>.md` (the canonical flat-list/`index.md` entry per §5.2a).
  Task 4.3's `ingest-complete` MUST emit the identical `<path>` token (same
  shell path) or the §10.2 orphan-sweep backstop silently never matches —
  this is the delta-1 Cluster C / §15.2 correctness-prerequisite.

  The shell (not the binary) is the emitted token because §5.2(a) makes the
  shell the flat-list/`index.md` entry and §10.2's orphan-sweep reconciles the
  external-`raw/` list (= index/shell entries) against `ingest-complete`.
  `raw_shell_logpath()` below is the SINGLE SOURCE OF TRUTH for this
  derivation; Task 4.3 MUST reuse it (do not re-derive the token).

§5.5 RAW IMMUTABILITY + DETERMINISM (M2):

  Raw is immutable except the two audited carve-outs of §5.5 (`file-removed`,
  `raw-reextract`) — neither is "overwrite". The harness therefore NEVER
  overwrites an existing raw entity. Re-invoking land() on an already-landed
  artifact (same content) is a deterministic safe NO-OP (no re-copy, no second
  `ingest-landed` line) for BOTH copy and relocate. A genuine base-name
  collision with DIFFERENT content is disambiguated with a deterministic
  numeric suffix — the §5.4 collision precedent ("same-day/same-topic
  collisions get a numeric suffix"), the only corpus collision precedent —
  applied analogously to `raw/<name>.<ext>` + `raw/<name>.md`.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path
from typing import Optional

from .adapters import select_adapter

# Split on UNESCAPED pipes only (Task 1.5 carry-forward constraint).
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")

# m1: exact-token F3 prefix for `ingest-landed` lines. Anchored on the full
# `[YYYY-MM-DD HH:MM] ingest-landed | ` prefix so a hypothetical
# `xingest-landed` event-kind can never be mistaken for ours.
_F3_INGEST_LANDED = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] ingest-landed \| ")

_BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                    ".mp3", ".mp4", ".wav", ".mov", ".m4a", ".docx",
                    ".xlsx", ".pptx", ".zip"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_stamp() -> str:
    """F3 timestamp `YYYY-MM-DD HH:MM` (local, deterministic format)."""
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _today() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def _is_url(artifact: str) -> bool:
    return artifact.lower().startswith(("http://", "https://"))


def _http_fetch(url: str):
    """Fetch a URL. Returns (raw_bytes, suggested_filename).

    Tests monkeypatch this; real use does a deterministic urllib GET (no LLM).
    """
    import urllib.request
    from urllib.parse import urlparse, unquote

    req = urllib.request.Request(url, headers={"User-Agent": "brain-dump-harness/2.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (explicit http/https)
        data = resp.read()
    path = urlparse(url).path
    base = unquote(Path(path).name) or urlparse(url).netloc or "web-page"
    if not base.lower().endswith((".html", ".htm")):
        base = base + ".html"
    return data, base


def _split_unescaped(line: str):
    """Split a log.md F3 line on UNESCAPED pipes only (treat \\| as literal)."""
    return _UNESCAPED_PIPE.split(line)


def _build_stub(*, ext: str, created: str, adapter_name: str,
                extraction_status: str, body_text: str) -> str:
    """Provisional MINIMAL raw stub (frontmatter + extracted-text body shell).

    Deliberately under-authored: only `type:` placeholder + `created:` (§5.1 /
    schema/frontmatter.md). The Task 4.3 LLM skill completes `type:`, infers
    `source-kind:`, renames, and writes the Phase-1 wiki stub — NOT here.
    """
    fm = (
        "---\n"
        "type:\n"                       # placeholder — Task 4.3 LLM infers
        f"created: {created}\n"
        "---\n"
    )
    header = (
        f"<!-- provisional raw stub written by the zero-token ingest-landing "
        f"harness (Task 2.1). type:/rename/source-summary are Task 4.3. "
        f"extractor adapter: {adapter_name}; extraction: {extraction_status} -->\n\n"
    )
    if extraction_status == "deterministic-empty":
        body = (
            "<!-- ingest: deterministic extraction produced no text layer "
            f"(adapter={adapter_name}). NOT a failure: no OCR performed by the "
            "zero-token harness; the Task 4.3 LLM-fallback adapter owns "
            "recovery. -->\n"
        )
    else:
        # Fenced so verbatim code/text (incl. literal pipes) is body content,
        # never re-parsed as markdown/log structure.
        body = "## Extracted text\n\n```\n" + body_text.rstrip("\n") + "\n```\n"
    return fm + header + body


def raw_shell_logpath(vault, shell_path) -> str:
    """SINGLE SOURCE OF TRUTH for the `ingest-landed` / `ingest-complete`
    primary-target `<path>` token (M1 — surfaced §5.2a convention, not new
    design).

    Returns the vault-relative POSIX path of the raw markdown SHELL —
    `raw/<name>.md` — which §5.2(a) defines as the canonical flat-list /
    `index.md` entry and which §10.2's orphan-sweep reconciles against
    `ingest-complete`. Task 4.3 MUST call THIS function (never re-derive the
    token) so the two events emit an identical token (delta-1 Cluster C /
    §15.2 correctness-prerequisite).
    """
    return Path(shell_path).relative_to(Path(vault)).as_posix()


def _same_artifact(raw_binary: Path, data: bytes) -> bool:
    """True iff an already-landed raw binary holds byte-identical content to
    the would-be artifact. The §5.5 immutability test for "is this the SAME
    artifact (safe no-op) vs. a genuine different-content collision"."""
    try:
        return raw_binary.exists() and raw_binary.read_bytes() == data
    except OSError:
        return False


def _unique_dest(raw_dir: Path, base: str, ext: str, data: bytes):
    """Resolve the shared-base-name `raw/<name>.<ext>` + `raw/<name>.md`
    destination, enforcing §5.5 raw immutability deterministically.

    Behavior (corpus-grounded; §5.5 immutability + §5.4 numeric-suffix
    collision precedent — the only corpus collision precedent — applied
    analogously to ingested raw entities):

      1. Base `raw/<base>.<ext>` free            -> use it.
      2. Base taken AND its binary is byte-identical to `data` (the SAME
         artifact already landed)                -> reuse it (caller makes
         this a safe NO-OP; raw is never re-copied/rewritten).
      3. Base taken by a DIFFERENT-content raw entity (genuine collision)
         -> NEVER overwrite the existing immutable entity (§5.5). Walk a
         deterministic numeric suffix `raw/<base>-1.<ext>`, `-2`, … until a
         free slot OR a slot already holding the SAME artifact is found.
         Deterministic: identical inputs always yield the identical slot.

    Returns (binary_path, shell_path) with a shared base name.
    """
    def pair(name: str):
        return raw_dir / f"{name}{ext}", raw_dir / f"{name}.md"

    cand_bin, cand_shell = pair(base)
    if not cand_bin.exists():
        return cand_bin, cand_shell
    if _same_artifact(cand_bin, data):
        return cand_bin, cand_shell  # same artifact -> caller no-ops

    # Genuine different-content collision: deterministic numeric suffix,
    # never clobbering the existing immutable raw entity (§5.5).
    n = 1
    while True:
        cand_bin, cand_shell = pair(f"{base}-{n}")
        if not cand_bin.exists() or _same_artifact(cand_bin, data):
            return cand_bin, cand_shell
        n += 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def land(artifact: str,
         vault_root: str,
         *,
         mode: Optional[str] = None,
         embed_in: Optional[str] = None,
         dest_name: Optional[str] = None) -> dict:
    """Land an artifact into the vault's raw/ deterministically.

    Parameters
    ----------
    artifact   : external path, in-vault path, or http(s) URL.
    vault_root : the Brain-Dump vault root (operated ON, never the code home).
    mode       : "copy" (external/URL — original intact, Hard Rule 2) or
                 "relocate" (already-in-vault / promoted — move + rewrite
                 in-note embed to a link). Auto-detected when None.
    embed_in   : (relocate only) markdown note whose embed of this artifact
                 is rewritten from `![[…]]` to a `[[…]]` link.
    dest_name  : optional explicit base name for the landed entity. NOTE: this
                 is a deterministic caller-supplied name (e.g. from `promote`),
                 NOT an LLM descriptive rename (that is Task 4.3).

    Returns a dict: {mode, raw_binary, raw_shell, log_line, extraction,
                     adapter, llm_tokens}.
    """
    vault = Path(vault_root)
    raw_dir = vault / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = vault / "log.md"

    url = _is_url(artifact)
    if mode is None:
        if url:
            mode = "copy"
        else:
            try:
                Path(artifact).resolve().relative_to(vault.resolve())
                mode = "relocate"
            except ValueError:
                mode = "copy"

    # ---- acquire bytes + base name + ext --------------------------------
    src = None
    if url:
        data, suggested = _http_fetch(artifact)
        base = dest_name or Path(suggested).stem
        ext = Path(suggested).suffix or ".html"
        suffix_for_adapter = ext
    else:
        src = Path(artifact)
        if src.exists():
            data = src.read_bytes()
            base = dest_name or src.stem
            ext = src.suffix
            suffix_for_adapter = ext
        else:
            # M2.1: the source is gone. Before raising, check whether this is
            # an already-completed RELOCATE (source moved into raw/ on a prior
            # invocation). If a raw entity for this exact base name already
            # exists, the landing happened — return it as a safe NO-OP (no
            # crash, no re-copy, no double-append; §5.5 + determinism). Only a
            # genuinely-absent artifact (no prior landing) is an error.
            base = dest_name or src.stem
            ext = src.suffix
            prior_bin = raw_dir / f"{base}{ext}"
            prior_shell = raw_dir / f"{base}.md"
            if prior_bin.exists() and prior_shell.exists():
                rel = raw_shell_logpath(vault, prior_shell)
                existing = _existing_landed_line(log_path, rel)
                return {
                    "mode": mode,
                    "raw_binary": str(prior_bin),
                    "raw_shell": str(prior_shell),
                    "log_line": existing
                    or f"[{_now_stamp()}] ingest-landed | {rel}",
                    "extraction": "already-landed",
                    "adapter": "none",
                    "llm_tokens": 0,
                }
            raise FileNotFoundError(f"artifact not found: {artifact}")

    # _unique_dest enforces §5.5: reuse the slot iff it already holds the SAME
    # artifact (-> safe no-op below); otherwise a free / deterministically
    # numeric-suffixed slot, NEVER overwriting an existing immutable entity.
    raw_binary, raw_shell = _unique_dest(raw_dir, base, ext, data)

    # ---- idempotency / safety guard -------------------------------------
    # If this exact landing already happened (shell + binary present AND the
    # binary is byte-identical to this artifact), do NOT re-extract, re-write,
    # or double-append. Raw is immutable once landed (§5.5).
    already_landed = (raw_shell.exists() and raw_binary.exists()
                      and _same_artifact(raw_binary, data))

    # ---- deterministic extraction (zero LLM tokens) ---------------------
    adapter = select_adapter(suffix=suffix_for_adapter, is_url=url, data=data)
    result = adapter.extract(data)

    # ---- place the immutable binary -------------------------------------
    if not already_landed:
        if url:
            raw_binary.write_bytes(data)            # store fetched bytes verbatim
        elif mode == "copy":
            shutil.copy2(src, raw_binary)            # external original intact
        elif mode == "relocate":
            shutil.move(str(src), str(raw_binary))   # in-vault: move
        else:
            raise ValueError(f"unknown mode: {mode!r}")

        # ---- write the provisional shell (shared base name) -------------
        stub = _build_stub(
            ext=ext,
            created=_today(),
            adapter_name=adapter.name,
            extraction_status=result.status,
            body_text=result.text,
        )
        # newline="" -> write \n verbatim (deterministic, platform-stable;
        # no \r\n translation that would corrupt verbatim code bodies).
        with raw_shell.open("w", encoding="utf-8", newline="") as fh:
            fh.write(stub)

        # ---- rewrite in-note embed -> link (relocate only) --------------
        if mode == "relocate" and embed_in:
            _rewrite_embed_to_link(Path(embed_in), artifact, vault, raw_shell)

    # ---- emit exactly ONE F3 ingest-landed line -------------------------
    # primary-target = the raw SHELL path via the single source of truth
    # (M1 — §5.2a flat-list/index.md entry; Task 4.3 reuses raw_shell_logpath).
    rel = raw_shell_logpath(vault, raw_shell)
    log_line = f"[{_now_stamp()}] ingest-landed | {rel}"
    _append_log_once(log_path, log_line, rel)

    return {
        "mode": mode,
        "raw_binary": str(raw_binary),
        "raw_shell": str(raw_shell),
        "log_line": _existing_landed_line(log_path, rel) or log_line,
        "extraction": result.status,
        "adapter": adapter.name,
        "llm_tokens": 0,
    }


# ---------------------------------------------------------------------------
# log.md (append-only, Rot-A) + idempotency
# ---------------------------------------------------------------------------

def _existing_landed_line(log_path: Path, rel_target: str) -> Optional[str]:
    """Return a pre-existing `ingest-landed` line for this target, if any.

    Reads log.md splitting on UNESCAPED pipes only (Task 1.5 carry-forward).
    m1: the event-kind is matched with the EXACT F3 prefix
    `^\\[ts\\] ingest-landed \\| ` (not `endswith("ingest-landed")`) so a
    hypothetical `xingest-landed` event-kind can never false-match ours.
    """
    if not log_path.exists():
        return None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        # m1: exact-token F3 prefix anchor (no `xingest-landed` false positive).
        if not _F3_INGEST_LANDED.match(line):
            continue
        parts = [p.strip() for p in _split_unescaped(line)]
        # F3: "[ts] ingest-landed " | " <target> " (split on unescaped pipes).
        if len(parts) >= 2 and parts[1] == rel_target:
            return line
    return None


def _append_log_once(log_path: Path, log_line: str, rel_target: str) -> None:
    """Append the F3 line, but never double-append the same landing (Rot-A:
    append-only, never rewrite/rotate existing content)."""
    if _existing_landed_line(log_path, rel_target) is not None:
        return  # idempotent: this landing is already recorded
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
    with log_path.open("a", encoding="utf-8", newline="") as fh:
        fh.write(sep + log_line + "\n")


# ---------------------------------------------------------------------------
# Embed -> link rewrite (relocate)
# ---------------------------------------------------------------------------

def _rewrite_embed_to_link(note: Path, original_artifact: str,
                           vault: Path, raw_shell: Path) -> None:
    """Rewrite the in-note Obsidian embed of the relocated artifact to a LINK
    pointing at the new raw shell (drop the `!` embed bang)."""
    if not note.exists():
        return
    txt = note.read_text(encoding="utf-8")
    shell_rel = raw_shell.relative_to(vault).as_posix()      # raw/<name>.md
    shell_link = shell_rel[:-3] if shell_rel.endswith(".md") else shell_rel

    # Match any ![[ ... ]] whose path resolves to the moved artifact (match on
    # the original file name, robust to vault-relative vs. absolute embeds).
    orig_name = Path(original_artifact).name

    def _repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        if Path(inner.split("|")[0]).name == orig_name or orig_name in inner:
            return f"[[{shell_link}]]"
        return m.group(0)

    new_txt = re.sub(r"!\[\[([^\]]+)\]\]", _repl, txt)
    if new_txt != txt:
        note.write_text(new_txt, encoding="utf-8")
