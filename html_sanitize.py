"""Allowlist HTML sanitizer for rich-text task notes (stdlib only).

Keeps basic formatting tags and drops every attribute, so stored notes can be
rendered as HTML without letting anyone inject scripts or event handlers.
"""
from html import escape
import re
from html.parser import HTMLParser

_ALLOWED = {"b", "strong", "i", "em", "u", "s", "strike", "p", "div", "br", "ul", "ol", "li", "span", "blockquote"}
_VOID = {"br"}
_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z]")
_DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in _ALLOWED:
            return
        self.out.append(f"<{tag}>")
        if tag not in _VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if not self.skip_depth and tag in _VOID:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth or tag not in self.open:
            return
        # Close anything left open inside this tag so output stays well-formed
        while self.open:
            t = self.open.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(escape(data, quote=False))


def sanitize_rich_text(html: str, max_len: int = 20000) -> str:
    if not html:
        return ""
    if not _TAG_RE.search(html):
        # Plain text (older notes): keep the line breaks
        return escape(html[:max_len], quote=False).replace("\n", "<br>")
    p = _Sanitizer()
    p.feed(html[:max_len])
    p.close()
    p.out.extend(f"</{t}>" for t in reversed(p.open))
    cleaned = "".join(p.out).strip()
    return "" if cleaned in ("<br>", "<div><br></div>", "<p><br></p>") else cleaned
