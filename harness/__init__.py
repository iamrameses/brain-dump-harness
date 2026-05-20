"""brain-dump-harness — Task 2.1 zero-token deterministic ingest-landing harness.

Lives OUTSIDE the vault (markdown-on-disk corpus). Operates ON the vault:
moves an artifact into raw/, writes a provisional raw stub, runs deterministic
extractor adapters, and appends exactly one F3 `ingest-landed` line to the
vault-root log.md. Zero LLM tokens.
"""
