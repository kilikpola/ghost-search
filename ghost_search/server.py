"""HTTP server: a streaming scan endpoint plus the static web UI."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .scanner import MAX_DEPTH, USERNAME_RE, Result, identifier_kind, scan
from .sites import SITES

app = FastAPI(title="Ghost Search")


@app.get("/api/sites")
def list_sites():
    return [{"name": s.name, "category": s.category, "email": s.accepts_email()} for s in SITES]


@app.get("/api/scan")
async def run_scan(request: Request, u: str = Query(..., max_length=320),
                   depth: int = Query(0, ge=0, le=MAX_DEPTH)):
    u = u.strip()
    if identifier_kind(u) == "username" and not USERNAME_RE.fullmatch(u):
        raise HTTPException(422, "enter a username, an email address, or a phone number")

    async def events():
        async for item in scan(u, depth):
            if await request.is_disconnected():
                break
            if isinstance(item, Result):
                yield f"event: result\ndata: {json.dumps(item.to_dict())}\n\n"
            else:
                yield f"event: queued\ndata: {json.dumps(item)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
