# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""Access to the local Steam installation: users, shortcuts, games, and the grid folder."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import zlib
from pathlib import Path

from . import vdf

STEAMID64_BASE = 76561197960265728

# Filename in config/grid for each artwork type ({id} = unsigned app ID)
SLOTS = {
    "capsule": "{id}p",      # portrait poster 600x900
    "wide": "{id}",          # landscape capsule 920x430
    "hero": "{id}_hero",     # header banner 1920x620
    "logo": "{id}_logo",     # logo transparente
    "icon": "{id}_icon",     # icon (shortcuts are also referenced in shortcuts.vdf)
}
IMG_EXTS = ("png", "jpg", "jpeg", "webp", "gif", "ico")


def find_steam_root(override: str | None = None) -> Path | None:
    cands = [override] if override else []
    home = Path.home()
    cands += [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]
    for c in cands:
        if not c:
            continue
        p = Path(c).expanduser()
        if (p / "userdata").is_dir():
            return p.resolve()
    return None


def list_users(root: Path) -> list[dict]:
    names = {}
    most_recent = None
    lu = root / "config/loginusers.vdf"
    if lu.is_file():
        try:
            data = vdf.loads_text(lu.read_text("utf-8", "replace")).get("users", {})
            for sid, info in data.items():
                try:
                    acc = str(int(sid) - STEAMID64_BASE)
                except ValueError:
                    continue
                names[acc] = info.get("PersonaName") or info.get("AccountName") or acc
                if str(info.get("MostRecent", "0")) == "1":
                    most_recent = acc
        except Exception:
            pass
    users = []
    for d in sorted((root / "userdata").iterdir()):
        if d.is_dir() and d.name.isdigit() and d.name != "0":
            users.append({
                "id": d.name,
                "name": names.get(d.name, d.name),
                "recent": d.name == most_recent,
                "mtime": d.stat().st_mtime,
            })
    users.sort(key=lambda u: (not u["recent"], -u["mtime"]))
    return users


def steam_running() -> bool:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            continue
        if comm in ("steam", "steamwebhelper"):
            return True
    return False


