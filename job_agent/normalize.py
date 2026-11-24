from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query params that only track where a click came from. Anything else
# (e.g. Indeed's "jk", Greenhouse's "gh_jid") identifies the job and is kept.
TRACKING_PARAMS = {
    "source", "src", "ref", "referrer", "refid", "trk", "trackingid",
    "jclickid", "publisher", "gh_src", "lever-source", "lever-origin",
    "sourcetype", "locationcountry", "locations", "domain", "from",
    "campaign", "cid", "mode", "iis", "iisn",
}


def normalize_url(url: str) -> str:
    """Canonical form of a job URL for duplicate detection."""
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower()
    if not parts.scheme and not parts.netloc:
        return url.lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parts.path) or "/"
    # Workday URLs exist with and without a language segment (/en-US/).
    if "myworkdayjobs.com" in host:
        path = re.sub(r"^/[a-z]{2}-[A-Z]{2}(?=/)", "", path)
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query.sort()
    return urlunsplit(("https", host, path.lower(), urlencode(query), ""))


def company_title_key(company: str, title: str) -> str:
    def clean(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()
    return f"{clean(company)}|{clean(title)}"


def html_to_text(html: str) -> str:
    """Strip HTML to readable plain text (no network)."""
    if not html:
        return ""
    import html as htmllib
    from bs4 import BeautifulSoup

    text = BeautifulSoup(htmllib.unescape(html), "html.parser").get_text("\n")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
