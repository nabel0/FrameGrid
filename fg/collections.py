# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""Steam library collections (userdata/<id>/config/cloudstorage).

Steam stores each collection as a "user-collections.<id>" entry in
cloud-storage-namespace-1.json. The value is JSON encoded as a string:
  {"id": "uc-xxxx", "name": "Ports", "added": [appid, ...], "removed": []}
Dynamic collections also contain "filterSpec" (they are not edited here).
Non-Steam shortcuts use machine-local app IDs, so they must be edited on
the device itself (Steam Frame), not from another computer.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import string
import time
from pathlib import Path

from .steam import steam_running

PREFIX = "user-collections."
BUILTIN = {"favorite": "Favorites", "hidden": "Hidden"}


class Collections:
    def __init__(self, config_dir: Path):
        self.dir = Path(config_dir) / "cloudstorage"
        self.path = self.dir / "cloud-storage-namespace-1.json"
        self.ns_path = self.dir / "cloud-storage-namespaces.json"
        self.mod_path = self.dir / "cloud-storage-namespace-1.modified.json"
        self._entries: list | None = None

    # ------------------------------------------------ IO
    def load(self) -> list:
        if self.path.is_file():
            self._entries = json.loads(self.path.read_text("utf-8") or "[]")
        else:
            self._entries = []
        return self._entries

    def _max_version(self) -> int:
        mv = 0
        for _, obj in self._entries:
            try:
                mv = max(mv, int(obj.get("version", 0)))
            except (TypeError, ValueError):
                pass
        if self.ns_path.is_file():
            try:
                for ns, ver in json.loads(self.ns_path.read_text("utf-8")):
                    if int(ns) == 1:
                        mv = max(mv, int(ver))
            except Exception:
                pass
        return mv

    def _save(self, changed_keys: list[str]):
        if steam_running():
            raise RuntimeError("Close Steam before editing collections: it keeps them in memory and overwrites them.")
        self.dir.mkdir(parents=True, exist_ok=True)
        # back up the entire folder
        if any(self.dir.iterdir()):
            root = self.dir.parent / "cloudstorage-framegrid-backups"
            # permanent "original" backup (the first time FrameGrid modifies collections)
            targets = []
            if not (root / "original").exists():
                targets.append(root / "original")
            targets.append(root / (time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 10**6:06d}"))
            for bak in targets:
                bak.mkdir(parents=True, exist_ok=True)
                for f in self.dir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, bak / f.name)
            olds = sorted(p for p in root.iterdir() if p.name != "original")
            for o in olds[:-20]:
                shutil.rmtree(o, ignore_errors=True)

        new_ver = str(self._max_version() + 1)
        now = int(time.time())
        for key, obj in self._entries:
            if key in changed_keys:
                obj["version"] = new_ver
                obj["timestamp"] = now

        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._entries, ensure_ascii=False, separators=(",", ":")), "utf-8")
        os.replace(tmp, self.path)

        # namespace version
        try:
            ns = json.loads(self.ns_path.read_text("utf-8")) if self.ns_path.is_file() else []
        except Exception:
            ns = []
        ns = [x for x in ns if int(x[0]) != 1] + [[1, new_ver]]
        self.ns_path.write_text(json.dumps(ns), "utf-8")

        # mark keys as pending upload to the cloud (if Steam uses this file)
        if self.mod_path.is_file():
            try:
                mod = json.loads(self.mod_path.read_text("utf-8") or "[]")
                if isinstance(mod, list):
                    for k in changed_keys:
                        if k not in mod:
                            mod.append(k)
                    self.mod_path.write_text(json.dumps(mod), "utf-8")
            except Exception:
                pass

    # ------------------------------------------------ consulta
    def _find(self, key):
        for pair in self._entries:
            if pair[0] == key:
                return pair[1]
        return None

    def list(self) -> list[dict]:
        self.load()
        out = []
        for key, obj in self._entries:
            if not key.startswith(PREFIX) or obj.get("is_deleted") or "value" not in obj:
                continue
            try:
                val = json.loads(obj["value"])
            except Exception:
                continue
            cid = key[len(PREFIX):]
            out.append({
                "id": cid,
                "name": BUILTIN.get(cid, val.get("name", cid)),
                "builtin": cid in BUILTIN,
                "dynamic": "filterSpec" in val,
                "added": [int(a) & 0xFFFFFFFF for a in val.get("added", [])],
            })
        for cid, name in BUILTIN.items():
            if not any(c["id"] == cid for c in out):
                out.append({"id": cid, "name": name, "builtin": True, "dynamic": False, "added": []})
        out.sort(key=lambda c: (not c["builtin"], c["name"].lower()))
        return out

    # ------------------------------------------------ editing
    def _get_value(self, cid):
        key = PREFIX + cid
        obj = self._find(key)
        if obj is None or obj.get("is_deleted") or "value" not in obj:
            if cid in BUILTIN:
                obj = {"key": key, "timestamp": 0, "value": json.dumps({"id": cid, "added": [], "removed": []}),
                       "version": "0", "conflictResolutionMethod": "custom", "strMethodId": "union-collections"}
                self._entries.append([key, obj])
            else:
                raise KeyError(f"Collection not found: {cid}")
        return key, obj, json.loads(obj["value"])

    def create(self, name: str) -> str:
        self.load()
        cid = "uc-" + "".join(random.choices(string.ascii_letters + string.digits, k=12))
        key = PREFIX + cid
        obj = {
            "key": key,
            "timestamp": int(time.time()),
            "value": json.dumps({"id": cid, "name": name, "added": [], "removed": []}, ensure_ascii=False),
            "version": "0",
            "conflictResolutionMethod": "custom",
            "strMethodId": "union-collections",
        }
        self._entries.append([key, obj])
        self._save([key])
        return cid

    def rename(self, cid: str, name: str):
        self.load()
        key, obj, val = self._get_value(cid)
        val["name"] = name
        obj["value"] = json.dumps(val, ensure_ascii=False)
        self._save([key])

    def delete(self, cid: str):
        if cid in BUILTIN:
            raise ValueError("Favorites/Hidden cannot be deleted.")
        self.load()
        key = PREFIX + cid
        obj = self._find(key)
        if obj is None:
            return
        obj.pop("value", None)
        obj["is_deleted"] = True
        self._save([key])

    def set_member(self, cid: str, appids: list[int], member: bool):
        self.load()
        key, obj, val = self._get_value(cid)
        if "filterSpec" in val:
            raise ValueError("Dynamic collections must be edited in Steam.")
        added = [int(a) & 0xFFFFFFFF for a in val.get("added", [])]
        removed = [int(a) & 0xFFFFFFFF for a in val.get("removed", [])]
        for a in appids:
            a = int(a) & 0xFFFFFFFF
            if member:
                if a not in added:
                    added.append(a)
                if a in removed:
                    removed.remove(a)
            else:
                # "removed" prevents the cloud's union merge from adding the item again
                if a in added:
                    added.remove(a)
                if a not in removed:
                    removed.append(a)
        val["added"], val["removed"] = added, removed
        obj["value"] = json.dumps(val, ensure_ascii=False)
        self._save([key])
