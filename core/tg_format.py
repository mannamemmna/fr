"""Shared Telegram HTML formatting helpers.

The bot uses parse_mode="HTML" everywhere. HTML only requires escaping 3
characters (< > &), unlike MarkdownV2's 18 — much safer for messages built
from lots of interpolated numbers, prices, and exchange-supplied text.

RULE: any string that did NOT originate as a literal in this codebase --
exchange error messages, exception text, delisting announcement titles/
reasons, symbol names pulled from exchange APIs -- MUST be passed through
esc() before being embedded in an HTML message. Numbers formatted with
f"{x:.2f}" etc. are safe as-is (digits/./+/-/% never need HTML escaping)
but wrapping them in esc() too is harmless if you're ever unsure.
"""
from __future__ import annotations

from html import escape as _html_escape


def esc(value) -> str:
    """HTML-escape any value for safe interpolation into a Telegram HTML
    message. Converts to str first so numbers/None are handled without
    extra casts at call sites."""
    return _html_escape(str(value), quote=False)


def b(text) -> str:
    """<b>bold</b> — section headers, key labels."""
    return f"<b>{esc(text)}</b>"


def i(text) -> str:
    """<i>italic</i> — hints, in-progress status lines."""
    return f"<i>{esc(text)}</i>"


def code(text) -> str:
    """<code>monospace</code> — numbers, IDs, symbols (replaces backticks)."""
    return f"<code>{esc(text)}</code>"


def pre(text, language: str | None = None) -> str:
    """<pre><code>block</code></pre> — multi-line monospace blocks."""
    lang_attr = f' class="language-{esc(language)}"' if language else ""
    return f"<pre><code{lang_attr}>{esc(text)}</code></pre>"


def link(text, url: str) -> str:
    return f'<a href="{esc(url)}">{esc(text)}</a>'


def spoiler(text) -> str:
    return f"<tg-spoiler>{esc(text)}</tg-spoiler>"


def kv(label: str, value, *, mono: bool = True) -> str:
    """One 'Label: value' line. value is wrapped in <code> by default,
    matching the existing bot convention of putting numbers/IDs in
    backticks. Pass mono=False for plain descriptive text values."""
    v = code(value) if mono else esc(value)
    return f"{esc(label)}: {v}"


def status_icon(is_ok: bool) -> str:
    return "🟢" if is_ok else "🔴"
