from __future__ import annotations

import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def get(session, url, *, retries=1, timeout=25, **kw):
    """GET with one retry on network errors / 429 / 5xx. Never raises;
    returns None if every attempt failed at the network level."""
    last = None
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=timeout, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                last = r
                continue
            return r
        except requests.RequestException:
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    return last


def post_json(session, url, payload, *, retries=1, timeout=25):
    last = None
    for attempt in range(retries + 1):
        try:
            r = session.post(url, json=payload, timeout=timeout,
                             headers={"Content-Type": "application/json",
                                      "Accept": "application/json"})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                last = r
                continue
            return r
        except requests.RequestException:
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    return last
