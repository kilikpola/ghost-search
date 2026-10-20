import httpx
import pytest

from ghost_search.scanner import Result, handles_in, identifier_kind, probe, scan
from ghost_search.sites import Site, _github, details


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


STATUS_SITE = Site("Plain", "code", "https://plain.test/{u}", known="x")
ABSENT_SITE = Site("Absent", "games", "https://absent.test/{u}", check="absent", marker="no such user")
JSON_SITE = Site("Api", "code", "https://api.test/{u}", check="json", parse=_github)


async def test_status_check():
    async with client_for(lambda r: httpx.Response(200 if r.url.path == "/real" else 404)) as c:
        assert (await probe(c, STATUS_SITE, "real")).status == "found"
        assert (await probe(c, STATUS_SITE, "ghost")).status == "missing"


async def test_absent_marker():
    def handler(r):
        return httpx.Response(200, text="no such user" if r.url.path == "/ghost" else "<h1>hi</h1>")
    async with client_for(handler) as c:
        assert (await probe(c, ABSENT_SITE, "real")).status == "found"
        assert (await probe(c, ABSENT_SITE, "ghost")).status == "missing"


async def test_bot_wall_and_rate_limit_are_errors():
    async with client_for(lambda r: httpx.Response(200, text="<title>Just a moment...</title>")) as c:
        assert (await probe(c, STATUS_SITE, "real")).status == "error"
    async with client_for(lambda r: httpx.Response(429)) as c:
        assert (await probe(c, STATUS_SITE, "real")).status == "error"


async def test_json_details_and_skip_invalid_names():
    body = {"name": "Ada", "bio": "<b>maths</b>", "followers": 3, "twitter_username": "ada"}
    async with client_for(lambda r: httpx.Response(200, json=body)) as c:
        res = await probe(c, JSON_SITE, "ada")
        assert res.status == "found"
        assert res.details["bio"] == "maths"
        assert "https://x.com/ada" in res.details["links"]
        site = Site("Strict", "code", "https://s.test/{u}", pattern=r"^[a-z]+$")
        assert (await probe(c, site, "Has.Dot")).status == "skipped"


def test_handles_in():
    links = ["https://github.com/ada", "x.com/ada_l", "https://twitter.com/intent",
             "https://example.com/ada", "https://www.youtube.com/@adal", "not a url"]
    assert handles_in(links) == {"ada", "ada_l", "adal"}


async def test_scan_follows_linked_handles():
    def handler(r):
        if r.url.path == "/start":
            return httpx.Response(200, json={"twitter_username": "second"})
        if r.url.path == "/second":
            return httpx.Response(200, json={"name": "Second"})
        return httpx.Response(404)
    async with client_for(handler) as c:
        items = [i async for i in scan("start", depth=1, sites=[JSON_SITE], client=c)]
    results = [i for i in items if isinstance(i, Result)]
    assert [(r.username, r.status, r.via) for r in results] == [
        ("start", "found", None), ("second", "found", "Api:start")]

    async with client_for(handler) as c:
        items = [i async for i in scan("start", depth=0, sites=[JSON_SITE], client=c)]
    assert len([i for i in items if isinstance(i, Result)]) == 1


async def test_scan_rejects_bad_username():
    with pytest.raises(ValueError):
        [i async for i in scan("bad name/../")]


async def test_html_profile_details():
    page = ('<meta property="og:title" content="Steam Community :: Ada &amp; Co">'
            '<meta property="og:description" content="Hi there">'
            '<meta property="og:image" content="https://cdn.test/a.png">'
            '<meta name="twitter:creator" content="@ada_l">')
    site = Site("Page", "games", "https://page.test/{u}", meta={"strip": r"^Steam Community :: "})
    async with client_for(lambda r: httpx.Response(200, text=page)) as c:
        res = await probe(c, site, "ada")
    assert res.details == {"name": "Ada & Co", "bio": "Hi there", "links": ["https://x.com/ada_l"]}
    with_avatar = Site("Page", "games", "https://page.test/{u}", meta={"avatar": True})
    async with client_for(lambda r: httpx.Response(200, text=page)) as c:
        assert (await probe(c, with_avatar, "ada")).details["avatar"] == "https://cdn.test/a.png"


def test_identifier_kind():
    assert identifier_kind("ada_l") == "username"
    assert identifier_kind("ada@example.com") == "email"
    assert identifier_kind("+1 415-555-0100") == "phone"
    assert identifier_kind("415-555-0100") == "phone"


async def test_email_probe_hashes_and_skips_non_email_sites():
    import hashlib
    digest = hashlib.md5(b"ada@example.com").hexdigest()
    EMAIL_SITE = Site("Mail", "social", "https://mail.test/{u}", "https://mail.test/api/{u}",
                      check="json", parse=lambda d: details(name="Ada") if d.get("found") else None,
                      email_probe="https://mail.test/api/{u}", email_profile="https://mail.test/{u}",
                      email_hash="md5")

    def handler(r):
        assert r.url.path == f"/api/{digest}"  # lowercased and hashed before filling {u}
        return httpx.Response(200, json={"found": True})

    async with client_for(handler) as c:
        res = await probe(c, EMAIL_SITE, "Ada@Example.com", kind="email")
        assert res.status == "found"
        assert res.details["name"] == "Ada"
        assert res.url == f"https://mail.test/{digest}"

    async with client_for(lambda r: httpx.Response(200)) as c:
        res = await probe(c, STATUS_SITE, "ada@example.com", kind="email")
        assert res.status == "skipped"


async def test_scan_recomputes_url_from_discovered_username():
    def by_email(d):
        return details(name="Ada", username="ada_real") if d.get("emails") else None

    EMAIL_SITE = Site("Mail", "social", "https://mail.test/{u}",
                      "https://mail.test/api?emails={u}", check="json", parse=by_email,
                      email_probe="https://mail.test/api?emails={u}")

    async with client_for(lambda r: httpx.Response(200, json={"emails": True})) as c:
        items = [i async for i in scan("ada@example.com", sites=[EMAIL_SITE], client=c)]
    results = [i for i in items if isinstance(i, Result)]
    assert len(results) == 1
    assert results[0].url == "https://mail.test/ada_real"


async def test_scan_phone_has_no_sites():
    items = [i async for i in scan("415-555-0100", sites=[JSON_SITE])]
    results = [i for i in items if isinstance(i, Result)]
    assert results == []
