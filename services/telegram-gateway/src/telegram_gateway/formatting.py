"""Convert agent Markdown into Telegram-compatible HTML."""

from __future__ import annotations

import html
import re

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE_RE = re.compile(r"```(?:[A-Za-z0-9_+.-]+)?\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"(?<!\()" r"https?://[^\s<>\)\]]+")
_TRACKER_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)")


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_to_list(headers: list[str], rows: list[list[str]]) -> list[str]:
    rendered: list[str] = []
    first_header = headers[0].strip().casefold() if headers else ""
    has_index_column = first_header in {"#", "№", "n", "id"}

    for index, row in enumerate(rows, start=1):
        values = row + [""] * (len(headers) - len(row))
        if has_index_column:
            primary = values[0].strip()
            title = values[1].strip() if len(values) > 1 else ""
            prefix = f"{primary}." if primary and primary not in {"-", "—"} else f"{index}."
            detail_start = 2
        else:
            prefix = f"{index}."
            title = values[0].strip()
            detail_start = 1

        if title and title not in {"-", "—"}:
            rendered.append(f"{prefix} **{title}**")
        else:
            rendered.append(prefix)

        for column_index in range(detail_start, len(headers)):
            label = headers[column_index].strip()
            value = values[column_index].strip()
            if not label or not value or value in {"-", "—"}:
                continue
            rendered.append(f"   • **{label}:** {value}")
    return rendered


def markdown_tables_to_lists(text: str) -> str:
    """Replace GitHub-style Markdown tables with readable nested lists."""
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and "|" in lines[index]
            and _TABLE_SEPARATOR_RE.match(lines[index + 1])
        ):
            headers = _table_cells(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_table_cells(lines[index]))
                index += 1
            output.extend(_table_to_list(headers, rows))
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _stash(pattern: re.Pattern[str], text: str, values: list[str], render) -> str:
    def replace(match: re.Match[str]) -> str:
        token = f"\x00TG{len(values)}\x00"
        values.append(render(match))
        return token

    return pattern.sub(replace, text)


def render_telegram_html(text: str) -> str:
    """Render common Markdown constructs using Telegram's supported HTML."""
    source = markdown_tables_to_lists(text.strip())
    protected: list[str] = []

    source = _stash(
        _FENCE_RE,
        source,
        protected,
        lambda match: f"<pre>{html.escape(match.group(1).strip())}</pre>",
    )
    source = _stash(
        _INLINE_CODE_RE,
        source,
        protected,
        lambda match: f"<code>{html.escape(match.group(1))}</code>",
    )
    source = _stash(
        _LINK_RE,
        source,
        protected,
        lambda match: (
            f'<a href="{html.escape(match.group(2), quote=True)}">{html.escape(match.group(1))}</a>'
        ),
    )
    source = _stash(
        _BARE_URL_RE,
        source,
        protected,
        lambda match: (
            f'<a href="{html.escape(match.group(0), quote=True)}">{html.escape(match.group(0))}</a>'
        ),
    )
    source = _stash(
        _TRACKER_KEY_RE,
        source,
        protected,
        lambda match: (
            f'<a href="https://tracker.yandex.ru/{match.group(1)}">{match.group(1)}</a>'
        ),
    )

    escaped = html.escape(source)
    lines: list[str] = []
    for line in escaped.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            line = f"<b>{heading.group(2).strip()}</b>"
        lines.append(line)
    rendered = "\n".join(lines)

    rendered = _BOLD_RE.sub(lambda match: f"<b>{match.group(1) or match.group(2)}</b>", rendered)
    rendered = _STRIKE_RE.sub(r"<s>\1</s>", rendered)
    rendered = _ITALIC_RE.sub(
        lambda match: f"<i>{match.group(1) or match.group(2)}</i>",
        rendered,
    )

    for index, value in enumerate(protected):
        rendered = rendered.replace(f"\x00TG{index}\x00", value)
    return rendered


__all__ = ["markdown_tables_to_lists", "render_telegram_html"]
