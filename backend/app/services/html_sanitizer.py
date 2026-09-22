"""Sanitize administrator-authored announcement HTML.

Announcements are intentionally flexible: the administrator may paste a
complete, hand-designed HTML fragment. This module therefore uses a blacklist
for executable/browser-bound content instead of a small tag and attribute
allow-list. Remaining content is escaped as it is serialized back to HTML,
and stylesheet rules are scoped to the announcement container.
"""

from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import urlparse


_DROP_CONTENT_TAGS = {
    "applet", "base", "embed", "frame", "frameset", "iframe", "link",
    "meta", "object", "portal", "script", "template", "svg", "math",
}
_DROP_FORM_TAGS = {"button", "datalist", "form", "input", "option", "select", "textarea"}
_VOID_TAGS = {"area", "br", "col", "img", "hr", "param", "source", "track", "wbr"}
_TAG_NAME_RE = re.compile(r"^[a-z][a-z0-9:-]*$")
_ATTR_NAME_RE = re.compile(r"^[a-z_:][a-z0-9:._-]*$")
_URL_ATTRS = {
    "action", "background", "cite", "formaction", "href", "poster", "src", "xlink:href",
}
_DROP_ATTRS = {"formaction", "srcdoc", "integrity", "nonce"}
_UNSAFE_CSS_RE = re.compile(
    r"(?:expression\s*\(|javascript\s*:|vbscript\s*:|-moz-binding\s*:|behavior\s*:|@import|<\s*/?\s*(?:style|script))",
    re.IGNORECASE,
)
_UNSAFE_SELECTOR_RE = re.compile(
    r"(?:^|[^a-z0-9_-])(html|body|:host|:host-context)(?:[^a-z0-9_-]|$)",
    re.IGNORECASE,
)
_SAFE_AT_RULE_RE = re.compile(r"^@(media|supports|container|layer)\b", re.IGNORECASE)
_KEYFRAME_AT_RULE_RE = re.compile(r"^@(?:-webkit-)?keyframes\b", re.IGNORECASE)


