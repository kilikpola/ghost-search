"""Probe sites for a username and, optionally, follow handles found on profiles."""

from __future__ import annotations

import asyncio
import html as html_lib
import os
import re
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator, Iterable
from urllib.parse import urlparse

import httpx

from .sites import SITES, Site, details as make_details

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) GhostSearch/0.1 (public profile lookup)"
TIMEOUT = httpx.Timeout(12.0, connect=6.0)
CONCURRENCY = 12
MAX_DEPTH = 2
MAX_FOLLOWED = 8  # extra usernames a single scan may follow

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s().]{6,}$")
# Interstitial pages some sites serve to scripts instead of the real profile.
BOT_WALL = re.compile(r"<title>\s*(Client Challenge|Just a moment|Attention Required)", re.I)


def identifier_kind(value: str) -> str:
    """Classify a search term as "email", "phone" or "username"."""
    value = value.strip()
    if EMAIL_RE.fullmatch(value):
        return "email"
    if PHONE_RE.fullmatch(value) and sum(c.isdigit() for c in value) >= 7:
        return "phone"
    return "username"


@dataclass
class Result:
    site: str
    category: str
    username: str
    url: str
    status: str  # found | missing | error | skipped
    depth: int = 0
    via: str | None = None  # "Site:username" whose profile linked here
    details: dict = field(default_factory=dict)
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"},
        timeout=TIMEOUT,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=CONCURRENCY * 2),
    )


def _auth_headers(site: Site) -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    if site.name == "GitHub" and token:
        return {"Authorization": f"Bearer {token}"}
    return {}


async def probe(client: httpx.AsyncClient, site: Site, identifier: str,
                depth: int = 0, via: str | None = None, kind: str = "username") -> Result:
    if kind == "email":
        if not site.accepts_email():
            return Result(site.name, site.category, identifier, "", "skipped", depth, via,
                          note="no email lookup for this site")
        probe_url = site.email_probe_url(identifier)
        url = site.email_profile_url(identifier) or probe_url
        result = Result(site.name, site.category, identifier, url, "missing", depth, via)
    else:
        probe_url = site.probe_url(identifier)
        result = Result(site.name, site.category, identifier, site.profile_url(identifier),
                        "missing", depth, via)
        if not site.accepts(identifier):
            result.status, result.note = "skipped", "username not valid on this site"
            return result
    for attempt in range(2):
        try:
            if site.method == "POST":
                resp = await client.post(probe_url, json=site.body(identifier),
                                         headers=_auth_headers(site))
            else:
                resp = await client.get(probe_url, headers=_auth_headers(site))
            break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if attempt:
                result.status, result.note = "error", type(exc).__name__
                return result
        except httpx.HTTPError as exc:
            result.status, result.note = "error", type(exc).__name__
            return result

    code = resp.status_code
    if code in (404, 410) or (site.check == "json" and code in (204, 400)):
        return result
    if code == 429 or code >= 500 or code in (401, 403):
        result.status, result.note = "error", f"HTTP {code}"
        return result
    if site.check != "json" and BOT_WALL.search(resp.text[:4000]):
        result.status, result.note = "error", "blocked by a bot check"
        return result

    if site.check == "status":
        result.status = "found" if code == 200 else "missing"
    elif site.check == "absent":
        result.status = "missing" if site.marker in resp.text else "found"
    elif site.check == "present":
        result.status = "found" if site.marker in resp.text else "missing"
    if site.check in ("status", "absent", "present"):
        if result.status == "found" and site.meta is not None:
            result.details = page_details(resp.text, site.meta)
    else:
        try:
            info = site.parse(resp.json())
        except ValueError:
            result.status, result.note = "error", "unexpected response"
            return result
        if info is not None:
            result.status, result.details = "found", info
            found_username = info.get("extra", {}).get("username")
            if kind == "email" and found_username and site.profile:
                result.url = site.profile_url(found_username)
    return result


# ---- Profile details from HTML pages ----------------------------------------

META_TAG = re.compile(r"<meta\s[^>]*>", re.I)
META_ATTR = re.compile(r'(property|name|content)\s*=\s*"([^"]*)"', re.I)


