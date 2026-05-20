r"""TDD suite for Task 2.4 — deterministic, idempotent migration transform.

Written FIRST. Expected to FAIL until harness/migration.py exists (RED).

Contract under test (6b-migration.md Q5 — THE RULE SET, verbatim; Q3/Q4/Q6;
new-vault-plan.md §5.1/§5.5/§9.3; schema/frontmatter.md `raw|daily-work|*`):

  Strictly-SUBTRACTIVE, code-block-aware body transform:
    R1  strip `- [ ]` / `- [x]` -> `- ` (indentation preserved)
    R2  Dataview `[key:: value]`: if value has [[wikilink(s)]] -> bare
        wikilink(s); else field removed entirely
    R3  inline `#tag` removed; NEVER a Markdown heading (`^#{1,6}\s`);
        NEVER inside fenced (```) or inline (`...`) code
    R4  all prose and all [[wikilinks]] (person AND project) byte-untouched
    R5  frontmatter normalized as creation-metadata: keep `date:` verbatim;
        set `type: daily-work`; set `workplace: stanford-raymond-lab`;
        drop `processed:`; NO `created:`. Map a per-type `status:` to the
        per-context enum ONLY if present (daily notes carry none -> no-op).

  Asset relocation: assets/ -> raw/daily-work/assets/YYYY-MM/, embeds
  resolve by basename (6b Q3/Q5).

  Pure + idempotent (R2 rollback / 6b Q6). NO log events. NO carve-outs.
  Source vault NEVER written (hash-identical before/after). Validation /
  manifest are plain-markdown `_migration/` artifacts.
"""

import hashlib
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import migration as mg  # noqa: E402