def _safe_url(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return candidate
    if not parsed.scheme and candidate.startswith("/"):
        return candidate
    if not parsed.scheme and not candidate.startswith("//"):
        return candidate
    return None


def _split_top_level(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == delimiter and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def _matching_brace(value: str, opening_index: int) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening_index, len(value)):
        char = value[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _sanitize_css_url(value: str) -> str | None:
    return _safe_url(value.strip().strip("'\""))


def _sanitize_css_value(value: str) -> str | None:
    value = value.strip()
    if not value or _UNSAFE_CSS_RE.search(value) or any(char in value for char in "<>{"):
        return None

    output: list[str] = []
    cursor = 0
    url_pattern = re.compile(r"url\s*\(([^)]*)\)", re.IGNORECASE)
    for match in url_pattern.finditer(value):
        output.append(value[cursor:match.start()])
        safe_url = _sanitize_css_url(match.group(1))
        if safe_url is None:
            return None
        output.append(f"url('{escape(safe_url, quote=True)}')")
        cursor = match.end()
    output.append(value[cursor:])
    sanitized = "".join(output).strip()
    if re.search(r"(?:^|[;\s])(?:-moz-binding|behavior)\s*:", sanitized, re.IGNORECASE):
        return None
    return sanitized


def _sanitize_declarations(value: str) -> str:
    declarations: list[str] = []
    for declaration in _split_top_level(value, ";"):
        if ":" not in declaration:
            continue
        property_name, property_value = declaration.split(":", 1)
        property_name = property_name.strip().lower()
        if not (_ATTR_NAME_RE.fullmatch(property_name) or re.fullmatch(r"--[a-z0-9_-]+", property_name)):
            continue
        safe_value = _sanitize_css_value(property_value)
        if safe_value is not None:
            declarations.append(f"{property_name}: {safe_value}")
    return "; ".join(declarations)


def _scope_selector(selector: str) -> str | None:
    selector = selector.strip()
    if not selector or _UNSAFE_CSS_RE.search(selector):
        return None
    selector = re.sub(r"(?<![a-z0-9_-]):root\b", ".announcement-content", selector, flags=re.IGNORECASE)
    if _UNSAFE_SELECTOR_RE.search(selector):
        return None
    if any(char in selector for char in "{};") or "</" in selector:
        return None
    if selector.startswith("&"):
        selector = ".announcement-content" + selector[1:]
    elif selector.startswith((">", "+", "~")):
        selector = ".announcement-content " + selector
    elif selector == ".announcement-content" or selector.startswith(".announcement-content "):
        pass
    else:
        selector = ".announcement-content " + selector
    return selector.strip()


def _iter_css_rules(css: str):
    cursor = 0
    while cursor < len(css):
        opening = css.find("{", cursor)
        if opening < 0:
            break
        closing = _matching_brace(css, opening)
        if closing is None:
            break
        yield css[cursor:opening].strip(), css[opening + 1 : closing]
        cursor = closing + 1


def _sanitize_stylesheet(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    css = re.sub(r"@import\b[^;]*(?:;|$)", "", css, flags=re.IGNORECASE)
    output: list[str] = []
    cursor = 0
    while cursor < len(css):
        opening = css.find("{", cursor)
        if opening < 0:
            break
        header = css[cursor:opening].strip()
        closing = _matching_brace(css, opening)
        if closing is None:
            break
        body = css[opening + 1 : closing]
        if _SAFE_AT_RULE_RE.match(header):
            nested = _sanitize_stylesheet(body)
            if nested:
                output.append(f"{header} {{{nested}}}")
        elif _KEYFRAME_AT_RULE_RE.match(header):
            frames: list[str] = []
            for frame_header, frame_body in _iter_css_rules(body):
                declarations = _sanitize_declarations(frame_body)
                if declarations and re.fullmatch(r"(?:from|to|[0-9.]+%)", frame_header.strip(), re.IGNORECASE):
                    frames.append(f"{frame_header.strip()} {{{declarations}}}")
            if frames:
                output.append(f"{header} {{{''.join(frames)}}}")
        else:
            declarations = _sanitize_declarations(body)
            if declarations:
                selectors = [
                    scoped
                    for item in _split_top_level(header, ",")
                    if (scoped := _scope_selector(item)) is not None
                ]
                if selectors:
                    output.append(f"{', '.join(selectors)} {{{declarations}}}")
        cursor = closing + 1
    return "".join(output)


class _AnnouncementSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.drop_stack: list[str] = []
        self.style_buffer: list[str] | None = None

    def _append_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        safe_attrs: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if not _ATTR_NAME_RE.fullmatch(name) or name.startswith("on") or name in _DROP_ATTRS:
                continue
            if value is None:
                safe_attrs.append(f" {name}")
                continue
            if name == "style":
                value = _sanitize_declarations(value)
                if not value:
                    continue
            elif name in _URL_ATTRS:
                value = _safe_url(value)
                if value is None:
                    continue
            elif name == "srcset":
                candidates = []
                for candidate in _split_top_level(value, ","):
                    parts = candidate.strip().split()
                    if not parts:
                        continue
                    safe = _safe_url(parts[0])
                    if safe is not None:
                        candidates.append(" ".join([safe, *parts[1:]]))
                value = ", ".join(candidates)
                if not value:
                    continue
            elif _UNSAFE_CSS_RE.search(value) or re.match(r"^\s*(?:javascript|vbscript|data):", value, re.IGNORECASE):
                continue
            if name == "target" and value not in {"_blank", "_self", "_parent", "_top"}:
                continue
            safe_attrs.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "a" and any(attr.startswith(' href="') for attr in safe_attrs):
            safe_attrs.extend([' target="_blank"', ' rel="noopener noreferrer"'])
        return "".join(safe_attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_stack:
            if tag not in _VOID_TAGS:
                self.drop_stack.append(tag)
            return
        if tag in _DROP_CONTENT_TAGS or tag in _DROP_FORM_TAGS or not _TAG_NAME_RE.fullmatch(tag):
            if tag not in _VOID_TAGS:
                self.drop_stack.append(tag)
            return
        if tag == "style":
            self.output.append("<style>")
            self.open_tags.append(tag)
            self.style_buffer = []
            return
        self.output.append(f"<{tag}{self._append_attrs(tag, attrs)}>")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in self.open_tags:
            self.handle_endtag(tag)
        elif self.drop_stack and tag.lower() == self.drop_stack[-1]:
            self.drop_stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.drop_stack:
            if tag in self.drop_stack:
                while self.drop_stack:
                    current = self.drop_stack.pop()
                    if current == tag:
                        break
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            if current == "style":
                self.output.append(_sanitize_stylesheet("".join(self.style_buffer or [])))
                self.output.append("</style>")
                self.style_buffer = None
            else:
                self.output.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.drop_stack:
            return
        if self.style_buffer is not None and self.open_tags and self.open_tags[-1] == "style":
            self.style_buffer.append(data)
        else:
            self.output.append(escape(data))

    def handle_comment(self, _data: str) -> None:
        return


def sanitize_announcement_html(value: str) -> str:
    """Preserve rich announcement HTML while removing executable content."""

    parser = _AnnouncementSanitizer()
    parser.feed(value)
    parser.close()
    while parser.open_tags:
        current = parser.open_tags.pop()
        if current == "style":
            parser.output.append(_sanitize_stylesheet("".join(parser.style_buffer or [])))
            parser.output.append("</style>")
        else:
            parser.output.append(f"</{current}>")
    return "".join(parser.output).strip()
