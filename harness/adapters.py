"""Swappable deterministic extractor adapters (new-vault-plan.md §5.3).

ONE fixed ingest contract; the only per-modality variation is a thin swappable
extractor adapter. This module ships the DETERMINISTIC-FIRST adapters only:

  - PdfTextLayerAdapter   : PDF text-layer extraction (deterministic, no OCR)
  - CodeAsIsAdapter       : code/text files stored verbatim
  - WebReadabilityAdapter : readability-style main-text extraction (stdlib)

LLM-fallback adapters (scanned-PDF OCR, image caption, audio/video transcript)
are explicitly NOT here — they belong to the Task 4.3 LLM ingest skill and plug
into this SAME interface (a new modality is a new adapter, never a new pipeline).

Adapter interface (the swap point Task 4.3 extends):

    class ExtractorAdapter:
        name: str
        def matches(self, *, suffix: str, is_url: bool, data: bytes) -> bool: ...
        def extract(self, data: bytes) -> ExtractionResult: ...

`ExtractionResult.status` is one of:
  - "extracted"            : deterministic text recovered (body = text)
  - "deterministic-empty"  : adapter ran, recovered no text deterministically
                             (e.g. scanned/no-text-layer PDF). NOT a failure;
                             NOT OCR'd. The LLM-fallback adapter (Task 4.3)
                             owns the recovery path.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List


@dataclass
class ExtractionResult:
    status: str          # "extracted" | "deterministic-empty"
    text: str            # extracted body text ("" when deterministic-empty)
    adapter: str         # adapter name that produced this result


class ExtractorAdapter:
    """Base adapter interface. Task 4.3 LLM-fallback adapters subclass this."""

    name: str = "base"

    def matches(self, *, suffix: str, is_url: bool, data: bytes) -> bool:
        raise NotImplementedError

    def extract(self, data: bytes) -> ExtractionResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# PDF text-layer (deterministic, NO OCR)
# ---------------------------------------------------------------------------

class PdfTextLayerAdapter(ExtractorAdapter):
    name = "pdf-text-layer"

    def matches(self, *, suffix: str, is_url: bool, data: bytes) -> bool:
        if is_url:
            return False
        return suffix.lower() == ".pdf" or data[:5] == b"%PDF-"

    def extract(self, data: bytes) -> ExtractionResult:
        from pypdf import PdfReader  # deterministic text-layer reader

        reader = PdfReader(io.BytesIO(data))
        parts: List[str] = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                parts.append(txt.strip())
        body = "\n\n".join(parts).strip()
        if not body:
            # Scanned / no text layer. Deterministic-empty: the harness must
            # NOT OCR. The Task 4.3 LLM-fallback OCR adapter owns recovery.
            return ExtractionResult("deterministic-empty", "", self.name)
        return ExtractionResult("extracted", body, self.name)


# ---------------------------------------------------------------------------
# Code / text as-is (verbatim body)
# ---------------------------------------------------------------------------

class CodeAsIsAdapter(ExtractorAdapter):
    name = "code-as-is"

    # Deliberately broad: any non-PDF, non-URL artifact that decodes as text.
    def matches(self, *, suffix: str, is_url: bool, data: bytes) -> bool:
        if is_url:
            return False
        try:
            data.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    def extract(self, data: bytes) -> ExtractionResult:
        # Decode verbatim, then normalize line endings deterministically to \n
        # so the stored body is platform-stable (the bytes are still preserved
        # verbatim in the immutable co-located binary).
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return ExtractionResult("extracted", text, self.name)


# ---------------------------------------------------------------------------
# Web / URL readability (deterministic, stdlib only)
# ---------------------------------------------------------------------------

class _ReadabilityParser(HTMLParser):
    """Deterministic readability-lite: drop script/style/nav/header/footer/aside,
    keep visible text from body/article/main and block elements."""

    _DROP = {"script", "style", "nav", "header", "footer", "aside",
             "noscript", "template", "form", "svg"}
    _BLOCK = {"p", "div", "section", "article", "li", "br", "h1", "h2",
              "h3", "h4", "h5", "h6", "tr", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._drop_depth = 0
        self._chunks: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._DROP:
            self._drop_depth += 1
        elif tag in self._BLOCK and self._drop_depth == 0:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._DROP and self._drop_depth > 0:
            self._drop_depth -= 1
        elif tag in self._BLOCK and self._drop_depth == 0:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._drop_depth == 0 and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        out: List[str] = []
        for ln in lines:
            if ln:
                out.append(ln)
            elif out and out[-1] != "":
                out.append("")
        return "\n".join(out).strip()


class WebReadabilityAdapter(ExtractorAdapter):
    name = "web-readability"

    def matches(self, *, suffix: str, is_url: bool, data: bytes) -> bool:
        return is_url

    def extract(self, data: bytes) -> ExtractionResult:
        markup = data.decode("utf-8", errors="replace")
        parser = _ReadabilityParser()
        parser.feed(markup)
        body = html.unescape(parser.text())
        if not body.strip():
            return ExtractionResult("deterministic-empty", "", self.name)
        return ExtractionResult("extracted", body, self.name)


# ---------------------------------------------------------------------------
# Binary passthrough (deterministic terminal): non-PDF binary artifact (image,
# audio, video, office doc) the deterministic adapters cannot extract. Per
# §5.3 a binary modality still yields a two-file raw entity; deterministic
# extraction is simply empty here (NOT a failure, NO OCR/caption/transcript —
# those are Task 4.3 LLM-fallback adapters that plug into this same interface).
# ---------------------------------------------------------------------------

class BinaryPassthroughAdapter(ExtractorAdapter):
    name = "binary-passthrough"

    def matches(self, *, suffix: str, is_url: bool, data: bytes) -> bool:
        return True  # terminal catch-all; always last in the registry

    def extract(self, data: bytes) -> ExtractionResult:
        return ExtractionResult("deterministic-empty", "", self.name)


# Deterministic adapter registry. Order matters: PDF before code-as-is so a
# .pdf is never mis-handled as decodable bytes; binary-passthrough is the
# terminal catch-all. Task 4.3 appends LLM-fallback adapters BEFORE the
# terminal passthrough without touching the pipeline.
DETERMINISTIC_ADAPTERS: List[ExtractorAdapter] = [
    PdfTextLayerAdapter(),
    WebReadabilityAdapter(),
    CodeAsIsAdapter(),
    BinaryPassthroughAdapter(),
]


def select_adapter(*, suffix: str, is_url: bool, data: bytes,
                    adapters: List[ExtractorAdapter] = None) -> ExtractorAdapter:
    """Pick the first matching adapter. Pure, deterministic."""
    for ad in (adapters if adapters is not None else DETERMINISTIC_ADAPTERS):
        if ad.matches(suffix=suffix, is_url=is_url, data=data):
            return ad
    raise LookupError(f"no deterministic extractor adapter for suffix={suffix!r} is_url={is_url}")
