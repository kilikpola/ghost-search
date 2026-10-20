"""Command line: `ghost-search serve`, `ghost-search scan NAME`, `ghost-search selfcheck`."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets

from .scanner import Result, make_client, probe, scan
from .sites import SITES


async def _scan(name: str, depth: int, as_json: bool) -> None:
    found = []
    async for item in scan(name, depth):
        if not isinstance(item, Result):
            if not as_json:
                print(f"-- checking {', '.join(item['usernames'])} on {item['checks']} sites")
            continue
        if as_json:
            print(json.dumps(item.to_dict()))
        elif item.status == "found":
            found.append(item)
            via = f"  (via {item.via})" if item.via else ""
            print(f"  + {item.site:<28} {item.url}{via}")
        elif item.status == "error":
            print(f"  ? {item.site:<28} {item.note}")
    if not as_json:
        print(f"{len(found)} profile(s) found")


async def _selfcheck() -> int:
    """Each site should find its known user and miss a random one."""
    bogus = "tm" + secrets.token_hex(5)
    failures = 0
    async with make_client() as client:
        async def one(site):
            hit, miss = await asyncio.gather(probe(client, site, site.known),
                                             probe(client, site, bogus))
            return site, hit, miss
        for site, hit, miss in await asyncio.gather(*(one(s) for s in SITES)):
            ok = hit.status == "found" and miss.status == "missing"
            failures += not ok
            mark = "ok  " if ok else "FAIL"
            print(f"{mark} {site.name:<28} known={hit.status:<8} random={miss.status:<8} "
                  f"{hit.note or miss.note or ''}")
    print(f"{len(SITES) - failures}/{len(SITES)} sites behave as expected")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="ghost-search")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8040)
    c = sub.add_parser("scan", help="scan a username, email, or phone number from the terminal")
    c.add_argument("username", help="a username, email address, or phone number")
    c.add_argument("--depth", type=int, default=0, help="follow linked handles (0-2)")
    c.add_argument("--json", action="store_true", help="one JSON object per line")
    sub.add_parser("selfcheck", help="verify every site's detection rule")
    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("ghost_search.server:app", host=args.host, port=args.port)
    elif args.cmd == "scan":
        asyncio.run(_scan(args.username, args.depth, args.json))
    else:
        raise SystemExit(asyncio.run(_selfcheck()))


if __name__ == "__main__":
    main()
