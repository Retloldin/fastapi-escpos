from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Union
from urllib.parse import unquote, urlparse
from urllib.request import urlopen


_INLINE_TOKEN_RE = re.compile(
    r"!\[([^\]]*)\]\(([^)]+)\)"
    r"|\[([^\]]+)\]\(([^)]+)\)"
    r"|(\*\*|__)(.+?)\5"
    r"|`([^`]+)`"
)
_HR_RE = re.compile(r"^\s*([-_*]\s*){3,}$")


@dataclass(frozen=True)
class MarkdownTextSpan:
    text: str
    bold: bool = False


@dataclass(frozen=True)
class MarkdownTextLine:
    spans: Sequence[MarkdownTextSpan]


@dataclass(frozen=True)
class MarkdownImage:
    alt_text: str
    source: str
    image_bytes: bytes


MarkdownBlock = Union[MarkdownTextLine, MarkdownImage]


def decode_text_file(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode the text file.")


def parse_markdown(markdown: str) -> List[MarkdownBlock]:
    blocks: List[MarkdownBlock] = []
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    in_code_block = False

    for raw_line in lines:
        stripped = raw_line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            blocks.append(MarkdownTextLine([MarkdownTextSpan(raw_line)]))
            continue

        if not stripped:
            blocks.append(MarkdownTextLine([]))
            continue

        if _HR_RE.match(stripped):
            blocks.append(MarkdownTextLine([MarkdownTextSpan("--------------------------------")]))
            continue

        force_bold = False
        line = raw_line.rstrip()

        heading_match = re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", line)
        if heading_match:
            line = heading_match.group(2).strip()
            force_bold = True
        else:
            line = re.sub(r"^\s*[-+*]\s+", "- ", line)
            line = re.sub(r"^\s*(\d+)\.\s+", r"\1. ", line)
            line = re.sub(r"^\s*>\s?", "", line)

        blocks.extend(_parse_markdown_line(line, force_bold=force_bold))

    return _normalize_blank_lines(blocks)


def render_markdown_to_text(markdown: str) -> str:
    rendered_lines: List[str] = []
    for block in parse_markdown(markdown):
        if isinstance(block, MarkdownImage):
            alt = f": {block.alt_text}" if block.alt_text else ""
            rendered_lines.append(f"[Image{alt}]")
            continue
        rendered_lines.append("".join(span.text for span in block.spans))
    return "\n".join(rendered_lines).strip()


def resolve_markdown_image(source: str) -> bytes:
    source = source.strip()
    if not source:
        raise ValueError("Markdown image source is empty.")

    if source.startswith("data:image/"):
        return _decode_data_url(source)

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        with urlopen(source, timeout=10) as response:
            return response.read()
    else:
        raise ValueError("Only HTTP(S) URLs and data URLs are supported for Markdown images.")


def _parse_markdown_line(line: str, *, force_bold: bool) -> List[MarkdownBlock]:
    blocks: List[MarkdownBlock] = []
    spans: List[MarkdownTextSpan] = []
    last_end = 0

    for match in _INLINE_TOKEN_RE.finditer(line):
        if match.start() > last_end:
            spans.append(
                MarkdownTextSpan(
                    line[last_end:match.start()],
                    bold=force_bold,
                )
            )

        if match.group(1) is not None:
            if spans:
                blocks.append(MarkdownTextLine(_merge_spans(spans)))
                spans = []

            image_source = match.group(2).strip()
            image_bytes = resolve_markdown_image(image_source)
            blocks.append(
                MarkdownImage(
                    alt_text=match.group(1).strip(),
                    source=image_source,
                    image_bytes=image_bytes,
                )
            )
        elif match.group(3) is not None:
            label = match.group(3).strip()
            url = match.group(4).strip()
            text = url if label == url else f"{label} ({url})"
            spans.append(MarkdownTextSpan(text, bold=force_bold))
        elif match.group(6) is not None:
            spans.append(MarkdownTextSpan(match.group(6), bold=True))
        elif match.group(7) is not None:
            spans.append(MarkdownTextSpan(match.group(7), bold=force_bold))

        last_end = match.end()

    if last_end < len(line):
        spans.append(MarkdownTextSpan(line[last_end:], bold=force_bold))

    if spans or not blocks:
        blocks.append(MarkdownTextLine(_merge_spans(spans)))

    return blocks


def _merge_spans(spans: Sequence[MarkdownTextSpan]) -> List[MarkdownTextSpan]:
    merged: List[MarkdownTextSpan] = []
    for span in spans:
        if not span.text:
            continue
        if merged and merged[-1].bold == span.bold:
            previous = merged[-1]
            merged[-1] = MarkdownTextSpan(previous.text + span.text, bold=previous.bold)
            continue
        merged.append(span)
    return merged


def _normalize_blank_lines(blocks: Sequence[MarkdownBlock]) -> List[MarkdownBlock]:
    normalized: List[MarkdownBlock] = []
    previous_blank = False
    for block in blocks:
        is_blank = isinstance(block, MarkdownTextLine) and not block.spans
        if is_blank and previous_blank:
            continue
        normalized.append(block)
        previous_blank = is_blank
    return normalized


def _decode_data_url(data_url: str) -> bytes:
    header, _, data = data_url.partition(",")
    if not data:
        raise ValueError("Markdown image data URL is empty.")
    try:
        if ";base64" in header:
            return base64.b64decode(data)
        return unquote(data).encode("latin-1")
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Could not decode the embedded Markdown image.") from exc