def page_meta(html_text: str) -> dict[str, str]:
    """The page's og:/twitter: meta tags, first value wins.

    Some sites (Pinterest, YouTube) serve megabytes of script/preload tags
    before their og:/twitter: tags, which can land well outside <head> (seen
    past 1.2MB on Pinterest), so the scan window has to be generous.
    """
    tags: dict[str, str] = {}
    for tag in META_TAG.findall(html_text[:2_000_000]):
        attrs = {k.lower(): v for k, v in META_ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and attrs.get("content") and key not in tags:
            tags[key] = attrs["content"]
    return tags


def page_details(html_text: str, opts: dict) -> dict:
    """Name, bio, avatar and linked X account as the profile page describes itself."""
    tags = {k: html_lib.unescape(v).strip() for k, v in page_meta(html_text).items()}
    name = tags.get("og:title") or tags.get("twitter:title")
    if name and opts.get("strip"):
        name = re.sub(opts["strip"], "", name).strip()
    bio = tags.get("og:description") or tags.get("twitter:description")
    avatar = (tags.get("og:image") or tags.get("twitter:image")) if opts.get("avatar") else None
    creator = (tags.get("twitter:creator") or "").lstrip("@")
    links = [f"https://x.com/{creator}"] if re.fullmatch(r"[A-Za-z0-9_]{1,15}", creator) else []
    return make_details(name=name or None, bio=bio or None, avatar=avatar, links=links)


# ---- Following linked handles -----------------------------------------------

# host -> regex over the URL path that captures a username
HANDLE_PATTERNS: dict[str, re.Pattern] = {
    host: re.compile(rx)
    for host, rx in {
        "github.com": r"^/([A-Za-z0-9-]{1,39})/?$",
        "gitlab.com": r"^/([A-Za-z0-9_.-]{2,40})/?$",
        "codeberg.org": r"^/([A-Za-z0-9_.-]{2,40})/?$",
        "twitter.com": r"^/([A-Za-z0-9_]{1,15})/?$",
        "x.com": r"^/([A-Za-z0-9_]{1,15})/?$",
        "instagram.com": r"^/([A-Za-z0-9_.]{1,30})/?$",
        "youtube.com": r"^/@([A-Za-z0-9_.-]{3,30})/?$",
        "tiktok.com": r"^/@([A-Za-z0-9_.]{2,24})/?$",
        "twitch.tv": r"^/([A-Za-z0-9_]{4,25})/?$",
        "reddit.com": r"^/(?:u|user)/([A-Za-z0-9_-]{3,20})/?$",
        "t.me": r"^/([A-Za-z0-9_]{5,32})/?$",
        "keybase.io": r"^/([A-Za-z0-9_]{2,16})/?$",
        "dev.to": r"^/([A-Za-z0-9_]{1,30})/?$",
        "medium.com": r"^/@([A-Za-z0-9_.-]{1,30})/?$",
        "linktr.ee": r"^/([A-Za-z0-9_.]{2,30})/?$",
        "mastodon.social": r"^/@([A-Za-z0-9_]{1,30})/?$",
        "bsky.app": r"^/profile/([a-z0-9-]{1,40})\.bsky\.social/?$",
    }.items()
}
RESERVED = {"about", "explore", "home", "login", "settings", "search", "share", "intent",
            "watch", "channel", "user", "users", "orgs", "topics", "sponsors", "p", "i"}


def handles_in(links: Iterable[str]) -> set[str]:
    """Usernames that appear in profile links to well-known sites."""
    found = set()
    for link in links:
        try:
            parsed = urlparse(link if "://" in link else "https://" + link)
        except ValueError:
            continue
        host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")
        rx = HANDLE_PATTERNS.get(host)
        if not rx:
            continue
        m = rx.match(parsed.path)
        if m and m.group(1).lower() not in RESERVED:
            found.add(m.group(1))
    return found


async def scan(identifier: str, depth: int = 0, sites: list[Site] | None = None,
               client: httpx.AsyncClient | None = None) -> AsyncIterator[Result | dict]:
    """Yield results as they complete.

    `identifier` is a username, email address, or phone number; the kind is
    detected automatically and only sites that support it are probed (today,
    only Gravatar and Keybase have genuine public lookup-by-email APIs, and no
    site exposes a public lookup-by-phone API, so phone searches return no
    results). With depth > 0, usernames found in the links of matched profiles
    are scanned too, up to MAX_DEPTH levels and MAX_FOLLOWED extra usernames.
    Besides Result objects, yields {"event": "queued", ...} dicts when a new
    identifier joins the scan, so callers can show progress.
    """
    identifier = identifier.strip()
    kind = identifier_kind(identifier)
    if kind == "username" and not USERNAME_RE.fullmatch(identifier):
        raise ValueError("usernames may only use letters, digits, '.', '_' and '-'")
    all_sites = sites or SITES
    depth = max(0, min(depth, MAX_DEPTH))
    own_client = client is None
    client = client or make_client()
    sem = asyncio.Semaphore(CONCURRENCY)
    seen = {identifier.lower()}
    level: list[tuple[str, str | None, str]] = [(identifier, None, kind)]

    async def guarded(site, name, d, via, k):
        async with sem:
            return await probe(client, site, name, d, via, k)

    def sites_for(k: str) -> list[Site]:
        if k == "email":
            return [s for s in all_sites if s.accepts_email()]
        if k == "phone":
            return []
        return all_sites

    try:
        for d in range(depth + 1):
            tasks = [asyncio.create_task(guarded(site, name, d, via, k))
                     for name, via, k in level for site in sites_for(k)]
            yield {"event": "queued", "usernames": [n for n, _, _ in level], "checks": len(tasks),
                   "depth": d}
            next_level: list[tuple[str, str | None, str]] = []
            try:
                for fut in asyncio.as_completed(tasks):
                    res = await fut
                    yield res
                    if res.status != "found" or d >= depth:
                        continue
                    for handle in sorted(handles_in(res.details.get("links", []))):
                        if handle.lower() in seen or len(seen) > MAX_FOLLOWED:
                            continue
                        if USERNAME_RE.fullmatch(handle):
                            seen.add(handle.lower())
                            next_level.append((handle, f"{res.site}:{res.username}", "username"))
            finally:
                for t in tasks:
                    t.cancel()
            if not next_level:
                break
            level = next_level
    finally:
        if own_client:
            await client.aclose()
