"""Small allow-list HTML sanitizer for administrator-authored announcements."""

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse


_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "em",
    "h2",
    "h3",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "strong",
    "ul",
}
_VOID_TAGS = {"br", "hr", "img"}
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}


def _safe_url(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return candidate
    if candidate.startswith("/"):
        return candidate
    return None


class _AnnouncementSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in _ALLOWED_TAGS:
            return
        safe_attrs: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if name not in _ALLOWED_ATTRS.get(tag, set()) or value is None:
                continue
            if name in {"href", "src"}:
                value = _safe_url(value)
                if value is None:
                    continue
            safe_attrs.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "a":
            safe_attrs.extend([' target="_blank"', ' rel="noopener noreferrer"'])
        self.output.append(f"<{tag}{''.join(safe_attrs)}>")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.output.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        self.output.append(escape(data))

    def handle_comment(self, _data: str) -> None:
        return


def sanitize_announcement_html(value: str) -> str:
    """Return safe HTML while preserving readable text from unsupported tags."""

    parser = _AnnouncementSanitizer()
    parser.feed(value)
    parser.close()
    while parser.open_tags:
        parser.output.append(f"</{parser.open_tags.pop()}>")
    return "".join(parser.output).strip()
