#!/usr/bin/env python3
# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""FrameGrid — SteamGridDB artwork and collections manager for Steam Frame / SteamOS.

Starts a local server on 127.0.0.1 and opens the interface in a browser
(or Steam's built-in browser). Uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fg import sgdb, steam  # noqa: E402
from fg.collections import Collections  # noqa: E402
from fg.live import SteamLive  # noqa: E402
from fg import imgutil  # noqa: E402
import base64  # noqa: E402

VERSION = "0.3.1"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "framegrid"
CONFIG_FILE = CONFIG_DIR / "config.json"


# ---------------------------------------------------------------- config
def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), "utf-8")
    os.chmod(CONFIG_FILE, 0o600)


class App:
    def __init__(self, steam_root_override=None):
        self.cfg = load_config()
        if steam_root_override:
            self.cfg["steam_root"] = steam_root_override
        self.lock = threading.Lock()

    @property
    def root(self):
        return steam.find_steam_root(self.cfg.get("steam_root"))

    @property
    def live(self) -> SteamLive:
        return SteamLive(int(self.cfg.get("cef_port") or 8080))

    def user(self) -> steam.SteamUser:
        root = self.root
        if not root:
            raise RuntimeError("Could not find the Steam installation (userdata folder).")
        users = steam.list_users(root)
        uid = self.cfg.get("user")
        if not uid or not any(u["id"] == uid for u in users):
            if not users:
                raise RuntimeError("No users found in Steam/userdata. Sign in to Steam once.")
            uid = users[0]["id"]
        return steam.SteamUser(root, uid)

    def sgdb(self) -> sgdb.SGDB:
        key = self.cfg.get("api_key")
        if not key:
            raise RuntimeError("Missing SteamGridDB API key (Settings).")
        return sgdb.SGDB(key)

    # ------------------------------------------------ modo de trabajo
    def mode(self) -> str:
        """live = Steam running with the CEF debugger (like Decky) · files = Steam closed · limited = running without the debugger."""
        if self.live.available():
            return "live"
        return "limited" if steam.steam_running() else "files"

    def need_write_mode(self) -> str:
        m = self.mode()
        if m == "limited":
            raise RuntimeError("Steam is running but its CEF debugger is not active. Click “Enable live mode” "
                               "and restart Steam Frame (or enable Settings › Developer › CEF Remote Debugging).")
        return m

    def cef_flag_paths(self) -> list[Path]:
        paths = []
        for p in (self.root, Path.home() / ".steam/steam"):
            if p and p.exists():
                f = p.resolve() / ".cef-enable-remote-debugging"
                if f not in paths:
                    paths.append(f)
        return paths

    def enable_cef(self) -> list[str]:
        out = []
        for f in self.cef_flag_paths():
            f.touch(exist_ok=True)
            out.append(str(f))
        return out

    # ------------------------------------------------ acciones
    def state(self):
        root = self.root
        users = steam.list_users(root) if root else []
        cur = None
        try:
            cur = self.user().uid
        except Exception:
            pass
        return {
            "version": VERSION,
            "steam_root": str(root) if root else None,
            "users": users,
            "user": cur,
            "steam_running": steam.steam_running(),
            "mode": self.mode(),
            "cef_flag": any(f.exists() for f in self.cef_flag_paths()),
            "has_key": bool(self.cfg.get("api_key")),
        }

    def library(self):
        u = self.user()
        try:
            vdf_sc = {s["appid"]: s for s in u.shortcuts()}
        except Exception:
            vdf_sc = {}
        shortcuts = list(vdf_sc.values())
        if self.mode() == "live":
            try:
                live_sc = self.live.shortcuts()
                shortcuts = [{**vdf_sc.get(s["appid"], {}), **s} for s in live_sc]
            except Exception:
                pass
        items = shortcuts + u.steam_games()
        for it in items:
            it["art"] = u.art_status(it["appid"])
        return items

    def _apply_data(self, u: steam.SteamUser, appid: int, slot: str, data: bytes, ext: str, kind: str, mode: str):
        if ext == "webp":
            raise RuntimeError("Steam does not load WebP from the grid folder; choose a PNG/JPG image.")
        if slot == "icon":
            if kind == "steam":
                if mode != "live":
                    raise RuntimeError("Changing a Steam game's icon requires live mode.")
                h = self.live.icon_hash(appid)
                if not h:
                    raise RuntimeError("Steam did not provide this game's icon_hash.")
                return {"path": str(u.poison_steam_icon(appid, h, data)), "note": None}
            path = u.write_art(appid, "icon", data, ext)
            if mode == "live":
                self.live.set_shortcut_icon(appid, str(path))
                return {"path": str(path), "note": None}
            if mode == "files":
                u.sync_shortcut_icons()
                return {"path": str(path), "note": None}
            return {"path": str(path), "note": "Icon saved. It will be linked to the shortcut when you enable live mode."}
        if mode == "live":
            logo_pos = slot == "logo" and kind == "shortcut" and not (u.grid / f"{appid}.json").exists()
            self.live.set_artwork(appid, slot, data, ext, logo_pos)
            return {"path": None, "note": None}
        path = u.write_art(appid, slot, data, ext)
        note = "Saved to the grid folder; it will appear after you restart Steam Frame." if mode == "limited" else None
        return {"path": str(path), "note": note}

    def apply(self, appid: int, slot: str, url: str, kind: str):
        data, ext = sgdb.download(url)
        return self._apply_data(self.user(), appid, slot, data, ext, kind, self.mode())

    def apply_bytes(self, appid: int, slot: str, data: bytes, kind: str):
        ext = imgutil.kind(data)
        if ext is None:
            raise RuntimeError("The file is not a PNG or JPG image.")
        if ext in ("webp", "gif"):
            raise RuntimeError(f"Steam does not load {ext.upper()} images. Convert it to PNG or JPG.")
        if len(data) > 40 * 1024 * 1024:
            raise RuntimeError("Image is too large (max. 40 MB).")
        return self._apply_data(self.user(), appid, slot, data, ext, kind, self.mode())

    def apply_local(self, appid: int, slot: str, path: str, kind: str):
        p = imgutil.safe_path(path)
        return self.apply_bytes(appid, slot, p.read_bytes(), kind)

    def remove(self, appid: int, slot: str, kind: str):
        u = self.user()
        mode = self.mode()
        if mode == "live":
            if slot == "icon":
                if kind == "steam":
                    raise RuntimeError("To restore a Steam game's original icon, verify its files.")
                u.remove_art(appid, "icon")
                self.live.set_shortcut_icon(appid, "")
            else:
                self.live.clear_artwork(appid, slot)
            return True
        return u.remove_art(appid, slot)

    def autofill(self, appids: list[int], slots: list[str], overwrite: bool):
        """For each entry: search by name, select the first matching game, and choose the best image for each artwork type."""
        u = self.user()
        api = self.sgdb()
        mode = self.mode()
        lib = {it["appid"]: it for it in self.library()}
        report = []
        for aid in appids:
            it = lib.get(aid)
            if not it:
                continue
            row = {"appid": aid, "name": it["name"], "applied": [], "error": None}
            try:
                game = None
                if it["type"] == "steam":
                    game = api.game_by_steam_appid(aid)
                if not game:
                    res = api.search(it["name"])
                    game = res[0] if res else None
                if not game:
                    row["error"] = "No results on SteamGridDB"
                else:
                    row["game"] = game.get("name")
                    for slot in slots:
                        if slot == "icon" and it["type"] == "steam":
                            continue
                        if not overwrite and u.art_path(aid, slot):
                            continue
                        for a in api.assets(slot, game["id"])[:3]:
                            data, ext = sgdb.download(a["url"])
                            if ext != "webp":
                                self._apply_data(u, aid, slot, data, ext, it["type"], mode)
                                row["applied"].append(slot)
                                break
            except Exception as e:  # continue with the remaining entries
                row["error"] = str(e)
            report.append(row)
        return report

    # ------------------------------------------------ shortcuts
    def add_shortcut(self, name, exe, start_dir="", launch_options=""):
        if self.need_write_mode() == "live":
            return self.live.add_shortcut(name, exe, start_dir, launch_options)
        return self.user().add_shortcut(name, exe, start_dir, launch_options)

    def remove_shortcut(self, appid):
        if self.need_write_mode() == "live":
            self.live.remove_shortcut(appid)
            return True
        return self.user().remove_shortcut(appid)

    # ------------------------------------------------ collections
    def collections(self):
        m = self.mode()
        if m == "live":
            return {"mode": m, "items": self.live.collections()}
        return {"mode": m, "items": Collections(self.user().config).list()}

    def col_op(self, op, b):
        if self.need_write_mode() == "live":
            lv = self.live
            if op == "create":
                return {"id": lv.create_collection(b["name"], b.get("appids", []))}
            if op == "rename":
                lv.rename_collection(b["id"], b["name"])
            elif op == "delete":
                if b["id"] in ("favorite", "hidden"):
                    raise RuntimeError("Favorites/Hidden cannot be deleted.")
                lv.delete_collection(b["id"])
            elif op == "member":
                lv.set_member(b["id"], [int(a) for a in b["appids"]], bool(b["member"]))
            return {"ok": True}
        col = Collections(self.user().config)
        if op == "create":
            cid = col.create(b["name"])
            if b.get("appids"):
                col.set_member(cid, b["appids"], True)
            return {"id": cid}
        if op == "rename":
            col.rename(b["id"], b["name"])
        elif op == "delete":
            col.delete(b["id"])
        elif op == "member":
            col.set_member(b["id"], b["appids"], bool(b["member"]))
        return {"ok": True}


