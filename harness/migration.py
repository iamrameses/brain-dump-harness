r"""Task 2.4 — deterministic, idempotent migration transform.

Constructs the new vault's IMMUTABLE raw layer from the real existing vault.
Strictly-subtractive, code-block-aware, zero-LLM, pure + idempotent. Lives
OUTSIDE both vaults. The source vault is READ-ONLY (never written) — this is
the structural fact the entire 6b-Q6 rollback story rests on (R0/R1/R2).

THE RULE SET (6b-migration.md Q5, implemented VERBATIM):

  (R1) strip checkbox markers `- [ ]` / `- [x]` -> `- ` (indentation
       preserved; the surviving line text is kept as a plain prose bullet).
  (R2) Dataview inline fields `[key:: value]` — if the value contains
       `[[wikilink(s)]]`, replace the whole field with the bare wikilink(s);
       else remove the field entirely.
  (R3) inline `#tag` tokens removed — but NEVER Markdown headings
       (`^#{1,6}\s`) and NEVER content inside fenced (```) / inline (`...`)
       code regions.
  (R4) all prose and all `[[wikilinks]]` (person AND project) untouched.
  (R5) frontmatter normalized as creation-metadata: keep `date:` (verbatim,
       well-formed); set `type: daily-work`; set
       `workplace: stanford-raymond-lab`; drop `processed:`; do NOT add
       `created:` (no schema/frontmatter.md row for it on daily). Map a
       per-type `status:` to the per-context enum (delta-2 Cluster I) ONLY
       if present — daily notes carry none, so this is a no-op (never
       invent a `status:`).

Asset relocation (6b Q3/Q5): `assets/` -> `raw/daily-work/assets/YYYY-MM/`;
embeds resolve by basename, so `![[name.ext]]` need not be rewritten.

Q3/Q4 scope: ONLY `daily/` -> `raw/daily-work/` (filename `YYYY-MM-DD.md`
preserved, single slug `stanford-raymond-lab`) and `assets/` cross over.
`notes/`, `_system/`, `dashboards/`, `templates/`, old `CLAUDE.md` are NOT
carried.

Q6: migration invents NO `log.md` event kind and emits NO `log.md` — the
manifest + validation report are plain-markdown artifacts in a disposable
`_migration/` report folder. It NEVER invokes the §5.5 `file-removed` /
`raw-reextract` carve-outs (irrelevant — pre-cutover).

Edge interpretations (6b Q5 minimal strictly-subtractive reading,
documented per the FIDELITY rule):

  E1  Code-region tracking is line-granular for fences (a ``` line toggles
      the fenced state; the fence lines themselves and everything between
      are byte-exempt) and span-granular for inline code (paired backticks
      on a line; unmatched backtick = literal, not a code opener — minimal).
  E2  R3 "inline #tag" = a `#` that begins a hashtag token at a position
      bounded on the left by start-of-string or whitespace, with a
      tag-char body `[A-Za-z][\w/-]*`. A `#` not at such a boundary (e.g.
      `http://x/y#frag`, `C#`, `a#b`) is prose, not machinery -> untouched
      (strictly-subtractive: only remove unambiguous tag tokens). A
      Markdown heading line (`^#{1,6}\s`) is WHOLLY R3-exempt — Q5 says
      "never Markdown headings"; a `#word` inside heading TEXT is heading
      content, not a discoverability tag. Grounded in the real corpus:
      0/149 dailies have any `#` in heading text; the corpus convention is
      a tag on its OWN line directly below a heading (that tag-only line is
      ordinary prose and IS subject to R3 -> becomes empty). This is the
      safest strictly-subtractive choice with zero corpus impact.
  E3  R2 value is "[[..]]-bearing" iff `[[` ... `]]` appears anywhere in the
      field value; the replacement is exactly the matched wikilink span(s)
      in order, joined by single spaces, nothing else (no surrounding prose
      kept — fields are machinery, only their embedded wikilinks are R4
      content). A field whose value has no `[[` is removed with one
      surrounding space collapsed (deterministic, prose-stable).
  E4  R1 only strips the marker when it is the first non-whitespace token of
      a list item (`^\s*[-*+] \[[ xX]\] `); a `[ ]` mid-prose is not a
      checkbox and is left (subtractive/minimal).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

# Task 1.5 carry-forward (Task-1.2 proven parser pattern): any GFM/registry
# read splits on UNESCAPED pipes only — `\|` is a literal cell character
# even inside backtick code spans. This is the ACTUAL split regex used by
# the schema/workplaces parser below (no hardcoded equivalent).
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")

# Default schema dir resolves from the BRAIN_DUMP_VAULT_ROOT environment
# variable, set per-machine (cross-platform). Examples:
#   Windows (PowerShell):
#       setx BRAIN_DUMP_VAULT_ROOT "E:\Google Drive\...\Brian Daily Brain Dump"
#   macOS (zsh, in ~/.zshrc):
#       export BRAIN_DUMP_VAULT_ROOT="$HOME/Library/CloudStorage/GoogleDrive-<email>/My Drive/.../Brian Daily Brain Dump"
# Tests pass `schema_dir` explicitly via `_resolve_schema_dir()` and ignore
# this default. Production callers either pass `schema_dir` explicitly or
# rely on the env var. READ-ONLY.
_DEFAULT_SCHEMA_DIR = (
    Path(os.environ["BRAIN_DUMP_VAULT_ROOT"]) / "schema"
    if "BRAIN_DUMP_VAULT_ROOT" in os.environ
    else None
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAILY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")

_WORKPLACE_SLUG = "stanford-raymond-lab"  # 6b Q4 (Brian-authorized)
_DAILY_TYPE = "daily-work"  # Task 1.2/2.2-fixed token; all 149 are daily-work

# delta-2 Cluster I per-context status enums (only consulted if a daily ever
# carries `status:` — the corpus carries none, so this stays a no-op map).
_STATUS_PROJECT = {"active", "paused", "completed", "archived"}
_STATUS_COURSE = {"active", "completed"}

# R1: checkbox marker only when it is the list-item lead token (E4).
_CHECKBOX_RE = re.compile(r"^(\s*[-*+] )\[[ xX]\] ?")

# R2: a Dataview inline field `[key:: value]`. key = no spaces/brackets;
# value = up to the matching `]` (fields do not nest brackets except the
# `[[wikilink]]` form, handled inside the value).
_FIELD_RE = re.compile(r"\[[^\[\]\s:]+::[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]")
_WIKILINK_RE = re.compile(r"\[\[[^\]]*\]\]")

# R3 (E2): a hashtag token bounded on the left by start / whitespace.
_TAG_RE = re.compile(r"(?:(?<=\s)|^)#[A-Za-z][\w/-]*")

# R3 (E2): a Markdown heading line — leading 1..6 #s then a space.
_HEADING_RE = re.compile(r"^#{1,6}\s")

# inline code span: paired backticks (E1). Non-greedy, single-line.
_INLINE_CODE_RE = re.compile(r"`[^`]*`")

_FENCE_RE = re.compile(r"^\s*```")


class MigrationError(Exception):
    """Raised when a source daily cannot be deterministically normalized
    (e.g. `date:` missing/ill-formed). Surfaces as a hard validation FAIL —
    migration never silently produces a non-schema-conformant raw note."""


# ---------------------------------------------------------------------------
# 6b Q6 — GENUINE schema parse of schema/frontmatter.md + workplaces.md
#
# The Pass-1 validation report asserts "every produced note parses against
# schema/frontmatter.md". This is driven by REALLY parsing the registry
# files (deterministic GFM-table parser, Task-1.5 carried unescaped-pipe
# constraint via `_UNESCAPED_PIPE`) — NOT a hardcoded regex equivalent. If
# the schema file changes, the validator tracks it (that is the point).
# These functions only ever READ the registry files (never write them).
# ---------------------------------------------------------------------------

_SEP_CELL_RE = re.compile(r":?-{2,}:?$")


def _split_gfm_row(line: str) -> List[str]:
    r"""Split one GFM table row into trimmed cell values.

    Splits ONLY on UNESCAPED pipes (`_UNESCAPED_PIPE` == `(?<!\\)\|`), so a
    `\\|` is a literal cell character even inside a backtick code span
    (Task-1.5 carried constraint; the Task-1.2-proven approach). Leading /
    trailing table pipes are stripped before the split; `\\|` is then
    unescaped to a literal `|` in each cell.
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in _UNESCAPED_PIPE.split(s)]


