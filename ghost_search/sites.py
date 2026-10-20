"""Site catalogue.

Each site says how to tell whether a profile exists for a username, using only
public profile pages or documented public APIs. A JSON site's `parse` function
receives the decoded body and returns profile details, or None when the
response means "no such user".
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from urllib.parse import quote

Check = Literal["status", "json", "absent", "present", "rule"]

DEFAULT_PATTERN = r"^[A-Za-z0-9_.-]{1,39}$"


@dataclass(frozen=True)
class Site:
    name: str
    category: str
    profile: str  # link shown to the user; {u} is the username
    probe: str | None = None  # URL actually requested, when it differs from profile
    check: Check = "status"
    marker: str | None = None  # text for "absent"/"present" checks
    parse: Callable[[Any], dict | None] | None = None
    pattern: str = DEFAULT_PATTERN
    method: Literal["GET", "POST"] = "GET"
    body: Callable[[str], dict] | None = None  # JSON body for POST probes
    known: str = ""  # a username that exists, used by `ghost-search selfcheck`
    tags: tuple[str, ...] = field(default_factory=tuple)
    # Imported sites (see catalog.py) describe detection as data instead:
    rule: dict | None = None
    headers: dict | None = None
    raw_body: str | None = None  # request body template, {u} is the username
    strip: str = ""  # characters the site drops from usernames
    source: str = "curated"
    nsfw: bool = False
    # For HTML profile pages: read the page's own og:/twitter: tags into details.
    # {"strip": regex removed from the title, "avatar": whether og:image is the user's}
    meta: dict | None = None
    # A site can also offer a genuine public API that takes an email address
    # directly (not an enumeration probe against a signup/reset form).
    email_probe: str | None = None  # URL template for the email lookup, {u} is the token
    email_profile: str | None = None  # display URL template, when the token alone forms one
    email_hash: Literal["md5"] | None = None  # transform applied to the email before filling {u}

    def accepts(self, username: str) -> bool:
        return re.fullmatch(self.pattern, username) is not None

    def fill(self, template: str, username: str) -> str:
        for ch in self.strip:
            username = username.replace(ch, "")
        return template.replace("{u}", username)

    def profile_url(self, username: str) -> str:
        return self.fill(self.profile, username)

    def probe_url(self, username: str) -> str:
        return self.fill(self.probe or self.profile, username)

    def accepts_email(self) -> bool:
        return self.email_probe is not None

    def email_token(self, email: str) -> str:
        email = email.strip().lower()
        if self.email_hash == "md5":
            return hashlib.md5(email.encode()).hexdigest()
        return quote(email, safe="")

    def email_probe_url(self, email: str) -> str:
        return self.email_probe.replace("{u}", self.email_token(email))

    def email_profile_url(self, email: str) -> str | None:
        if not self.email_profile:
            return None
        return self.email_profile.replace("{u}", self.email_token(email))


def strip_html(text: str | None, limit: int = 280) -> str | None:
    if not text:
        return None
    text = re.sub(r"<br\s*/?>|</p>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or None


def details(
    name=None, bio=None, avatar=None, location=None, followers=None, links=(), **extra
) -> dict:
    """Normalise profile details, dropping empty values."""
    out = {
        "name": name,
        "bio": strip_html(bio),
        "avatar": avatar,
        "location": location,
        "followers": followers,
        "links": [link for link in links if link],
        "extra": {k: v for k, v in extra.items() if v not in (None, "", [], {})},
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


# ---- JSON parsers -----------------------------------------------------------


def _github(d):
    twitter = d.get("twitter_username")
    blog = d.get("blog")
    if blog and not blog.startswith("http"):
        blog = "https://" + blog
    return details(
        name=d.get("name"),
        bio=d.get("bio"),
        avatar=d.get("avatar_url"),
        location=d.get("location"),
        followers=d.get("followers"),
        links=[blog, f"https://x.com/{twitter}" if twitter else None],
        repos=d.get("public_repos"),
        joined=(d.get("created_at") or "")[:10],
    )


def _gitlab(d):
    if not d:
        return None
    u = d[0]
    return details(name=u.get("name"), avatar=u.get("avatar_url"))


def _codeberg(d):
    return details(
        name=d.get("full_name"),
        bio=d.get("description"),
        avatar=d.get("avatar_url"),
        location=d.get("location"),
        followers=d.get("followers_count"),
        links=[d.get("website")],
    )


def _hackernews(d):
    if not d:
        return None
    about = d.get("about") or ""
    return details(
        bio=about,
        links=re.findall(r'href="([^"]+)"', html.unescape(about)),
        karma=d.get("karma"),
    )


def _keybase(d):
    them = (d.get("them") or [None])[0]
    if not them:
        return None
    profile = them.get("profile") or {}
    proofs = (them.get("proofs_summary") or {}).get("all") or []
    pics = them.get("pictures") or {}
    username = (them.get("basics") or {}).get("username")
    return details(
        name=profile.get("full_name"),
        bio=profile.get("bio"),
        location=profile.get("location"),
        avatar=(pics.get("primary") or {}).get("url"),
        links=[p.get("service_url") for p in proofs],
        username=username,
    )


def _devto(d):
    return details(
        name=d.get("name"),
        bio=d.get("summary"),
        avatar=d.get("profile_image"),
        location=d.get("location"),
        links=[
            d.get("website_url"),
            f"https://github.com/{d['github_username']}" if d.get("github_username") else None,
            f"https://x.com/{d['twitter_username']}" if d.get("twitter_username") else None,
        ],
    )


def _lichess(d):
    if d.get("disabled") or d.get("closed"):
        return None
    p = d.get("profile") or {}
    return details(
        name=p.get("realName") or " ".join(filter(None, [p.get("firstName"), p.get("lastName")])),
        bio=p.get("bio"),
        location=p.get("location") or p.get("flag"),
        links=(p.get("links") or "").split(),
        games=(d.get("count") or {}).get("all"),
    )


def _chesscom(d):
    country = (d.get("country") or "").rsplit("/", 1)[-1]
    return details(
        name=d.get("name"),
        avatar=d.get("avatar"),
        location=d.get("location") or country or None,
        followers=d.get("followers"),
    )


def _dockerhub(d):
    return details(name=d.get("full_name"), location=d.get("location"), company=d.get("company"))


def _mastodon(d):
    fields = d.get("fields") or []
    links = re.findall(r'href="([^"]+)"', " ".join(f.get("value", "") for f in fields))
    return details(
        name=d.get("display_name"),
        bio=d.get("note"),
        avatar=d.get("avatar"),
        followers=d.get("followers_count"),
        links=links,
    )


def _bluesky(d):
    return details(
        name=d.get("displayName"),
        bio=d.get("description"),
        avatar=d.get("avatar"),
        followers=d.get("followersCount"),
        posts=d.get("postsCount"),
    )


def _wikipedia(d):
    users = (d.get("query") or {}).get("users") or []
    if not users or "missing" in users[0] or "invalid" in users[0]:
        return None
    u = users[0]
    return details(edits=u.get("editcount"), joined=(u.get("registration") or "")[:10])


def _speedrun(d):
    u = d.get("data") or {}
    loc = ((u.get("location") or {}).get("country") or {}).get("names") or {}
    return details(
        name=(u.get("names") or {}).get("international"),
        location=loc.get("international"),
        links=[(u.get(k) or {}).get("uri") for k in ("twitch", "youtube", "twitter", "hitbox")],
    )


def _duolingo(d):
    users = d.get("users") or []
    if not users:
        return None
    u = users[0]
    pic = u.get("picture")
    return details(
        name=u.get("name"),
        bio=u.get("bio"),
        avatar=f"https:{pic}/large" if pic and pic.startswith("//") else None,
        streak=u.get("streak"),
    )


def _roblox(d):
    users = d.get("data") or []
    if not users:
        return None
    return details(name=users[0].get("displayName"), id=users[0].get("id"))


def _minecraft(d):
    if not d or "id" not in d:
        return None
    return details(name=d.get("name"), uuid=d.get("id"))


def _codewars(d):
    return details(
        name=d.get("name"),
        honor=d.get("honor"),
        clan=d.get("clan"),
    )


def _tiktok(d):
    if d.get("code"):  # oembed's own "not found" shape
        return None
    return details(name=d.get("author_name"), links=[d.get("author_url")])


def _gamejolt(d):
    u = (d.get("payload") or {}).get("user")
    if not u:
        return None
    return details(name=u.get("display_name"), avatar=u.get("img_avatar"), links=[u.get("web_site")])


def _npm(d):
    if not d.get("objects"):
        return None
    return details(packages=d.get("total"))


def _gravatar(d):
    entry = (d.get("entry") or [None])[0]
    if not entry:
        return None
    return details(
        name=entry.get("displayName"),
        bio=entry.get("aboutMe"),
        avatar=entry.get("thumbnailUrl"),
        location=entry.get("currentLocation"),
        links=[x.get("value") for x in entry.get("urls") or []]
        + [x.get("url") for x in entry.get("accounts") or []],
    )


# ---- Catalogue --------------------------------------------------------------

LOWER_DASH = r"^[a-z0-9-]{1,40}$"
LOWER_ANY = r"^[A-Za-z0-9-]{1,40}$"

SITES: list[Site] = [
    # Code
    Site("GitHub", "code", "https://github.com/{u}", "https://api.github.com/users/{u}",
         "json", parse=_github, pattern=r"^[A-Za-z0-9-]{1,39}$", known="torvalds"),
    Site("GitLab", "code", "https://gitlab.com/{u}", "https://gitlab.com/api/v4/users?username={u}",
         "json", parse=_gitlab, known="sytses"),
    Site("Codeberg", "code", "https://codeberg.org/{u}", "https://codeberg.org/api/v1/users/{u}",
         "json", parse=_codeberg, known="fnetx"),
    Site("DEV", "code", "https://dev.to/{u}", "https://dev.to/api/users/by_username?url={u}",
         "json", parse=_devto, pattern=r"^[A-Za-z0-9_]{1,30}$", known="ben"),
    Site("Docker Hub", "code", "https://hub.docker.com/u/{u}", "https://hub.docker.com/v2/users/{u}/",
         "json", parse=_dockerhub, pattern=r"^[a-z0-9]{4,30}$", known="jessfraz"),
    Site("npm", "code", "https://www.npmjs.com/~{u}",
         "https://registry.npmjs.org/-/v1/search?text=maintainer:{u}&size=1",
         "json", parse=_npm, pattern=r"^[a-z0-9._-]{1,214}$", known="sindresorhus"),
    Site("Codewars", "code", "https://www.codewars.com/users/{u}",
         "https://www.codewars.com/api/v1/users/{u}", "json", parse=_codewars, known="jhoffner"),
    Site("Hacker News", "code", "https://news.ycombinator.com/user?id={u}",
         "https://hacker-news.firebaseio.com/v0/user/{u}.json", "json", parse=_hackernews,
         pattern=r"^[A-Za-z0-9_-]{2,15}$", known="pg"),
    # Social
    Site("Mastodon (mastodon.social)", "social", "https://mastodon.social/@{u}",
         "https://mastodon.social/api/v1/accounts/lookup?acct={u}", "json", parse=_mastodon,
         pattern=r"^[A-Za-z0-9_]{1,30}$", known="Gargron"),
    Site("Bluesky", "social", "https://bsky.app/profile/{u}.bsky.social",
         "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={u}.bsky.social",
         "json", parse=_bluesky, pattern=LOWER_ANY, known="atproto"),
    Site("Keybase", "social", "https://keybase.io/{u}",
         "https://keybase.io/_/api/1.0/user/lookup.json?usernames={u}"
         "&fields=basics,profile,proofs_summary,pictures",
         "json", parse=_keybase, pattern=r"^[A-Za-z0-9_]{2,16}$", known="chris",
         email_probe="https://keybase.io/_/api/1.0/user/lookup.json?emails={u}"
         "&fields=basics,profile,proofs_summary,pictures"),
    Site("Telegram", "social", "https://t.me/{u}", check="present",
         marker='<div class="tgme_page_extra">', pattern=r"^[A-Za-z0-9_]{5,32}$", known="durov",
         meta={"avatar": True}),
    Site("Gravatar", "social", "https://gravatar.com/{u}", "https://en.gravatar.com/{u}.json",
         "json", parse=_gravatar, known="beau",
         email_probe="https://en.gravatar.com/{u}.json", email_profile="https://gravatar.com/{u}",
         email_hash="md5"),
    Site("Instagram", "social", "https://www.instagram.com/{u}/", check="present",
         marker="(&#064;", pattern=r"^[A-Za-z0-9_.]{1,30}$", known="instagram",
         meta={"avatar": True, "strip": r"\s*\(@[^)]*\).*$"}),
    Site("TikTok", "social", "https://www.tiktok.com/@{u}",
         "https://www.tiktok.com/oembed?url=https://www.tiktok.com/@{u}",
         "json", parse=_tiktok, pattern=r"^[A-Za-z0-9_.]{2,24}$", known="tiktok"),
    Site("Pinterest", "social", "https://www.pinterest.com/{u}/", check="present",
         marker='name="og:title"', pattern=r"^[A-Za-z0-9_]{3,30}$", known="pinterest",
         meta={"strip": r"\s*\([^)]*\)\s*-\s*Profile.*$"}),
    Site("Wikipedia", "social", "https://en.wikipedia.org/wiki/User:{u}",
         "https://en.wikipedia.org/w/api.php?action=query&list=users&ususers={u}"
         "&usprop=editcount|registration&format=json",
         "json", parse=_wikipedia, known="Jimbo_Wales"),
    # Games
    Site("Steam", "games", "https://steamcommunity.com/id/{u}", check="absent",
         marker="The specified profile could not be found", known="gabelogannewell",
         meta={"strip": r"^Steam Community :: "}),
    Site("Chess.com", "games", "https://www.chess.com/member/{u}",
         "https://api.chess.com/pub/player/{u}", "json", parse=_chesscom,
         pattern=r"^[A-Za-z0-9_-]{3,25}$", known="hikaru"),
    Site("Lichess", "games", "https://lichess.org/@/{u}", "https://lichess.org/api/user/{u}",
         "json", parse=_lichess, pattern=r"^[A-Za-z0-9_-]{2,30}$", known="thibault"),
    Site("Speedrun.com", "games", "https://www.speedrun.com/users/{u}",
         "https://www.speedrun.com/api/v1/users/{u}", "json", parse=_speedrun, known="cheese"),
    Site("Roblox", "games", "https://www.roblox.com/search/users?keyword={u}",
         "https://users.roblox.com/v1/usernames/users", "json", parse=_roblox, method="POST",
         body=lambda u: {"usernames": [u], "excludeBannedUsers": True},
         pattern=r"^[A-Za-z0-9_]{3,20}$", known="Roblox"),
    Site("Minecraft", "games", "https://namemc.com/profile/{u}",
         "https://api.mojang.com/users/profiles/minecraft/{u}", "json", parse=_minecraft,
         pattern=r"^[A-Za-z0-9_]{3,16}$", known="Notch"),
    Site("itch.io", "games", "https://{u}.itch.io", pattern=LOWER_DASH, known="leafo", meta={}),
    Site("osu!", "games", "https://osu.ppy.sh/u/{u}", pattern=r"^[A-Za-z0-9_ \[\]-]{2,16}$",
         known="peppy"),
    Site("Game Jolt", "games", "https://gamejolt.com/@{u}",
         "https://gamejolt.com/site-api/web/profile/@{u}", "json", parse=_gamejolt,
         pattern=r"^[A-Za-z0-9-]{3,30}$", known="gamejolt"),
    # Media & hobbies
    Site("Duolingo", "media", "https://www.duolingo.com/profile/{u}",
         "https://www.duolingo.com/2017-06-30/users?username={u}&fields=users,name,bio,picture,streak",
         "json", parse=_duolingo, known="luis"),
    Site("YouTube", "media", "https://www.youtube.com/@{u}", pattern=r"^[A-Za-z0-9_.-]{3,30}$",
         known="mkbhd", meta={"avatar": True}),
]

BY_NAME = {s.name: s for s in SITES}
