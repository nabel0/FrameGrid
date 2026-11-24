# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Chrome DevTools Protocol (CDP) client for the Steam client.

Steam exposes its interface (CEF) at http://127.0.0.1:8080 when remote debugging
is enabled (the .cef-enable-remote-debugging file or Settings > Developer).
The "SharedJSContext" tab contains SteamClient, appStore, collectionStore, and other
objects used by Decky plugins. Only WebSocket + Runtime.evaluate are needed here,
implemented with the standard library.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.parse
import urllib.request

SHARED_CTX_NAMES = ["SharedJSContext", "Steam Shared Context presented by Valve™", "Steam", "SP"]


class CDPError(Exception):
    pass


class _WebSocket:
    def __init__(self, url: str, timeout=15):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("Steam's debugger closed the connection")
            resp += chunk
        head, self._buf = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise CDPError("Steam rejected the WebSocket connection: " + head.split(b"\r\n")[0].decode(errors="replace"))

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise CDPError("Connection to Steam lost")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text: str):
        payload = text.encode("utf-8")
        hdr = bytearray([0x81])
        n = len(payload)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        # fast masking using large-integer arithmetic
        m = (mask * (n // 4 + 1))[:n]
        masked = (int.from_bytes(payload, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big") if n else b""
        self.sock.sendall(bytes(hdr) + masked)

    def recv_text(self) -> str:
        parts = []
        while True:
            b0, b1 = self._recv_exact(2)
            op, fin = b0 & 0x0F, b0 & 0x80
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if op == 0x8:
                raise CDPError("Steam closed the connection")
            if op == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80 | len(data)]) + b"\0\0\0\0" + data)
                continue
            if op in (0x1, 0x0, 0x2):
                parts.append(data)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + b"\0\0\0\0")
        except OSError:
            pass
        self.sock.close()


class SteamCDP:
    def __init__(self, port: int = 8080, host: str = "127.0.0.1"):
        self.base = f"http://{host}:{port}"

    def targets(self, timeout=2) -> list[dict]:
        try:
            with urllib.request.urlopen(self.base + "/json", timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            raise CDPError(f"Steam debugger unavailable at {self.base} ({e})")

    def shared_context(self) -> dict:
        tabs = self.targets()
        for name in SHARED_CTX_NAMES:
            for t in tabs:
                if t.get("title") == name and t.get("webSocketDebuggerUrl") and "steamloopback.host" in t.get("url", ""):
                    return t
        for name in SHARED_CTX_NAMES:
            for t in tabs:
                if t.get("title") == name and t.get("webSocketDebuggerUrl"):
                    return t
        raise CDPError("Steam responde pero no encuentro su SharedJSContext")

    def available(self) -> bool:
        try:
            self.shared_context()
            return True
        except CDPError:
            return False

    def run(self, body: str, timeout=30):
        """Runs JS inside an async function in Steam's context and returns its `return` value."""
        expr = "(async()=>{ const __r = await (async()=>{ " + body + " })(); return JSON.stringify(__r === undefined ? null : __r); })()"
        tab = self.shared_context()
        ws = _WebSocket(tab["webSocketDebuggerUrl"], timeout=timeout)
        try:
            ws.send_text(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                     "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv_text())
                if msg.get("id") == 1:
                    break
        finally:
            ws.close()
        if "error" in msg:
            raise CDPError(msg["error"].get("message", str(msg["error"])))
        res = msg.get("result", {})
        if res.get("exceptionDetails"):
            ed = res["exceptionDetails"]
            desc = (ed.get("exception") or {}).get("description") or ed.get("text") or "JavaScript error"
            raise CDPError("Error inside Steam: " + desc.splitlines()[0])
        val = res.get("result", {}).get("value")
        return json.loads(val) if isinstance(val, str) else val