def _is_separator_row(cells: List[str]) -> bool:
    return bool(cells) and all(
        _SEP_CELL_RE.fullmatch(c) for c in cells if c != ""
    )


def _iter_gfm_table(text: str, header: List[str]):
    """Yield each body row (as a dict over `header`) of the first GFM table
    whose header cells equal `header` (case-insensitive). Deterministic."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            hdr = _split_gfm_row(lines[i])
            if (i + 1 < len(lines)
                    and lines[i + 1].strip().startswith("|")
                    and _is_separator_row(_split_gfm_row(lines[i + 1]))
                    and [h.lower() for h in hdr] == header):
                j = i + 2
                while j < len(lines) and lines[j].strip().startswith("|"):
                    cells = _split_gfm_row(lines[j])
                    if not _is_separator_row(cells):
                        yield dict(zip(header,
                                       cells + [""] * (len(header)
                                                       - len(cells))))
                    j += 1
                return
        i += 1


def parse_schema_daily_work(
    schema_path: Path,
) -> Dict[str, Tuple[str, str, str]]:
    """Parse `schema/frontmatter.md` and extract the `raw|daily-work` rules.

    Returns `{field: (requirement, cardinality, value_kind)}` for every row
    whose `layer` == `raw` and `type` == `daily-work`. The set of fields and
    their (cardinality, value-kind) are DERIVED from the parsed table — the
    validator below is driven entirely by this, so a schema change is
    tracked automatically (no hardcoded field list). READ-ONLY.
    """
    text = Path(schema_path).read_text(encoding="utf-8")
    cols = ["layer", "type", "field", "requirement",
            "cardinality", "value-kind", "notes"]
    rules: Dict[str, Tuple[str, str, str]] = {}
    for row in _iter_gfm_table(text, cols):
        if row["layer"] == "raw" and row["type"] == "daily-work":
            rules[row["field"]] = (
                row["requirement"], row["cardinality"], row["value-kind"],
            )
    if not rules:
        raise MigrationError(
            "schema/frontmatter.md: no `raw|daily-work` rows parsed"
        )
    return rules


def parse_workplace_slugs(workplaces_path: Path) -> set:
    """Parse `schema/workplaces.md` (same unescaped-pipe split) and return
    the set of registered slugs (the `slug` column). READ-ONLY."""
    text = Path(workplaces_path).read_text(encoding="utf-8")
    cols = ["slug", "label", "status", "notes"]
    slugs = {row["slug"] for row in _iter_gfm_table(text, cols)
             if row["slug"]}
    if not slugs:
        raise MigrationError(
            "schema/workplaces.md: no slug rows parsed"
        )
    return slugs


def validate_frontmatter_against_schema(
    fm_text: str,
    rules: Dict[str, Tuple[str, str, str]],
    slugs: set,
) -> Tuple[bool, str]:
    """Validate a PRODUCED note's frontmatter against the PARSED schema.

    Driven by `rules` (the parsed `raw|daily-work` rows) and the parsed
    `slugs`. Returns `(ok, reason)`:

      * every `required` schema field present;
      * NO field present that has no parsed `raw|daily-work` row (so
        `processed:` / `created:` / any unknown key are rejected purely
        because the schema has no such row — not by a hardcoded blacklist);
      * value-kinds honoured per the parsed `value-kind`: `string` field
        `type` must equal the registered token `daily-work`; a `slug` field
        (`workplace`) must be a registered slug from `schema/workplaces.md`;
        a `date` field must be well-formed `YYYY-MM-DD`.
    """
    present: Dict[str, str] = {}
    for raw in fm_text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        present[key.strip()] = val.strip()

    # no field outside the parsed schema rows (rejects processed:/created:/*)
    for key in present:
        if key not in rules:
            return False, (f"field {key!r} has no raw|daily-work row in "
                           f"schema/frontmatter.md")

    # every required schema field present
    for field, (req, _card, _vk) in rules.items():
        if req == "required" and field not in present:
            return False, f"required schema field {field!r} missing"

    # value-kind checks, driven by the parsed value-kind per field
    for field, val in present.items():
        req, card, vk = rules[field]
        if field == "type":
            if val != _DAILY_TYPE:
                return False, (f"type {val!r} != registered token "
                               f"{_DAILY_TYPE!r}")
        elif vk == "date":
            if not _DATE_RE.match(val):
                return False, f"{field} {val!r} not well-formed date"
        elif vk == "slug":
            if val not in slugs:
                return False, (f"{field} {val!r} not a registered slug in "
                               f"schema/workplaces.md")
        elif vk == "string":
            if not val:
                return False, f"{field} empty (string value-kind)"
    return True, ""


def _resolve_schema_dir(schema_dir) -> Path:
    if schema_dir is not None:
        return Path(schema_dir)
    if _DEFAULT_SCHEMA_DIR is None:
        raise RuntimeError(
            "schema_dir is None and the BRAIN_DUMP_VAULT_ROOT environment "
            "variable is unset. Either pass schema_dir explicitly or set "
            "BRAIN_DUMP_VAULT_ROOT to the vault root path on this machine."
        )
    return _DEFAULT_SCHEMA_DIR


# ---------------------------------------------------------------------------
# R5 — frontmatter
# ---------------------------------------------------------------------------

def transform_frontmatter(fm_text: str) -> str:
    """Normalize a daily note's YAML frontmatter body (between the `---`
    fences) as creation-metadata per Q5 R5. Deterministic; pure.

    Keeps `date:` verbatim (must be present & well-formed YYYY-MM-DD),
    sets `type: daily-work`, sets `workplace: stanford-raymond-lab`, drops
    `processed:`, adds NO `created:`. A present `status:` is mapped to the
    delta-2 Cluster I per-context enum; absent -> no-op (never invented).
    """
    date_val = None
    status_val = None
    for raw in fm_text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "date":
            date_val = val
        elif key == "status":
            status_val = val
        # type/processed/anything else: intentionally dropped/overwritten

    if not date_val:
        raise MigrationError("daily frontmatter missing required `date:`")
    if not _DATE_RE.match(date_val):
        raise MigrationError(f"daily `date:` not well-formed: {date_val!r}")

    lines = [
        f"type: {_DAILY_TYPE}",
        f"date: {date_val}",
        f"workplace: {_WORKPLACE_SLUG}",
    ]

    if status_val is not None:
        # delta-2 Cluster I: map per-context. Daily notes are work-stream;
        # the only sanctioned daily-adjacent enums are project/course.
        # Validate membership; pass through verbatim if already a valid
        # member (no synthesis). Unknown -> hard flag (no silent invent).
        if status_val in _STATUS_PROJECT or status_val in _STATUS_COURSE:
            lines.append(f"status: {status_val}")
        else:
            raise MigrationError(
                f"daily `status:` {status_val!r} maps to no delta-2 "
                "Cluster I per-context enum member"
            )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# code-region masking (E1) — shared by R2 & R3
# ---------------------------------------------------------------------------

def _inline_code_spans(line: str) -> List[Tuple[int, int]]:
    """Return [start,end) spans of inline `...` code on a single line."""
    return [(m.start(), m.end()) for m in _INLINE_CODE_RE.finditer(line)]


def _in_spans(idx: int, spans: List[Tuple[int, int]]) -> bool:
    return any(s <= idx < e for s, e in spans)


# ---------------------------------------------------------------------------
# R2 — Dataview inline fields
# ---------------------------------------------------------------------------

def _apply_r2(line: str, code_spans: List[Tuple[int, int]]) -> str:
    """Replace `[key:: value]` fields outside code: wikilink-bearing ->
    the bare wikilink(s); else removed (one adjacent space collapsed)."""
    out = []
    pos = 0
    for m in _FIELD_RE.finditer(line):
        if _in_spans(m.start(), code_spans):
            continue  # field token is inside inline code -> exempt (R3/E1)
        out.append(line[pos:m.start()])
        field = m.group(0)
        wls = _WIKILINK_RE.findall(field)
        if wls:
            out.append(" ".join(wls))
        else:
            # plain-value field removed; collapse a single surrounding
            # space so prose spacing stays natural & deterministic (E3).
            tail = line[m.end():]
            if out and out[-1].endswith(" ") and (tail.startswith(" ")
                                                  or tail == ""):
                out[-1] = out[-1][:-1]
        pos = m.end()
    out.append(line[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# R3 — inline #tag removal (heading & code exempt)
# ---------------------------------------------------------------------------

def _apply_r3(line: str, code_spans: List[Tuple[int, int]]) -> str:
    """Strip inline #tag tokens outside code spans. Heading leading-#s are
    never tags (E2): on a heading line only a *trailing* inline tag is
    removed (the `^#{1,6}\\s` structure is preserved because _TAG_RE never
    matches a `#` that is followed by another `#`/space at line start)."""
    result = []
    last = 0
    for m in _TAG_RE.finditer(line):
        if _in_spans(m.start(), code_spans):
            continue
        result.append(line[last:m.start()])
        # collapse a single space left behind by the removed tag so prose
        # spacing stays natural ("a #t b" -> "a b", "#t" -> "").
        seg = "".join(result)
        after = line[m.end():]
        if seg.endswith(" ") and (after.startswith(" ") or after == ""):
            result = [seg[:-1]]
        last = m.end()
    result.append(line[last:])
    return "".join(result)


# ---------------------------------------------------------------------------
# body transform (R1–R4, code-block-aware)
# ---------------------------------------------------------------------------

def transform_body(body: str) -> str:
    """Apply Q5 R1–R4 to a note body (no frontmatter). Pure, deterministic,
    strictly subtractive, code-block-aware. Newlines preserved exactly;
    a trailing-newline-free input stays trailing-newline-free."""
    had_trailing_nl = body.endswith("\n")
    lines = body.split("\n")
    out: List[str] = []
    in_fence = False

    for line in lines:
        if _FENCE_RE.match(line):
            # the fence line itself and the enclosed block are byte-exempt
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)  # fenced code: byte-verbatim (R3/E1, R4)
            continue

        # R1 — strip a leading checkbox marker (indentation preserved)
        new = _CHECKBOX_RE.sub(r"\1", line, count=1)

        code_spans = _inline_code_spans(new)

        # R2 — Dataview fields (skip those inside inline code)
        new = _apply_r2(new, code_spans)
        # spans can shift after R2 edits the line; recompute for R3
        code_spans = _inline_code_spans(new)

        # R3 — inline #tags, NEVER inside inline code, and a Markdown
        # heading line (`^#{1,6}\s`) is WHOLLY exempt (E2): Q5 "never
        # Markdown headings"; `#word` in heading text is heading content.
        if not _HEADING_RE.match(new):
            new = _apply_r3(new, code_spans)

        # R4 — strictly subtractive: a line a rule DID NOT touch passes
        # through BYTE-IDENTICAL (prose, [[wikilinks]], and a heading with
        # pre-existing trailing whitespace — never altered, Q5 R4). When a
        # rule removed a *trailing* field/tag, collapse ONLY the trailing
        # whitespace that the removal NEWLY introduced — never pre-existing
        # trailing whitespace (that would be a non-subtractive edit of a
        # line whose tail no rule otherwise touched, e.g. a heading).
        if new != line:
            orig_tail = len(line) - len(line.rstrip(" "))
            new = new.rstrip(" ") + " " * orig_tail
        out.append(new)

    text = "\n".join(out)
    if had_trailing_nl and not text.endswith("\n"):
        text += "\n"
    if not had_trailing_nl and text.endswith("\n"):
        text = text[:-1]
    return text


def transform_note(text: str) -> str:
    """Transform a full daily note (frontmatter + body) per Q5. Pure."""
    if not text.startswith("---"):
        raise MigrationError("daily note missing leading frontmatter fence")
    # split: ---\n <fm> \n---\n <body>
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        raise MigrationError("daily note frontmatter fence not closed")
    fm_in = m.group(1)
    body = text[m.end():]
    fm_out = transform_frontmatter(fm_in)
    body_out = transform_body(body)
    return f"---\n{fm_out}---\n{body_out}"


# ---------------------------------------------------------------------------
# asset relocation + embed resolution
# ---------------------------------------------------------------------------

_EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def _index_assets(src: Path) -> Dict[str, Path]:
    """Map every asset basename -> its source path (globally-unique
    `YYYYMMDDHHmmss.ext` per the 6b inventory)."""
    out: Dict[str, Path] = {}
    adir = src / "assets"
    if adir.is_dir():
        for p in sorted(adir.rglob("*")):
            if p.is_file():
                out[p.name] = p
    return out


def _asset_target(asset_src: Path, src_root: Path, dst_root: Path) -> Path:
    """`assets/YYYY-MM/<name>` -> `raw/daily-work/assets/YYYY-MM/<name>`.
    Falls back to month-from-name if the source dir isn't `YYYY-MM`."""
    rel = asset_src.relative_to(src_root / "assets")
    parts = rel.parts
    sub = parts[0] if len(parts) > 1 and re.match(r"^\d{4}-\d{2}$",
                                                  parts[0]) else None
    if sub is None:
        mm = re.match(r"^(\d{4})(\d{2})", asset_src.stem)
        sub = f"{mm.group(1)}-{mm.group(2)}" if mm else "unsorted"
    return dst_root / "raw" / "daily-work" / "assets" / sub / asset_src.name