# ---------------------------------------------------------------- HTTP
def make_handler(app: App, stop_event: threading.Event):
    web = HERE / "web"

    class H(BaseHTTPRequestHandler):
        server_version = "FrameGrid/" + VERSION

        def log_message(self, fmt, *a):
            if os.environ.get("FRAMEGRID_DEBUG"):
                super().log_message(fmt, *a)

        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _file(self, p: Path):
            if not p or not p.is_file():
                self.send_error(404)
                return
            b = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _guard(self):
            # Prevent arbitrary websites from communicating with this local server
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                self.send_error(403)
                return False
            origin = self.headers.get("Origin")
            if self.command == "POST" and origin and urllib.parse.urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
                self.send_error(403)
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            p = u.path
            try:
                if p in ("/", "/index.html"):
                    return self._file(web / "index.html")
                if p.startswith("/static/"):
                    f = (web / p[len("/static/"):]).resolve()
                    if web.resolve() not in f.parents:
                        return self.send_error(403)
                    return self._file(f)
                if p == "/api/state":
                    return self._json(app.state())
                if p == "/api/library":
                    return self._json(app.library())
                if p.startswith("/api/art/"):
                    _, _, _, aid, slot = p.split("/")
                    if slot not in steam.SLOTS:
                        return self.send_error(404)
                    return self._file(app.user().art_path(int(aid), slot))
                if p == "/api/sgdb/search":
                    return self._json(app.sgdb().search(q.get("q", "")))
                if p == "/api/sgdb/steam":
                    return self._json(app.sgdb().game_by_steam_appid(int(q["appid"])))
                if p == "/api/sgdb/assets":
                    return self._json(app.sgdb().assets(
                        q["slot"], int(q["game"]), int(q.get("page", 0)),
                        q.get("animated") == "1", q.get("nsfw") == "1", q.get("humor") == "1"))
                if p == "/api/local/list":
                    return self._json(imgutil.list_dir(q.get("dir") or app.cfg.get("images_dir")))
                if p == "/api/local/file":
                    f = imgutil.safe_path(q["path"])
                    if f.suffix.lower() not in imgutil.IMAGE_EXTS:
                        return self.send_error(403)
                    return self._file(f)
                if p == "/api/collections":
                    return self._json(app.collections())
                return self.send_error(404)
            except Exception as e:
                return self._json({"error": str(e)}, 400)

        def do_POST(self):
            if not self._guard():
                return
            p = urllib.parse.urlparse(self.path).path
            try:
                b = self._body()
                with app.lock:
                    if p == "/api/config":
                        for k in ("api_key", "user", "steam_root", "cef_port", "images_dir"):
                            if k in b:
                                app.cfg[k] = b[k] or None
                        save_config({k: v for k, v in app.cfg.items() if v})
                        return self._json(app.state())
                    if p == "/api/apply":
                        return self._json(app.apply(int(b["appid"]), b["slot"], b["url"], b.get("kind", "")))
                    if p == "/api/apply_local":
                        return self._json(app.apply_local(int(b["appid"]), b["slot"], b["path"], b.get("kind", "")))
                    if p == "/api/upload":
                        return self._json(app.apply_bytes(int(b["appid"]), b["slot"],
                                                          base64.b64decode(b["data"]), b.get("kind", "")))
                    if p == "/api/remove":
                        return self._json({"ok": app.remove(int(b["appid"]), b["slot"], b.get("kind", ""))})
                    if p == "/api/autofill":
                        return self._json(app.autofill([int(a) for a in b["appids"]],
                                                       b.get("slots", list(steam.SLOTS)),
                                                       bool(b.get("overwrite"))))
                    if p == "/api/shortcut/add":
                        return self._json({"appid": app.add_shortcut(b["name"], b["exe"], b.get("start_dir", ""),
                                                                     b.get("launch_options", ""))})
                    if p == "/api/shortcut/remove":
                        return self._json({"ok": app.remove_shortcut(int(b["appid"]))})
                    if p.startswith("/api/collections/"):
                        return self._json(app.col_op(p.rsplit("/", 1)[1], b))
                    if p == "/api/cef/enable":
                        return self._json({"files": app.enable_cef()})
                    if p == "/api/steam/start":
                        return self._json({"ok": steam.start_steam()})
                    if p == "/api/quit":
                        self._json({"ok": True})
                        stop_event.set()
                        return
                return self.send_error(404)
            except Exception as e:
                return self._json({"error": str(e)}, 400)

    return H


