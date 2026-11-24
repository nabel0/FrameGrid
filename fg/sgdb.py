# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal SteamGridDB API v2 client (standard library only)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("FRAMEGRID_SGDB_BASE", "https://www.steamgriddb.com/api/v2")
UA = "FrameGrid/0.3.1 (+SteamOS)"

# slot -> (endpoint, dimensiones)
SLOT_QUERY = {
    "capsule": ("grids", "600x900,342x482,660x930"),
    "wide": ("grids", "460x215,920x430"),
    "hero": ("heroes", None),
    "logo": ("logos", None),
    "icon": ("icons", None),
}
# Steam does not reliably load WebP/ICO files from the grid folder: use PNG/JPG (PNG includes animated APNG).
MIMES = {
    "grids": "image/png,image/jpeg",
    "heroes": "image/png,image/jpeg",
    "logos": "image/png",
    "icons": "image/png",
}


class SGDBError(Exception):
    pass


class SGDB:
    def __init__(self, api_key: str, timeout=20):
        self.key = api_key.strip()
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.key}", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SGDBError("Invalid SteamGridDB API key (401).")
            if e.code == 404:
                return []
            raise SGDBError(f"SteamGridDB returned {e.code}")
        except urllib.error.URLError as e:
            raise SGDBError(f"Could not connect to SteamGridDB: {e.reason}")
        if not body.get("success", False):
            raise SGDBError("; ".join(body.get("errors", ["Unknown error"])))
        return body.get("data", [])

    def search(self, term: str) -> list[dict]:
        return self._get("/search/autocomplete/" + urllib.parse.quote(term, safe=""))

    def game_by_steam_appid(self, appid: int) -> dict | None:
        d = self._get(f"/games/steam/{int(appid)}")
        return d or None

    def assets(self, slot: str, game_id: int, page=0, animated=False, nsfw=False, humor=False) -> list[dict]:
        kind, dims = SLOT_QUERY[slot]
        params = {
            "page": page,
            "nsfw": "any" if nsfw else "false",
            "humor": "any" if humor else "false",
            "types": "static,animated" if animated else "static",
            "mimes": MIMES[kind],
        }
        if dims:
            params["dimensions"] = dims
        data = self._get(f"/{kind}/game/{int(game_id)}", params)
        return [{
            "id": a.get("id"),
            "url": a.get("url"),
            "thumb": a.get("thumb") or a.get("url"),
            "width": a.get("width"),
            "height": a.get("height"),
            "style": a.get("style"),
            "mime": a.get("mime"),
            "score": a.get("score"),
            "author": (a.get("author") or {}).get("name"),
        } for a in data]


def download(url: str, timeout=60) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type", "")
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lstrip(".").lower()
    if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(ctype.split(";")[0], "png")
    # Check the file signature in case the extension is misleading
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = "webp"
    return data, ext
