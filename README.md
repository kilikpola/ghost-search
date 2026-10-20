# Ghost Search

Checks which public sites a username, email address, or phone number is
registered on, and can optionally follow handles that matched profiles link
to (up to 2 hops).

Only public profile pages and documented public APIs are used. Nothing is stored.
A match is not proof the accounts belong to the same person.

The kind of search term is detected automatically:

- **Username**: checked against every site in the catalogue.
- **Email**: checked only against sites with a genuine public lookup-by-email
  API — today that's Gravatar (its documented email-hash endpoint) and
  Keybase (its documented email-lookup endpoint). Other sites aren't probed
  by email, because doing so would mean querying signup or password-reset
  endpoints to test whether an address is registered — undocumented account
  enumeration, not a public API, and often against a site's terms of service.
- **Phone**: recognised, but checked against nothing yet. No site in the
  catalogue offers a public, documented lookup-by-phone API; adding phone
  coverage means adding a `Site` with one, not probing arbitrary endpoints.

## Run

    uv sync
    uv run ghost-search serve            # web UI on http://127.0.0.1:8040
    uv run ghost-search scan ada --depth 1
    uv run ghost-search scan ada@example.com
    uv run ghost-search selfcheck        # verify every site's detection rule
    uv run --group dev pytest

Set `GITHUB_TOKEN` to raise GitHub's anonymous limit of 60 requests an hour.

## Adding a site

Add a `Site(...)` to `ghost-search/sites.py` with a `known` username that exists,
then run `ghost-search selfcheck`. Detection modes:

- `status`: profile URL returns 200, missing user returns 404
- `json`: a public API; `parse` returns details or None for "no such user"
- `absent` / `present`: look for a marker string in the page

Sites that serve bot challenges to scripts (PyPI, Last.fm, Letterboxd) are
deliberately left out, because their answers can't be trusted.

## API

- `GET /api/sites`: site list, each with whether it supports email lookup
- `GET /api/scan?u=NAME&depth=0..2`: server-sent events (`queued`, `result`, `done`);
  `u` may be a username, email address, or phone number