REAL_SOURCE = Path(
    r"e:\Google Drive\Brian-Home\Notes\Obsidian\Brian at Raymond Lab"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hash_tree(root: Path) -> str:
    """Order-stable hash of every file's relative path + bytes under root."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def _mini_source(tmp_path: Path) -> Path:
    """A synthetic source vault exercising every Q5 rule + edges."""
    src = tmp_path / "src-vault"
    (src / "daily").mkdir(parents=True)
    (src / "assets" / "2026-05").mkdir(parents=True)
    (src / "notes").mkdir(parents=True)  # must NOT be migrated (Q3)
    (src / "_system").mkdir(parents=True)  # must NOT be carried

    # asset embedded by basename
    (src / "assets" / "2026-05" / "20260518120000.png").write_bytes(
        b"\x89PNG\r\n\x1a\nFAKE"
    )

    note = textwrap.dedent(
        """\
        ---
        type: daily
        date: 2026-05-18
        processed: true
        ---
        # Heading must survive #not-a-tag-here-its-a-heading

        Prose line with a #realtag and a [[Jennifer Raymond]] wikilink.

        - [ ] An open task with fields [priority:: high] [project:: [[Big Project]]] [created::2026-05-18]
        - [x] A done task [project::] [due:: ]
        	- [ ] An indented subtask keeps its tab indent

        Plain bullet with [[Person Link|alias]] and #another-tag here.

        Inline code `not a #tag and not [[a link]] and - [ ] not a task` stays.

        ```bash
        #!/bin/bash
        # - [ ] this is code not a task
        echo "#nothashtag [[nolink]] [k:: v]"
        ```

        ![[20260518120000.png]]

        Final prose with [[Single-Trial Optogenetics]] project link.
        """
    )
    (src / "daily" / "2026-05-18.md").write_text(note, encoding="utf-8", newline="")

    # a second daily, different date, no machinery (idempotence/no-op shape)
    (src / "daily" / "2024-04-04.md").write_text(
        "---\ntype: daily\ndate: 2024-04-04\nprocessed: false\n---\n"
        "# Pure prose note\n\nNothing to strip here. [[Hyun-Geun Shim]]\n",
        encoding="utf-8",
        newline="",
    )

    # decoy non-daily content that must NOT cross over (Q3)
    (src / "notes" / "Jennifer Raymond.md").write_text(
        "---\ntype: person\n---\nshould not migrate\n", encoding="utf-8"
    )
    (src / "_system" / "vault-index.md").write_text("legacy", encoding="utf-8")
    return src


# ---------------------------------------------------------------------------
# R5 — frontmatter normalization
# ---------------------------------------------------------------------------

def test_r5_frontmatter_keep_date_set_type_workplace_drop_processed():
    fm_in = "type: daily\ndate: 2026-05-18\nprocessed: true\n"
    out = mg.transform_frontmatter(fm_in)
    assert "date: 2026-05-18" in out, "date: kept verbatim"
    assert re.search(r"^type: daily-work$", out, re.M), "type set to daily-work"
    assert re.search(r"^workplace: stanford-raymond-lab$", out, re.M)
    assert "processed:" not in out, "processed: dropped"
    assert "created:" not in out, "no created: row for daily (schema)"


def test_r5_status_present_maps_else_noop():
    # daily notes carry no status: -> absolute no-op (do not invent a row)
    out = mg.transform_frontmatter("type: daily\ndate: 2024-04-04\n")
    assert "status:" not in out
    # if a status WERE present, it maps per delta-2 Cluster I (project enum)
    out2 = mg.transform_frontmatter(
        "type: daily\ndate: 2024-04-04\nstatus: active\n"
    )
    assert "status: active" in out2  # 'active' is valid in status-project


def test_r5_date_must_be_wellformed_else_flagged():
    with pytest.raises(mg.MigrationError):
        mg.transform_frontmatter("type: daily\ndate: not-a-date\n")
    with pytest.raises(mg.MigrationError):
        mg.transform_frontmatter("type: daily\n")  # missing date


# ---------------------------------------------------------------------------
# R1 — checkbox stripping (indentation preserved)
# ---------------------------------------------------------------------------

def test_r1_checkbox_markers_stripped_indent_preserved():
    assert mg.transform_body("- [ ] todo") == "- todo"
    assert mg.transform_body("- [x] done") == "- done"
    assert mg.transform_body("\t- [ ] indented") == "\t- indented"
    assert mg.transform_body("    - [x] spaces") == "    - spaces"
    # surviving line text kept as a plain prose bullet, prose untouched
    assert mg.transform_body("- [ ] Buy [[milk]] today") == "- Buy [[milk]] today"


# ---------------------------------------------------------------------------
# R2 — Dataview inline fields
# ---------------------------------------------------------------------------

def test_r2_field_with_wikilink_becomes_bare_wikilink():
    assert mg.transform_body("- task [project:: [[Big Project]]]") == \
        "- task [[Big Project]]"


def test_r2_field_plain_value_removed_entirely():
    assert mg.transform_body("- task [priority:: high]") == "- task"
    assert mg.transform_body("- task [created::2026-05-18]") == "- task"
    assert mg.transform_body("- task [project::]") == "- task"
    assert mg.transform_body("- task [due:: ]") == "- task"


def test_r2_mixed_fields_one_line_real_corpus_shape():
    line = ("- [ ] Create GUI for [[Hannah Cui|Hannah]]'s code "
            "[priority:: medium] [project:: [[Single-Trial Optogenetics]]] "
            "[created::2025-06-29]")
    assert mg.transform_body(line) == (
        "- Create GUI for [[Hannah Cui|Hannah]]'s code "
        "[[Single-Trial Optogenetics]]"
    )


def test_r2_multiple_wikilinks_in_one_field_all_kept():
    assert mg.transform_body("x [rel:: [[A]] [[B]]]") == "x [[A]] [[B]]"


# ---------------------------------------------------------------------------
# R3 — inline #tag removal, with carve-outs
# ---------------------------------------------------------------------------

def test_r3_inline_tag_removed_in_prose():
    assert mg.transform_body("Prose with #realtag here.") == "Prose with here."
    assert mg.transform_body("#data-analysis") == ""


def test_r3_heading_line_wholly_exempt():
    # E2 documented interpretation: a `^#{1,6}\s` heading line is WHOLLY
    # R3-exempt (Q5 "never Markdown headings"); grounded in 0/149 corpus
    # headings containing any '#' in text. Heading byte-untouched.
    assert mg.transform_body("# Heading") == "# Heading"
    assert mg.transform_body("###### h6 with #tag") == "###### h6 with #tag"
    assert mg.transform_body("## H #not-a-tag-here") == "## H #not-a-tag-here"
    # the corpus convention — a tag on its OWN line below a heading — IS
    # subject to R3 (that line is ordinary prose, not a heading):
    assert mg.transform_body("# Title\n#data-analysis\nbody") == \
        "# Title\n\nbody"


def test_r3_tag_inside_fenced_code_preserved():
    src = "```bash\n#!/bin/bash\n# - [ ] not a task\n```"
    assert mg.transform_body(src) == src, "fenced code is byte-exempt"


def test_r3_tag_inside_inline_code_preserved():
    s = "Text `#nothashtag and - [ ] and [[no]]` then #realtag out"
    assert mg.transform_body(s) == "Text `#nothashtag and - [ ] and [[no]]` then out"


def test_r3_does_not_touch_anchor_or_url_hash():
    # a '#' not forming a standalone #tag token in prose stays (subtractive,
    # minimal): only whitespace/again-bounded #tag tokens are machinery
    assert mg.transform_body("see http://x/y#frag end") == "see http://x/y#frag end"


# ---------------------------------------------------------------------------
# R4 — prose / wikilinks byte-untouched
# ---------------------------------------------------------------------------

def test_r4_prose_and_wikilinks_byte_untouched():
    s = ("Para with [[Jennifer Raymond]] and [[Person|alias]] and a "
         "[[Project Name]] link. Em-dash — and *emphasis* kept.")
    assert mg.transform_body(s) == s


def test_r4_blockquote_callout_untouched():
    s = "> [!QUOTE] Per [[Jennifer Raymond]]:\n> - a quoted bullet stays"
    assert mg.transform_body(s) == s


# ---------------------------------------------------------------------------
# whole-note transform + asset relocation
# ---------------------------------------------------------------------------

def test_full_note_transform_strictly_subtractive(tmp_path):
    src = _mini_source(tmp_path)
    dst = tmp_path / "out"
    rep = mg.run_migration(src, dst, dry_run=False)

    produced = (dst / "raw" / "daily-work" / "2026-05-18.md").read_text(
        encoding="utf-8"
    )
    # R5 frontmatter
    assert "type: daily-work" in produced
    assert "workplace: stanford-raymond-lab" in produced
    assert "date: 2026-05-18" in produced
    assert "processed:" not in produced
    # R1 checkbox gone from real list items, indent kept. (Literal "- [ ]"
    # that survives is INSIDE fenced/inline code — correctly byte-exempt
    # per R3/E1; assert per-line only on NON-code lines.)
    in_fence = False
    for ln in produced.split("\n"):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or "`" in ln:
            continue  # code region: byte-exempt, may contain "- [ ]"
        assert "- [ ]" not in ln and "- [x]" not in ln, ln
    assert "\t- An indented subtask keeps its tab indent" in produced
    # R2
    assert "[priority::" not in produced and "[created::" not in produced
    assert "[[Big Project]]" in produced
    # R3 prose tag gone, heading hash + heading text survive
    assert "#realtag" not in produced and "#another-tag" not in produced
    assert "# Heading must survive #not-a-tag-here-its-a-heading" in produced
    # fenced + inline code byte-exempt
    assert "#!/bin/bash" in produced
    assert "# - [ ] this is code not a task" in produced
    assert "`not a #tag and not [[a link]] and - [ ] not a task`" in produced
    # R4 wikilinks + prose intact
    assert "[[Jennifer Raymond]]" in produced
    assert "[[Single-Trial Optogenetics]]" in produced
    assert "[[Person Link|alias]]" in produced
    # asset relocated + embed still resolves by basename
    assert (dst / "raw" / "daily-work" / "assets" / "2026-05"
            / "20260518120000.png").exists()
    assert "![[20260518120000.png]]" in produced

    # Q3: notes/ and _system/ did NOT cross over
    assert not (dst / "notes").exists()
    assert not (dst / "_system").exists()
    assert not list(dst.rglob("Jennifer Raymond.md"))
    # Q6: NO log.md emitted by migration
    assert not (dst / "log.md").exists()
    assert rep["log_events_emitted"] == 0


def test_149_count_reconciliation_and_no_collision(tmp_path):
    src = tmp_path / "s"
    (src / "daily").mkdir(parents=True)
    (src / "assets").mkdir(parents=True)
    for i in range(149):
        d = f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        # unique filename per note so 149 distinct names
        (src / "daily" / f"note-{i:03d}-{d}.md").write_text(
            f"---\ntype: daily\ndate: {d}\nprocessed: false\n---\nbody {i}\n",
            encoding="utf-8",
        )
    dst = tmp_path / "o"
    rep = mg.run_migration(src, dst, dry_run=True)
    assert rep["source_daily_count"] == 149
    assert rep["produced_daily_count"] == 149
    assert rep["count_reconciled"] is True
    assert rep["filename_collisions"] == []


def test_asset_resolution_assertion_catches_dangling_embed(tmp_path):
    src = tmp_path / "s"
    (src / "daily").mkdir(parents=True)
    (src / "assets").mkdir(parents=True)
    (src / "daily" / "2026-01-01.md").write_text(
        "---\ntype: daily\ndate: 2026-01-01\n---\n![[missing-asset.png]]\n",
        encoding="utf-8",
    )
    dst = tmp_path / "o"
    rep = mg.run_migration(src, dst, dry_run=True)
    assert any("missing-asset.png" in u for u in rep["unresolved_embeds"])
    assert rep["all_embeds_resolve"] is False


def test_produced_frontmatter_parses_against_schema(tmp_path):
    src = _mini_source(tmp_path)
    dst = tmp_path / "o"
    rep = mg.run_migration(src, dst, dry_run=False)
    assert rep["schema_parse_ok"] is True
    assert rep["all_dates_wellformed"] is True


# ---------------------------------------------------------------------------
# Note 1 — the schema-driven validator GENUINELY parses
# schema/frontmatter.md + schema/workplaces.md (6b Q6 + Task-1.5
# carried unescaped-pipe constraint). NO hardcoded regex equivalent.
# ---------------------------------------------------------------------------

REAL_SCHEMA_DIR = Path(
    r"F:\Code\Obsidian\Brian Daily Brain Dump\schema"
)


def _write_schema_copy(tmp_path: Path) -> Path:
    """Temp copy of the REAL schema/ (frontmatter.md + workplaces.md).
    The validator must operate against THIS copy, never the real file."""
    sdir = tmp_path / "schema"
    sdir.mkdir(parents=True)
    shutil.copy2(REAL_SCHEMA_DIR / "frontmatter.md", sdir / "frontmatter.md")
    shutil.copy2(REAL_SCHEMA_DIR / "workplaces.md", sdir / "workplaces.md")
    return sdir


def test_unescaped_pipe_split_treats_backslash_pipe_as_literal():
    # Task-1.5 carried constraint: split ONLY on (?<!\|, `\|` is a literal
    # cell character even inside backtick code spans.
    row = r"| a | b `x \| y` | c\|d | e |"
    cells = mg._split_gfm_row(row)
    assert cells == ["a", "b `x | y`", "c|d", "e"], cells


@pytest.mark.skipif(not REAL_SCHEMA_DIR.exists(),
                    reason="real schema dir not present")
def test_schema_parser_extracts_raw_daily_work_rules(tmp_path):
    sdir = _write_schema_copy(tmp_path)
    rules = mg.parse_schema_daily_work(sdir / "frontmatter.md")
    # required raw|daily-work field set + (cardinality, value-kind), driven
    # entirely by the parsed GFM table — no hardcoded field list.
    assert set(rules) == {"type", "date", "workplace"}, rules
    assert rules["type"] == ("required", "scalar", "string")
    assert rules["date"] == ("required", "scalar", "date")
    assert rules["workplace"] == ("required", "scalar", "slug")
    # there is NO processed/created row for daily-work in the schema
    assert "processed" not in rules and "created" not in rules


@pytest.mark.skipif(not REAL_SCHEMA_DIR.exists(),
                    reason="real schema dir not present")
def test_workplace_slug_registry_parsed_unescaped_pipe(tmp_path):
    sdir = _write_schema_copy(tmp_path)
    slugs = mg.parse_workplace_slugs(sdir / "workplaces.md")
    assert "stanford-raymond-lab" in slugs
    assert "personal" in slugs
    assert "sep" not in slugs and "slug" not in slugs  # header/sep excluded


@pytest.mark.skipif(not REAL_SCHEMA_DIR.exists(),
                    reason="real schema dir not present")
def test_validator_driven_by_parsed_schema_rejects_unknown_fields(tmp_path):
    sdir = _write_schema_copy(tmp_path)
    rules = mg.parse_schema_daily_work(sdir / "frontmatter.md")
    slugs = mg.parse_workplace_slugs(sdir / "workplaces.md")

    good = ("type: daily-work\ndate: 2026-05-18\n"
            "workplace: stanford-raymond-lab\n")
    ok, why = mg.validate_frontmatter_against_schema(good, rules, slugs)
    assert ok, why

    # processed:/created:/unknown all rejected — there is NO parsed row for
    # them under raw|daily-work (driven by the ABSENCE of a schema row).
    for inj in ("processed: true", "created: 2026-05-18", "tags: x"):
        bad = good + inj + "\n"
        ok, why = mg.validate_frontmatter_against_schema(bad, rules, slugs)
        assert not ok, f"{inj!r} must be rejected (no schema row): {why}"
        assert inj.split(":")[0] in why


@pytest.mark.skipif(not REAL_SCHEMA_DIR.exists(),
                    reason="real schema dir not present")
def test_validator_rejects_wrong_type_and_unregistered_slug(tmp_path):
    sdir = _write_schema_copy(tmp_path)
    rules = mg.parse_schema_daily_work(sdir / "frontmatter.md")
    slugs = mg.parse_workplace_slugs(sdir / "workplaces.md")

    wrong_type = ("type: daily\ndate: 2026-05-18\n"
                  "workplace: stanford-raymond-lab\n")
    ok, why = mg.validate_frontmatter_against_schema(wrong_type, rules, slugs)
    assert not ok and "type" in why

    bad_slug = ("type: daily-work\ndate: 2026-05-18\n"
                "workplace: not-a-registered-slug\n")
    ok, why = mg.validate_frontmatter_against_schema(bad_slug, rules, slugs)
    assert not ok and "workplace" in why

    missing_date = ("type: daily-work\n"
                    "workplace: stanford-raymond-lab\n")
    ok, why = mg.validate_frontmatter_against_schema(missing_date, rules,
                                                     slugs)
    assert not ok and "date" in why

    bad_date = ("type: daily-work\ndate: nope\n"
                "workplace: stanford-raymond-lab\n")
    ok, why = mg.validate_frontmatter_against_schema(bad_date, rules, slugs)
    assert not ok and "date" in why


@pytest.mark.skipif(not REAL_SCHEMA_DIR.exists(),
                    reason="real schema dir not present")
def test_run_migration_uses_schema_dir_param_temp_copy(tmp_path):
    # The validator READS a schema dir we point at a temp copy; it never
    # writes it. The mini-source still validates schema-driven.
    sdir = _write_schema_copy(tmp_path)
    before = _hash_tree(sdir)
    src = _mini_source(tmp_path)
    dst = tmp_path / "o"
    rep = mg.run_migration(src, dst, dry_run=False, schema_dir=sdir)
    assert rep["schema_parse_ok"] is True
    assert rep["all_dates_wellformed"] is True
    assert _hash_tree(sdir) == before, "schema dir must be READ-ONLY"


@pytest.mark.skipif(
    not (REAL_SOURCE.exists() and REAL_SCHEMA_DIR.exists()),
    reason="real source vault or schema dir not present")
def test_all_149_real_dailies_pass_schema_driven_validation(tmp_path):
    # COPY the real daily/+assets/ AND schema/ into scratch; operate ONLY
    # on the copies. All 149 produced notes must validate against the
    # GENUINELY-parsed schema/frontmatter.md + workplaces.md.
    copy_root = tmp_path / "real-copy"
    (copy_root / "daily").mkdir(parents=True)
    (copy_root / "assets").mkdir(parents=True)
    shutil.copytree(REAL_SOURCE / "daily", copy_root / "daily",
                     dirs_exist_ok=True)
    shutil.copytree(REAL_SOURCE / "assets", copy_root / "assets",
                     dirs_exist_ok=True)
    sdir = _write_schema_copy(tmp_path)

    real_before = _hash_tree(REAL_SOURCE / "daily")
    schema_before = _hash_tree(sdir)
    rep = mg.run_migration(copy_root, tmp_path / "o", dry_run=True,
                           schema_dir=sdir)
    assert _hash_tree(REAL_SOURCE / "daily") == real_before
    assert _hash_tree(sdir) == schema_before, "schema READ-ONLY"

    assert rep["produced_daily_count"] == 149
    assert rep["schema_parse_ok"] is True, rep["errors"][:5]
    assert rep["all_dates_wellformed"] is True
    # truthful report wording: it genuinely parsed the schema files
    val = (tmp_path / "o" / "_migration" / "validation.md").read_text(
        encoding="utf-8")
    assert "schema/frontmatter.md" in val and "workplaces.md" in val


# ---------------------------------------------------------------------------
# idempotence + source-unmodified
# ---------------------------------------------------------------------------

def test_idempotence_second_run_byte_identical(tmp_path):
    src = _mini_source(tmp_path)
    d1 = tmp_path / "o1"
    d2 = tmp_path / "o2"
    mg.run_migration(src, d1, dry_run=False)
    mg.run_migration(src, d2, dry_run=False)
    assert _hash_tree(d1) == _hash_tree(d2), "two runs must be byte-identical"
    # re-run into the SAME dir is also byte-stable
    h_before = _hash_tree(d1)
    mg.run_migration(src, d1, dry_run=False)
    assert _hash_tree(d1) == h_before, "re-run into same dir is byte-stable"


def test_source_tree_hash_identical_before_and_after(tmp_path):
    src = _mini_source(tmp_path)
    before = _hash_tree(src)
    mg.run_migration(src, tmp_path / "o", dry_run=False)
    mg.run_migration(src, tmp_path / "o2", dry_run=True)
    after = _hash_tree(src)
    assert before == after, "SOURCE VAULT MUST BE BYTE-UNTOUCHED"


def test_no_carveouts_invoked_and_no_log_event(tmp_path):
    src = _mini_source(tmp_path)
    dst = tmp_path / "o"
    rep = mg.run_migration(src, dst, dry_run=False)
    # migration must not write any log.md nor emit file-removed/raw-reextract
    assert rep["log_events_emitted"] == 0
    assert rep["carveouts_invoked"] == []
    assert not (dst / "log.md").exists()
    # _migration/ report is plain markdown, NOT vault operational state
    man = dst / "_migration" / "manifest.md"
    val = dst / "_migration" / "validation.md"
    assert man.exists() and val.exists()
    assert man.read_text(encoding="utf-8").lstrip().startswith("#")


# ---------------------------------------------------------------------------
# dry-run over a TEMP COPY of the REAL daily/+assets/ (never the original)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_SOURCE.exists(),
                    reason="real source vault not present")
def test_dryrun_over_temp_copy_of_real_vault(tmp_path):
    # COPY the real daily/+assets/ into scratch; operate ONLY on the copy.
    copy_root = tmp_path / "real-copy"
    (copy_root / "daily").mkdir(parents=True)
    (copy_root / "assets").mkdir(parents=True)
    shutil.copytree(REAL_SOURCE / "daily", copy_root / "daily",
                     dirs_exist_ok=True)
    shutil.copytree(REAL_SOURCE / "assets", copy_root / "assets",
                     dirs_exist_ok=True)

    real_before = _hash_tree(REAL_SOURCE / "daily")
    rep = mg.run_migration(copy_root, tmp_path / "o", dry_run=True)
    real_after = _hash_tree(REAL_SOURCE / "daily")

    assert real_before == real_after, "REAL vault daily/ must be untouched"
    assert rep["source_daily_count"] == 149
    assert rep["produced_daily_count"] == 149
    assert rep["count_reconciled"] is True
    assert rep["filename_collisions"] == []
    assert rep["all_embeds_resolve"] is True, rep["unresolved_embeds"][:5]
    assert rep["schema_parse_ok"] is True
    assert rep["all_dates_wellformed"] is True
    assert rep["headings_altered"] == 0
    assert rep["code_regions_altered"] == 0
    assert rep["wikilinks_altered"] == 0