def shutdown_steam(timeout=30) -> bool:
    try:
        subprocess.Popen(["steam", "-shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return False
    end = time.time() + timeout
    while time.time() < end:
        if not steam_running():
            return True
        time.sleep(1)
    return not steam_running()


def start_steam():
    try:
        subprocess.Popen(["steam"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except FileNotFoundError:
        return False


def shortcut_appid(exe: str, name: str) -> int:
    """Classic shortcut AppID: crc32(exe+name) | 0x80000000."""
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


class SteamUser:
    def __init__(self, root: Path, uid: str):
        self.root = Path(root)
        self.uid = uid
        self.config = self.root / "userdata" / uid / "config"
        self.grid = self.config / "grid"
        self.shortcuts_path = self.config / "shortcuts.vdf"

    # ------------------------------------------------ shortcuts
    def load_shortcuts(self) -> dict:
        if not self.shortcuts_path.is_file():
            return {"shortcuts": {}}
        return vdf.loads_binary(self.shortcuts_path.read_bytes())

    def save_shortcuts(self, data: dict):
        if steam_running():
            raise RuntimeError("Close Steam before modifying shortcuts.vdf (Steam overwrites it on exit).")
        self.config.mkdir(parents=True, exist_ok=True)
        if self.shortcuts_path.is_file():
            bak = self.config / f"shortcuts.vdf.framegrid-{time.strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(self.shortcuts_path, bak)
            self._prune_backups("shortcuts.vdf.framegrid-*.bak", keep=10)
        tmp = self.shortcuts_path.with_suffix(".vdf.tmp")
        tmp.write_bytes(vdf.dumps_binary(data))
        os.replace(tmp, self.shortcuts_path)

    def _prune_backups(self, pattern, keep):
        baks = sorted(self.config.glob(pattern))
        for b in baks[:-keep]:
            try:
                b.unlink()
            except OSError:
                pass

    @staticmethod
    def _entry_appid(e: dict) -> int:
        if "appid" in e:
            return int(e["appid"]) & 0xFFFFFFFF
        return shortcut_appid(e.get("Exe", e.get("exe", "")), e.get("AppName", e.get("appname", "")))

    def shortcuts(self) -> list[dict]:
        out = []
        for idx, e in self.load_shortcuts().get("shortcuts", {}).items():
            out.append({
                "appid": self._entry_appid(e),
                "name": e.get("AppName", e.get("appname", "")),
                "exe": e.get("Exe", e.get("exe", "")),
                "start_dir": e.get("StartDir", ""),
                "icon_path": e.get("icon", ""),
                "launch_options": e.get("LaunchOptions", ""),
                "hidden": bool(e.get("IsHidden", 0)),
                "type": "shortcut",
                "index": idx,
            })
        return out

    def add_shortcut(self, name, exe, start_dir="", launch_options="", icon=""):
        data = self.load_shortcuts()
        sc = data.setdefault("shortcuts", {})
        q = lambda s: s if (not s or s.startswith('"')) else f'"{s}"'
        exe_q = q(exe)
        if not start_dir:
            start_dir = str(Path(exe.strip('"')).parent)
        appid = shortcut_appid(exe_q, name)
        existing = {self._entry_appid(e) for e in sc.values()}
        while appid in existing:
            appid = ((appid + 1) & 0x7FFFFFFF) | 0x80000000
        idx = str(max([int(k) for k in sc.keys() if k.isdigit()] + [-1]) + 1)
        sc[idx] = {
            "appid": appid,
            "AppName": name,
            "Exe": exe_q,
            "StartDir": q(start_dir),
            "icon": icon,
            "ShortcutPath": "",
            "LaunchOptions": launch_options,
            "IsHidden": 0,
            "AllowDesktopConfig": 1,
            "AllowOverlay": 1,
            "OpenVR": 0,
            "Devkit": 0,
            "DevkitGameID": "",
            "DevkitOverrideAppID": 0,
            "LastPlayTime": 0,
            "FlatpakAppID": "",
            "tags": {},
        }
        self.save_shortcuts(data)
        return appid

    def remove_shortcut(self, appid: int):
        data = self.load_shortcuts()
        sc = data.get("shortcuts", {})
        kept = [e for e in sc.values() if self._entry_appid(e) != appid]
        if len(kept) == len(sc):
            return False
        data["shortcuts"] = {str(i): e for i, e in enumerate(kept)}
        self.save_shortcuts(data)
        return True

    def sync_shortcut_icons(self) -> int:
        """Points each shortcut's icon field to its <appid>_icon.* file in the grid folder."""
        data = self.load_shortcuts()
        changed = 0
        for e in data.get("shortcuts", {}).values():
            p = self.art_path(self._entry_appid(e), "icon")
            if p and e.get("icon") != str(p):
                e["icon"] = str(p)
                changed += 1
        if changed:
            self.save_shortcuts(data)
        return changed

    # ------------------------------------------------ installed Steam games
    def steam_games(self) -> list[dict]:
        libs = [self.root]
        lf = self.root / "steamapps/libraryfolders.vdf"
        if lf.is_file():
            try:
                d = vdf.loads_text(lf.read_text("utf-8", "replace"))
                for v in d.get("libraryfolders", {}).values():
                    if isinstance(v, dict) and v.get("path"):
                        libs.append(Path(v["path"]))
            except Exception:
                pass
        seen, out = set(), []
        for lib in libs:
            sa = Path(lib) / "steamapps"
            if not sa.is_dir():
                continue
            for acf in sa.glob("appmanifest_*.acf"):
                try:
                    st = vdf.loads_text(acf.read_text("utf-8", "replace")).get("AppState", {})
                    aid = int(st.get("appid", 0))
                except Exception:
                    continue
                name = st.get("name", str(aid))
                if not aid or aid in seen or name.startswith("Steamworks") or "Proton" in name \
                        or name.startswith("Steam Linux Runtime"):
                    continue
                seen.add(aid)
                out.append({"appid": aid, "name": name, "type": "steam"})
        out.sort(key=lambda g: g["name"].lower())
        return out

    # ------------------------------------------------ artwork
    def art_path(self, appid: int, slot: str) -> Path | None:
        base = SLOTS[slot].format(id=appid)
        for ext in IMG_EXTS:
            p = self.grid / f"{base}.{ext}"
            if p.is_file():
                return p
        return None

    def art_status(self, appid: int) -> dict:
        return {s: self.art_path(appid, s) is not None for s in SLOTS}

    def write_art(self, appid: int, slot: str, data: bytes, ext: str) -> Path:
        ext = ext.lower().lstrip(".")
        if ext == "jpeg":
            ext = "jpg"
        self.grid.mkdir(parents=True, exist_ok=True)
        base = SLOTS[slot].format(id=appid)
        bakdir = self.grid / ".framegrid-backup"
        for e in IMG_EXTS:
            old = self.grid / f"{base}.{e}"
            if old.is_file():
                bakdir.mkdir(exist_ok=True)
                shutil.move(str(old), bakdir / f"{base}.{int(time.time())}.{e}")
        dest = self.grid / f"{base}.{ext}"
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        return dest

    def poison_steam_icon(self, appid: int, icon_hash: str, data: bytes) -> Path:
        """Steam game icon: replace its cache file (the decky-steamgriddb/SGDBoop method).
        Se escribe a un temporal y se renombra, porque Steam restaura el original si ve que se escribe directamente."""
        d = self.root / "appcache" / "librarycache" / str(appid)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f"{icon_hash}.jpg"
        tmp = d / f"{icon_hash}.jpg_temp"
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        return dest

    def remove_art(self, appid: int, slot: str) -> bool:
        p = self.art_path(appid, slot)
        if not p:
            return False
        bakdir = self.grid / ".framegrid-backup"
        bakdir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), bakdir / f"{p.stem}.{int(time.time())}{p.suffix}")
        return True