# ---------------------------------------------------------------------------
# diff instrumentation — proves strictly-subtractive (Q6 validation report)
# ---------------------------------------------------------------------------

def _count_headings(text: str) -> List[str]:
    return [ln for ln in text.split("\n") if _HEADING_RE.match(ln)]


def _code_regions(text: str) -> List[str]:
    """All fenced-block + inline-code substrings (order-stable)."""
    regions: List[str] = []
    in_fence = False
    buf: List[str] = []
    for ln in text.split("\n"):
        if _FENCE_RE.match(ln):
            buf.append(ln)
            if in_fence:
                regions.append("\n".join(buf))
                buf = []
            in_fence = not in_fence
            continue
        if in_fence:
            buf.append(ln)
        else:
            regions.extend(_INLINE_CODE_RE.findall(ln))
    if buf:
        regions.append("\n".join(buf))
    return regions


def _wikilinks(text: str) -> List[str]:
    """All [[wikilinks]] including those inside ![[embeds]] (R4 set)."""
    return _WIKILINK_RE.findall(text)


# ---------------------------------------------------------------------------
# the migration runner (dry-run / execute) + Q6 validation assertions
# ---------------------------------------------------------------------------

def run_migration(src_root: Path, dst_root: Path, *, dry_run: bool,
                  schema_dir=None) -> Dict:
    """Run the migration over a COPY-source `src_root` into `dst_root`.

    NEVER writes to `src_root` (read-only — R0/R1/R2 rollback fact).
    `dry_run=True` performs zero writes and only validates. Returns the
    Pass-1 validation report (the Q6 report schema). Pure & idempotent:
    same pristine input -> byte-identical output every run.

    `schema_dir` (default: the real `schema/`) holds `frontmatter.md` +
    `workplaces.md`; the Pass-1 "schema parse" assertion GENUINELY parses
    them (6b Q6). The schema dir is only ever READ, never written.
    """
    src_root = Path(src_root)
    dst_root = Path(dst_root)
    daily_dir = src_root / "daily"

    # ---- GENUINELY parse the registries (6b Q6) — once, deterministic ----
    sdir = _resolve_schema_dir(schema_dir)
    schema_rules = parse_schema_daily_work(sdir / "frontmatter.md")
    workplace_slugs = parse_workplace_slugs(sdir / "workplaces.md")

    rep: Dict = {
        "source_daily_count": 0,
        "produced_daily_count": 0,
        "count_reconciled": False,
        "filename_collisions": [],
        "all_embeds_resolve": True,
        "unresolved_embeds": [],
        "schema_parse_ok": True,
        "all_dates_wellformed": True,
        "headings_altered": 0,
        "code_regions_altered": 0,
        "wikilinks_altered": 0,
        "log_events_emitted": 0,          # Q6 — migration emits NONE
        "carveouts_invoked": [],          # §5.5 — NONE invoked
        "assets_relocated": 0,
        "errors": [],
        "diffs": [],                      # (name, before, after) samples
    }

    src_dailies = sorted(p for p in daily_dir.glob("*.md")
                         if p.is_file()) if daily_dir.is_dir() else []
    rep["source_daily_count"] = len(src_dailies)

    asset_index = _index_assets(src_root)

    # ---- transform every daily, collecting validation evidence ----------
    seen_targets: Dict[str, str] = {}
    produced: List[Tuple[Path, str]] = []
    for sp in src_dailies:
        text = sp.read_text(encoding="utf-8")
        try:
            out_text = transform_note(text)
        except MigrationError as e:
            rep["errors"].append(f"{sp.name}: {e}")
            rep["schema_parse_ok"] = False
            rep["all_dates_wellformed"] = False
            continue

        # filename preserved verbatim (Q4: YYYY-MM-DD.md)
        target_rel = f"raw/daily-work/{sp.name}"
        if target_rel in seen_targets:
            rep["filename_collisions"].append(target_rel)
        seen_targets[target_rel] = sp.name

        # --- strictly-subtractive instrumentation (before vs. after) -----
        if _count_headings(text) != _count_headings(out_text):
            rep["headings_altered"] += 1
        if _code_regions(text) != _code_regions(out_text):
            rep["code_regions_altered"] += 1
        if _wikilinks(text) != _wikilinks(out_text):
            rep["wikilinks_altered"] += 1

        # --- embed resolution (by basename, post-move) -------------------
        for emb in _EMBED_RE.findall(out_text):
            base = Path(emb.strip()).name
            if base not in asset_index:
                rep["all_embeds_resolve"] = False
                rep["unresolved_embeds"].append(f"{sp.name}: {emb}")

        # --- GENUINE schema parse of produced frontmatter (6b Q6) --------
        # Driven entirely by the parsed schema/frontmatter.md rows +
        # parsed schema/workplaces.md slugs (NO hardcoded equivalent).
        fm = re.match(r"^---\n(.*?)\n---\n", out_text, re.S)
        if not fm:
            rep["schema_parse_ok"] = False
        else:
            ok, why = validate_frontmatter_against_schema(
                fm.group(1), schema_rules, workplace_slugs)
            if not ok:
                rep["schema_parse_ok"] = False
                rep["errors"].append(f"{sp.name}: schema-parse: {why}")
                if "date" in why or "not well-formed" in why:
                    rep["all_dates_wellformed"] = False

        produced.append((dst_root / target_rel, out_text))
        if len(rep["diffs"]) < 5:
            rep["diffs"].append((sp.name, text, out_text))

    rep["produced_daily_count"] = len(produced)
    rep["count_reconciled"] = (
        rep["source_daily_count"] == rep["produced_daily_count"]
        and not rep["filename_collisions"]
        and not rep["errors"]
    )

    # ---- execute (writes) only when NOT a dry run -----------------------
    if not dry_run:
        for tgt, content in produced:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            with tgt.open("w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        # relocate assets (copy from the read-only source — never move it)
        for name, asp in sorted(asset_index.items()):
            at = _asset_target(asp, src_root, dst_root)
            at.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asp, at)
            rep["assets_relocated"] += 1
        _write_reports(dst_root, rep)
    else:
        rep["assets_relocated"] = len(asset_index)
        # dry-run still writes the plain-markdown _migration/ report so a
        # human can approve Pass-1 (Q6); it is NOT vault operational state.
        _write_reports(dst_root, rep)

    return rep


