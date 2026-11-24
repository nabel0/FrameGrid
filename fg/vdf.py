# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""Valve VDF parsers/writers (binary for shortcuts.vdf and text for .acf/.vdf)."""
import struct


class U64(int):
    """uint64 integer in binary VDF (type 0x07)."""


class F32(float):
    """float in binary VDF (type 0x03)."""


# ---------------------------------------------------------------- binario
def loads_binary(data: bytes) -> dict:
    pos = 0

    def read_str():
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return s

    def read_map():
        nonlocal pos
        out = {}
        while pos < len(data):
            t = data[pos]
            pos += 1
            if t == 0x08:
                return out
            key = read_str()
            if t == 0x00:
                out[key] = read_map()
            elif t == 0x01:
                out[key] = read_str()
            elif t == 0x02:
                out[key] = struct.unpack_from("<i", data, pos)[0]
                pos += 4
            elif t == 0x03:
                out[key] = F32(struct.unpack_from("<f", data, pos)[0])
                pos += 4
            elif t == 0x07:
                out[key] = U64(struct.unpack_from("<Q", data, pos)[0])
                pos += 8
            else:
                raise ValueError(f"Unknown VDF type 0x{t:02x} at {pos}")
        return out

    return read_map()


def dumps_binary(obj: dict) -> bytes:
    buf = bytearray()

    def w_str(s):
        buf.extend(str(s).encode("utf-8"))
        buf.append(0)

    def w_map(d):
        for k, v in d.items():
            if isinstance(v, dict):
                buf.append(0x00); w_str(k); w_map(v)
            elif isinstance(v, str):
                buf.append(0x01); w_str(k); w_str(v)
            elif isinstance(v, U64):
                buf.append(0x07); w_str(k); buf.extend(struct.pack("<Q", int(v)))
            elif isinstance(v, F32):
                buf.append(0x03); w_str(k); buf.extend(struct.pack("<f", float(v)))
            elif isinstance(v, bool) or isinstance(v, int):
                iv = int(v)
                if iv > 0x7FFFFFFF:  # store as signed int32
                    iv -= 0x100000000
                buf.append(0x02); w_str(k); buf.extend(struct.pack("<i", iv))
            else:
                raise TypeError(f"No serializable en VDF: {k}={v!r}")
        buf.append(0x08)

    w_map(obj)
    return bytes(buf)


# ---------------------------------------------------------------- texto
def loads_text(text: str) -> dict:
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "/" and text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{}":
            tokens.append(c); i += 1
        elif c == '"':
            i += 1
            s = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    s.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, nxt))
                    i += 2
                else:
                    s.append(text[i]); i += 1
            i += 1
            tokens.append(("s", "".join(s)))
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}"':
                j += 1
            tokens.append(("s", text[i:j])); i = j

    pos = 0

    def parse_map():
        nonlocal pos
        out = {}
        while pos < len(tokens):
            tk = tokens[pos]
            if tk == "}":
                pos += 1
                return out
            key = tk[1]; pos += 1
            nxt = tokens[pos] if pos < len(tokens) else None
            if nxt == "{":
                pos += 1
                out[key] = parse_map()
            else:
                out[key] = nxt[1] if nxt else ""
                pos += 1
        return out

    return parse_map()