def free_port(start):
    for port in range(start, start + 50):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free ports available")


def open_ui(url: str, mode: str):
    """mode: auto | xdg | steam | none"""
    if mode == "none":
        return
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    # In game mode (gamescope), xdg-open has no browser to open: use Steam's browser
    in_gamescope = os.environ.get("XDG_CURRENT_DESKTOP", "").lower() == "gamescope" or bool(os.environ.get("GAMESCOPE_WAYLAND_DISPLAY"))
    if mode == "xdg" or (mode == "auto" and has_display and not in_gamescope and shutil.which("xdg-open")):
        try:
            r = subprocess.run(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if r.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if shutil.which("steam"):
        subprocess.Popen(["steam", f"steam://openurl/{url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description="FrameGrid — SteamGridDB artwork and collections for Steam Frame")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open", choices=["auto", "xdg", "steam", "none"], default="auto",
                    help="how to open the interface (steam = Steam's built-in browser)")
    ap.add_argument("--steam-root", help="path to the Steam folder if it is not detected automatically")
    ap.add_argument("--add-to-steam", action="store_true",
                    help="add FrameGrid to the Steam library (live mode if the CEF debugger is active)")
    ap.add_argument("--enable-cef", action="store_true",
                    help="create .cef-enable-remote-debugging; Steam Frame must then be restarted")
    ap.add_argument("--status", action="store_true", help="show the current operating mode and exit")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args()

    app = App(args.steam_root)
    if args.status:
        st = app.state()
        print(f"Steam: {st['steam_root']}  user: {st['user']}  running: {st['steam_running']}")
        print(f"Mode: {st['mode']}  (CEF flag present: {st['cef_flag']})")
        if st["mode"] == "live":
            print("Steam context:", app.live.check())
        return
    if args.enable_cef:
        for f in app.enable_cef():
            print("Created", f)
        print("Restart Steam Frame (or Steam) to activate the debugger.")
        return
    if args.add_to_steam:
        me = str(HERE / "framegrid.py")
        try:
            existing = [s for s in app.library() if s["type"] == "shortcut" and s["name"] == "FrameGrid"]
            if existing:
                print("FrameGrid is already in the Steam library.")
                return
            aid = app.add_shortcut("FrameGrid", me, str(HERE), "--open steam")
            print(f"FrameGrid added to Steam (appid {aid}).")
        except RuntimeError as e:
            print(e)
            sys.exit(1)
        return
    port = free_port(args.port)
    stop = threading.Event()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, stop))
    url = f"http://127.0.0.1:{port}/"
    print(f"FrameGrid {VERSION} at {url}  (Ctrl+C to exit)")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    open_ui(url, args.open)
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    srv.shutdown()


if __name__ == "__main__":
    main()