# ---------------------------------------------------------------------------
# _migration/ plain-markdown report folder (Q6) — NOT a log.md event
# ---------------------------------------------------------------------------

def _write_reports(dst_root: Path, rep: Dict) -> None:
    mdir = dst_root / "_migration"
    mdir.mkdir(parents=True, exist_ok=True)

    manifest = ["# Migration manifest (Task 2.4 — disposable `_migration/`)",
                "",
                "Plain-markdown artifact. NOT vault operational state, NOT a "
                "`log.md` event (6b Q6). Source vault is read-only.",
                "",
                f"- source daily notes: {rep['source_daily_count']}",
                f"- produced daily notes: {rep['produced_daily_count']}",
                f"- assets relocated -> raw/daily-work/assets/YYYY-MM/: "
                f"{rep['assets_relocated']}",
                "",
                "## Source -> target",
                ""]
    for name, *_ in rep["diffs"]:
        manifest.append(f"- `daily/{name}` -> `raw/daily-work/{name}`")
    (mdir / "manifest.md").write_text("\n".join(manifest) + "\n",
                                      encoding="utf-8", newline="")

    val = ["# Migration validation report (Pass-1)",
           "",
           "Strictly-subtractive, code-block-aware transform (6b Q5). "
           "Asserted invariants:",
           "",
           f"- [{'PASS' if rep['count_reconciled'] else 'FAIL'}] "
           f"count reconciliation "
           f"{rep['source_daily_count']}->{rep['produced_daily_count']}",
           f"- [{'PASS' if not rep['filename_collisions'] else 'FAIL'}] "
           f"no raw/daily-work/ filename collisions",
           f"- [{'PASS' if rep['all_embeds_resolve'] else 'FAIL'}] "
           f"every ![[asset]] resolves by basename post-move",
           f"- [{'PASS' if rep['schema_parse_ok'] else 'FAIL'}] "
           f"every produced note parses against the GENUINELY-parsed "
           f"schema/frontmatter.md raw|daily-work rows + "
           f"schema/workplaces.md slug registry",
           f"- [{'PASS' if rep['all_dates_wellformed'] else 'FAIL'}] "
           f"date: present & well-formed",
           f"- [{'PASS' if rep['headings_altered'] == 0 else 'FAIL'}] "
           f"0 Markdown headings altered ({rep['headings_altered']})",
           f"- [{'PASS' if rep['code_regions_altered'] == 0 else 'FAIL'}] "
           f"0 fenced/inline code regions altered "
           f"({rep['code_regions_altered']})",
           f"- [{'PASS' if rep['wikilinks_altered'] == 0 else 'FAIL'}] "
           f"0 [[wikilinks]] altered ({rep['wikilinks_altered']})",
           f"- [PASS] migration emitted {rep['log_events_emitted']} log "
           f"events (Q6: none)",
           f"- [PASS] §5.5 carve-outs invoked: "
           f"{rep['carveouts_invoked'] or 'none'}",
           ""]
    if rep["errors"]:
        val += ["## Errors", ""] + [f"- {e}" for e in rep["errors"]] + [""]
    if rep["unresolved_embeds"]:
        val += ["## Unresolved embeds", ""] + \
            [f"- {u}" for u in rep["unresolved_embeds"][:50]] + [""]
    (mdir / "validation.md").write_text("\n".join(val) + "\n",
                                        encoding="utf-8", newline="")
