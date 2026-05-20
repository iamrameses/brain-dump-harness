# brain-dump-harness

Zero-token deterministic toolchain for the Brain-Dump Obsidian vault. Handles `ingest-landing` (the harness drop step before the LLM `/ingest` skill takes over) and the one-off migration from the previous vault. Operates on the vault at a per-machine path read from the `BRAIN_DUMP_VAULT_ROOT` environment variable. Cross-platform (Windows + macOS), Python 3.10+.

## What this repo provides

- `harness.ingest_landing` — the zero-token deterministic intake module called by the `/ingest` and `promote` skills. Moves an artifact into `raw/`, writes a provisional stub, runs deterministic extractor adapters, and appends an `ingest-landed` F3 line to the vault's `log.md`.
- `harness.adapters` — deterministic extractor adapters (PDF text-layer, code-as-is, web-readability) for the ingest path.
- `harness.migration` — the one-off migration toolchain used to populate the new vault from the prior vault (executed 2026-05-19; preserved here for audit and rollback).

## Prerequisites

- Python 3.10 or newer.
- Git (for cloning this repo).
- The Brain-Dump vault accessible on this machine (typically via Google Drive sync).

## Setup

Four steps. Do them in this order — Layer 3 verification (step 4) depends on both the env var (steps 1–2) and the pip install (step 3) being complete.

---

### Step 1 — Set `BRAIN_DUMP_VAULT_ROOT` and verify shell-level (Layer 1)

This is the path *on this machine* to the vault root directory (the folder containing `CLAUDE.md`, `schema/`, `raw/`, `wiki/`, `_system/`, `log.md`).

#### Windows (PowerShell)

Set the variable (one-time; persists across sessions):

```powershell
setx BRAIN_DUMP_VAULT_ROOT "E:\Google Drive\Brian-Home\Notes\Obsidian\Brian Daily Brain Dump"
```

`setx` only affects *future* sessions — close this PowerShell window and open a new one, then verify:

```powershell
$env:BRAIN_DUMP_VAULT_ROOT
```

Should print the vault path. Blank output = `setx` didn't take or you're still in the old session.

#### macOS (zsh)

Append to `~/.zshrc`:

```zsh
export BRAIN_DUMP_VAULT_ROOT="$HOME/Library/CloudStorage/GoogleDrive-<your-email>/My Drive/Brian-Home/Notes/Obsidian/Brian Daily Brain Dump"
```

Replace `<your-email>` with the Google account whose Drive holds the vault. The exact mount path may vary by Google Drive for Desktop configuration — check `ls ~/Library/CloudStorage/` to see the actual mount.

Source the rc file or open a new terminal, then verify:

```zsh
source ~/.zshrc
echo $BRAIN_DUMP_VAULT_ROOT
```

Should print the vault path.

---

### Step 2 — Verify Python sees the env var (Layer 2)

Python sometimes has its own view of the environment (different shell sessions, IDE-spawned terminals, etc.). Confirm Python sees the same value the shell does:

```bash
python -c "import os; print(repr(os.environ.get('BRAIN_DUMP_VAULT_ROOT')))"
```

(Use `python3` on macOS if `python` doesn't resolve to Python 3.) Should print the path inside quotes. `None` means Python doesn't see the env var — check whether the shell session inherited the variable correctly.

---

### Step 3 — Install the harness via pip and verify

Clone this repo (if not already), then editable-install:

#### Windows (PowerShell)

```powershell
cd F:\Code\brain-dump-harness
pip install -e .
```

#### macOS (zsh)

```zsh
cd ~/path/to/brain-dump-harness
pip install -e .
```

The `-e` flag installs in editable mode — `git pull` updates take effect immediately without reinstall. Verify the import works from any directory:

```bash
python -c "import harness; print(harness.__file__)"
```

Should print the path to `harness/__init__.py` on this machine. `ModuleNotFoundError: No module named 'harness'` means the install didn't take.

---

### Step 4 — Verify the harness resolves to a valid schema directory (Layer 3)

End-to-end check: env var set, harness installed, and the harness's `_DEFAULT_SCHEMA_DIR` resolves to a directory that actually exists on this machine.

```bash
python -c "from harness.migration import _DEFAULT_SCHEMA_DIR; print(_DEFAULT_SCHEMA_DIR); print(_DEFAULT_SCHEMA_DIR.exists() if _DEFAULT_SCHEMA_DIR else 'env var unset')"
```

Two-line output:
1. The resolved schema directory path (e.g., `E:\Google Drive\…\Brian Daily Brain Dump\schema` on Windows, or `/Users/<you>/Library/CloudStorage/…/Brian Daily Brain Dump/schema` on macOS).
2. `True` (fully wired), `False` (env var points at a path that doesn't exist), or `env var unset` (`BRAIN_DUMP_VAULT_ROOT` isn't set).

If line 2 prints `True`, the harness is fully wired on this machine.

---

## Common failure modes

- **`setx` set but PowerShell still doesn't see it:** open a *new* terminal window. `setx` doesn't update the current session.
- **`export` set on macOS but not visible:** check `echo $SHELL` — if you're on bash or fish instead of zsh, put the `export` in the relevant rc file (`~/.bashrc` or `~/.config/fish/config.fish`).
- **Path with spaces unquoted on macOS:** the `export` value must be in double quotes (the snippet above already does this); without quotes, spaces break the value.
- **Layer 3 shows `False`:** the env var is set but points at a path that doesn't exist on this machine. Verify the vault is mounted/synced and the path matches exactly (especially the Google Drive mount form on macOS).
- **VSCode terminal has stale env after setting the variable:** restart VSCode (or open a terminal outside VSCode for the verification).
- **Layer 3 shows `env var unset` even though Layer 2 worked:** the Python process running Layer 3 was launched from a shell that didn't inherit the env var. Close and reopen the terminal.

## Updating

`git pull` from inside the harness repo. Because the install is editable (`pip install -e .`), no reinstall is needed — the import picks up the new code immediately.
