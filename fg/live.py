# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""Operations on the running Steam client (via CDP), as Decky does.

Funciona con Steam abierto: no hay que cerrarlo ni reiniciarlo para ver los cambios.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from .cdp import SteamCDP

# Tipos de ELibraryAssetType de Steam
ASSET_TYPE = {"capsule": 0, "hero": 1, "logo": 2, "wide": 3, "icon": 4}
SHORTCUT = 1073741824


def js(v) -> str:
    return json.dumps(v, ensure_ascii=False)


class SteamLive:
    def __init__(self, port=8080):
        self.cdp = SteamCDP(port)

    def available(self) -> bool:
        return self.cdp.available()

    def check(self) -> dict:
        return self.cdp.run("return {client: typeof SteamClient, apps: typeof appStore,"
                            " cols: typeof collectionStore, user: (window.App && App.m_CurrentUser"
                            " && App.m_CurrentUser.strAccountName) || null};")

    # ------------------------------------------------ biblioteca
    def shortcuts(self) -> list[dict]:
        return self.cdp.run(f"""
            return appStore.allApps.filter(a => a.app_type === {SHORTCUT})
              .map(a => ({{appid: a.appid >>> 0, name: a.display_name, type: 'shortcut'}}));""")

    def icon_hash(self, appid: int) -> str | None:
        return self.cdp.run(f"const o = appStore.GetAppOverviewByAppID({int(appid)}); return o ? (o.icon_hash || null) : null;")

    # ------------------------------------------------ artwork
    def set_artwork(self, appid: int, slot: str, data: bytes, ext: str, set_logo_pos: bool):
        b64 = base64.b64encode(data).decode()
        kind = "jpg" if ext in ("jpg", "jpeg") else "png"
        t = ASSET_TYPE[slot]
        extra = ""
        if slot == "logo" and set_logo_pos:
            # without a saved position, shortcut logos appear blank (decky-steamgriddb workaround)
            extra = f"""
              const o = appStore.GetAppOverviewByAppID({int(appid)});
              if (o) await appDetailsStore.SaveCustomLogoPosition(o, {{pinnedPosition: 'BottomLeft', nWidthPct: 50, nHeightPct: 50}});"""
        self.cdp.run(f"""
            await SteamClient.Apps.ClearCustomArtworkForApp({int(appid)}, {t});
            await new Promise(r => setTimeout(r, 500));
            await SteamClient.Apps.SetCustomArtworkForApp({int(appid)}, {js(b64)}, {js(kind)}, {t});{extra}
            return true;""", timeout=60)

    def clear_artwork(self, appid: int, slot: str):
        self.cdp.run(f"await SteamClient.Apps.ClearCustomArtworkForApp({int(appid)}, {ASSET_TYPE[slot]});"
                     " await new Promise(r => setTimeout(r, 500)); return true;")

    def set_shortcut_icon(self, appid: int, path: str):
        # SetShortcutName consigo mismo fuerza a Steam a releer el icono
        self.cdp.run(f"""
            SteamClient.Apps.SetShortcutIcon({int(appid)}, {js(path)});
            const o = appStore.GetAppOverviewByAppID({int(appid)});
            if (o) SteamClient.Apps.SetShortcutName({int(appid)}, o.display_name);
            return true;""")

    # ------------------------------------------------ shortcuts
    def add_shortcut(self, name, exe, start_dir="", launch_options="") -> int:
        start_dir = start_dir or str(Path(exe).parent)
        return int(self.cdp.run(f"""
            const id = (await SteamClient.Apps.AddShortcut({js(name)}, {js(exe)}, {js(start_dir)}, {js(launch_options)})) >>> 0;
            SteamClient.Apps.SetShortcutName(id, {js(name)});
            SteamClient.Apps.SetShortcutStartDir(id, {js(json.dumps(start_dir))});
            if ({js(launch_options)}) SteamClient.Apps.SetAppLaunchOptions(id, {js(launch_options)});
            return id;""")) & 0xFFFFFFFF

    def remove_shortcut(self, appid: int):
        self.cdp.run(f"SteamClient.Apps.RemoveShortcut({int(appid)}); return true;")

    # ------------------------------------------------ collections
    def collections(self) -> list[dict]:
        return self.cdp.run("""
            const ids = c => {
              const a = c.apps; let it;
              if (a instanceof Map) it = a.keys(); else if (a && a[Symbol.iterator]) it = a; else it = [];
              return Array.from(it, x => (typeof x === 'number' ? x : x.appid) >>> 0);
            };
            const out = [];
            const seen = new Set();
            const push = (c, builtin, name) => {
              if (!c || seen.has(c.id)) return; seen.add(c.id);
              out.push({id: c.id, name: name || c.displayName, builtin, dynamic: !!c.bIsDynamic,
                        added: ids(c)});
            };
            push(collectionStore.GetCollection('favorite'), true, 'Favorites');
            push(collectionStore.GetCollection('hidden'), true, 'Hidden');
            for (const c of collectionStore.userCollections) push(c, false);
            return out;""")

    def create_collection(self, name: str, appids: list[int]) -> str:
        return self.cdp.run(f"""
            const ovs = {js([int(a) for a in appids])}.map(i => appStore.GetAppOverviewByAppID(i)).filter(Boolean);
            const c = collectionStore.NewUnsavedCollection({js(name)}, undefined, ovs);
            await c.Save();
            return c.id;""")

    def rename_collection(self, cid: str, name: str):
        self.cdp.run(f"""
            const c = collectionStore.GetCollection({js(cid)});
            if (!c) throw new Error('Collection not found');
            c.displayName = {js(name)};
            await c.Save(); return true;""")

    def delete_collection(self, cid: str):
        self.cdp.run(f"await collectionStore.DeleteCollection({js(cid)}); return true;")

    def set_member(self, cid: str, appids: list[int], member: bool):
        ids = js([int(a) for a in appids])
        b = "true" if member else "false"
        if cid == "favorite":
            body = f"collectionStore.SetAppsAsFavorite({ids}, {b});"
        elif cid == "hidden":
            body = f"collectionStore.SetAppsAsHidden({ids}, {b});"
        else:
            body = f"""
              const c = collectionStore.GetCollection({js(cid)});
              if (!c) throw new Error('Collection not found');
              if (c.bIsDynamic) throw new Error('Dynamic collections must be edited in Steam.');
              collectionStore.AddOrRemoveApp({ids}, {b}, {js(cid)});"""
        self.cdp.run(body + " return true;")
