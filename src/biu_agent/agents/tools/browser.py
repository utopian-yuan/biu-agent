"""A read-only text browser for explicit public HTTPS pages and their same-site links."""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import quote, urldefrag, urljoin, urlsplit, urlunsplit

from ...identity import Problem

MAX_PAGE = 512 * 1024


def address(url):
    if len(url) > 1500 or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
        raise Problem(422, "browser_url", "网页地址格式无效。")
    try:
        p = urlsplit(urldefrag(url).url)
        hostname = (p.hostname or "").encode("idna").decode("ascii").lower()
        if (
            p.scheme != "https"
            or not hostname
            or p.username is not None
            or p.password is not None
            or p.port not in (None, 443)
        ):
            raise ValueError
        host = "[" + hostname + "]" if ":" in hostname else hostname
        return urlunsplit(
            (
                "https",
                host,
                quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                quote(p.query, safe="=&%?/:;+,@!$'()*-._~"),
                "",
            )
        )
    except (ValueError, UnicodeError):
        raise Problem(422, "browser_url", "请输入标准 HTTPS 网页地址。") from None


def granted_urls(text):
    found = set()
    for item in re.findall(r"https://[^\s<>\"'，。；：！？、（）【】「」“”《》]+", text):
        item = item.rstrip(".,;:!?]}")
        while item.endswith(")") and item.count(")") > item.count("("):
            item = item[:-1]
        try:
            found.add(address(item))
        except Problem:
            continue
    return found


def connect_public(endpoint, timeout, source_address=None):
    addresses = socket.getaddrinfo(*endpoint, type=socket.SOCK_STREAM)
    for _, _, _, _, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            not ip.is_global
            or ip.is_multicast
            or ip.is_reserved
            or getattr(ip, "ipv4_mapped", None)
            or getattr(ip, "sixtofour", None)
            or getattr(ip, "teredo", None)
        ):
            raise Problem(403, "browser_scope", "此网页不属于可访问的公开地址。")
    # Connect to the checked numeric endpoint. HTTPSConnection still verifies TLS against the hostname.
    for family, kind, proto, _, sockaddr in addresses:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError:
            sock.close()
    raise OSError("public page unavailable")


def fetch(url):
    original = urlsplit(url).netloc
    try:
        for _ in range(3):
            parsed = urlsplit(url)
            if parsed.netloc != original:
                raise Problem(403, "browser_scope", "网页跳转到其他站点，请在问题中给出目标网址。")
            conn = http.client.HTTPSConnection(
                parsed.hostname, timeout=4, context=ssl.create_default_context()
            )
            conn._create_connection = connect_public
            try:
                path = urlunsplit(("", "", parsed.path, parsed.query, ""))
                conn.request(
                    "GET",
                    path,
                    headers={"Accept": "text/html,text/plain", "User-Agent": "biu-agent/0.1 (text browser)"},
                )
                response = conn.getresponse()
                if response.status in {301, 302, 303, 307, 308} and response.getheader("Location"):
                    url = address(urljoin(url, response.getheader("Location")))
                    continue
                if not 200 <= response.status < 300:
                    raise Problem(422, "browser_http", f"网页返回 HTTP {response.status}。")
                mime = response.headers.get_content_type()
                if mime not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise Problem(415, "browser_type", "此地址没有返回网页或纯文本。")
                body = response.read(MAX_PAGE + 1)
                if len(body) > MAX_PAGE:
                    raise Problem(413, "browser_size", "网页正文超过 512 KiB。")
                content = body.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                return url, mime, content
            finally:
                conn.close()
    except (OSError, http.client.HTTPException, LookupError, UnicodeError):
        raise Problem(503, "browser_offline", "暂时无法读取网页，请检查地址或网络。") from None
    raise Problem(422, "browser_redirect", "网页跳转次数过多。")


class VisibleText(HTMLParser):
    block = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "pre",
        "blockquote",
        "hr",
    }
    void = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []
        self.title = []
        self.in_title = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        if (
            self.hidden
            or tag in {"head", "script", "style", "template", "noscript", "svg"}
            or "hidden" in values
            or values.get("aria-hidden") == "true"
        ):
            if tag not in self.void:
                self.hidden.append(tag)
            return
        if tag in self.block:
            self.parts.append("\n")
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.hidden:
            if self.hidden[-1] == tag:
                self.hidden.pop()
        elif tag in self.block:
            self.parts.append("\n")

    def handle_data(self, text):
        if self.in_title:
            self.title.append(text)
        elif not self.hidden:
            self.parts.append(text)


class TextBrowser:
    def __init__(self, question):
        self.allowed = granted_urls(question)
        self.cache = {}

    def open(self, target):
        url = address(target)
        if url not in self.allowed:
            raise Problem(403, "browser_scope", "请在当前问题中给出网页地址，或打开本轮已列出的同站链接。")
        if url in self.cache:
            return self.cache[url]
        final, mime, raw = fetch(url)
        links = []
        title = urlsplit(final).hostname
        text = raw
        if mime != "text/plain":
            parser = VisibleText()
            parser.feed(raw)
            parser.close()
            text = "".join(parser.parts)
            title = " ".join("".join(parser.title).split())[:96] or title
            for href in parser.links:
                try:
                    link = address(urljoin(final, href))
                except (Problem, ValueError):
                    continue
                if (
                    urlsplit(link).netloc == urlsplit(final).netloc
                    and link not in links
                    and len(link) <= 400
                    and link != final
                ):
                    links.append(link)
                    if len(links) == 6:
                        break
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        if not lines:
            raise Problem(422, "browser_empty", "未取得可阅读的网页正文。")
        parts = [line[i : i + 800] for line in lines for i in range(0, len(line), 800)]
        digest = hashlib.sha256(raw.encode()).hexdigest()
        key = hashlib.sha256((final + digest).encode()).hexdigest()[:20]
        doc = {
            "id": "web_" + key,
            "title": title,
            "sha256": digest,
            "url": final,
            "sections": {f"L{i}": line for i, line in enumerate(parts, 1)},
        }
        self.allowed.add(final)
        self.cache[url] = self.cache[final] = doc, links
        return doc, links
