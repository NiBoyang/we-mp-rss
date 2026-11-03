"""Parse public WeChat article HTML without importing the collector stack."""

from urllib.parse import quote, unquote, urlsplit, urlunsplit

from bs4 import BeautifulSoup


def normalize_mp_article_url(url: str) -> str:
    """Rewrite WeRead short-link tokens that use ``~`` in place of ``_``.

    WeChat rejects ``https://mp.weixin.qq.com/s/<token>`` when ``_`` was stored
    as ``~``: the response is a small ``参数错误`` page with no ``og:title`` and
    no article body. The canonical token uses ``_``.
    """
    raw = (url or "").strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host != "mp.weixin.qq.com":
        return raw

    path = parts.path or ""
    marker = "/s/"
    if not path.startswith(marker):
        return raw

    token = unquote(path[len(marker):].split("/", 1)[0])
    if "~" not in token:
        return raw

    fixed_token = quote(token.replace("~", "_"), safe="")
    return urlunsplit(
        (parts.scheme or "https", parts.netloc, marker + fixed_token, parts.query, parts.fragment)
    )


def meta_content(html: str, prop: str) -> str:
    """Read a meta property from raw HTML. Missing tags return an empty string."""
    if not html or not prop:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("meta", attrs={"property": prop})
    if node is None:
        return ""
    return str(node.get("content") or "")


def _extract_js_single_quoted(html: str, marker: str) -> str:
    """Extract a single-quoted JS string, keeping escape sequences intact."""
    i = html.find(marker)
    if i < 0:
        return ""
    i += len(marker)
    out = []
    k = i
    while k < len(html):
        ch = html[k]
        if ch == "\\":
            out.append(html[k : k + 2])
            k += 2
            continue
        if ch == "'":
            break
        out.append(ch)
        k += 1
    return "".join(out)


def _js_unescape(value: str) -> str:
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            if nxt == "x" and index + 3 < len(value):
                try:
                    out.append(chr(int(value[index + 2 : index + 4], 16)))
                    index += 4
                    continue
                except ValueError:
                    pass
            mapping = {"n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"', "\\": "\\"}
            out.append(mapping.get(nxt, nxt))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _extract_content_noencode(html: str) -> str:
    """Read the ``content_noencode`` share variable when ``#js_content`` is absent.

    The variable is either plain text (newlines as ``\\x0a``) or the rich-text
    HTML itself (tags as ``\\x3c``). Plain text is wrapped in paragraphs.
    """
    raw = _extract_js_single_quoted(html, "content_noencode: '")
    if not raw:
        raw = _extract_js_single_quoted(html, "content_noencode:'")
    if not raw:
        return ""
    text = _js_unescape(raw).strip()
    if not text:
        return ""
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "html.parser")
        for element in soup.select("script, style"):
            element.decompose()
        if soup.body is not None:
            return soup.body.decode_contents().strip()
        return str(soup).strip()

    paragraphs = [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]
    escaped = []
    for paragraph in paragraphs:
        paragraph = (
            paragraph.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        escaped.append(f"<p>{paragraph}</p>")
    return "".join(escaped)


def extract_article_html(html: str) -> str:
    """Return the article body HTML, or ``""`` when the page has no body."""
    soup = BeautifulSoup(html or "", "html.parser")
    content = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    if content is None:
        return _extract_content_noencode(html or "")
    for element in content.select("script, style"):
        element.decompose()
    return content.decode_contents().strip()
