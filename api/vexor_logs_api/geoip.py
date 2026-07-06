"""Minimal, dependency-free MaxMind DB (.mmdb) reader for GeoIP enrichment.

vexor-api's venv ships no ``maxminddb`` package, and the logs plugin follows a
stdlib-only convention (see ``_client.py``). This module implements just enough
of the MaxMind DB binary format to resolve an IP address to a country for the
DB-IP Country Lite database shipped by ``vexor-geoip-update`` at
``/var/lib/vexor-geoip/dbip-country-lite.mmdb``.

Only the decoder types actually used by a country database are implemented
(pointer, utf8 string, map, uint16/32, array, boolean). The reader is
read-only, memoises the parsed metadata, and is safe to call concurrently
(it re-reads the mmap for every lookup but keeps no mutable cursor state).
"""
from __future__ import annotations

import ipaddress
import os
import struct
import threading
from typing import Any, Optional

DEFAULT_DB = os.environ.get(
    "VEXOR_GEOIP_DB", "/var/lib/vexor-geoip/dbip-country-lite.mmdb")

_METADATA_MARKER = b"\xab\xcd\xefMaxMind.com"

_lock = threading.Lock()
_reader: "Optional[_MMDBReader]" = None
_reader_path: Optional[str] = None
_reader_mtime: float = 0.0


class _Decoder:
    """Decodes values from the data section of a MaxMind DB."""

    def __init__(self, buf: bytes, data_start: int):
        self._buf = buf
        self._data_start = data_start

    def decode(self, offset: int) -> tuple[Any, int]:
        ctrl = self._buf[offset]
        offset += 1
        type_num = ctrl >> 5
        if type_num == 0:  # extended type
            type_num = self._buf[offset] + 7
            offset += 1
        size = ctrl & 0x1F
        if type_num == 1:  # pointer
            return self._decode_pointer(size, offset)
        size, offset = self._resolve_size(size, offset)
        if type_num == 2:  # utf8 string
            return (self._buf[offset:offset + size].decode("utf-8", "replace"),
                    offset + size)
        if type_num == 5:  # uint16
            return self._decode_uint(offset, size)
        if type_num == 6:  # uint32
            return self._decode_uint(offset, size)
        if type_num == 7:  # map
            return self._decode_map(size, offset)
        if type_num == 9:  # uint64
            return self._decode_uint(offset, size)
        if type_num == 11:  # array
            return self._decode_array(size, offset)
        if type_num == 14:  # boolean
            return (size != 0, offset)
        if type_num == 4:  # bytes
            return (self._buf[offset:offset + size], offset + size)
        if type_num in (8, 10):  # int32 / uint128 (unused, decode as int)
            return self._decode_uint(offset, size)
        if type_num == 15:  # float
            return (struct.unpack(">f", self._buf[offset:offset + 4])[0], offset + 4)
        if type_num == 3:  # double
            return (struct.unpack(">d", self._buf[offset:offset + 8])[0], offset + 8)
        # Unknown/unsupported type: skip.
        return (None, offset + size)

    def _resolve_size(self, size: int, offset: int) -> tuple[int, int]:
        if size < 29:
            return size, offset
        if size == 29:
            return 29 + self._buf[offset], offset + 1
        if size == 30:
            return 285 + int.from_bytes(self._buf[offset:offset + 2], "big"), offset + 2
        return (65821 + int.from_bytes(self._buf[offset:offset + 3], "big"),
                offset + 3)

    def _decode_uint(self, offset: int, size: int) -> tuple[int, int]:
        return (int.from_bytes(self._buf[offset:offset + size], "big"),
                offset + size)

    def _decode_pointer(self, size: int, offset: int) -> tuple[Any, int]:
        pointer_size = ((size >> 3) & 0x3) + 1
        buf = self._buf
        if pointer_size == 1:
            base = (size & 0x7) << 8
            packed = base | buf[offset]
            new_offset = offset + 1
            target = packed
        elif pointer_size == 2:
            base = (size & 0x7) << 16
            packed = base | int.from_bytes(buf[offset:offset + 2], "big")
            new_offset = offset + 2
            target = packed + 2048
        elif pointer_size == 3:
            base = (size & 0x7) << 24
            packed = base | int.from_bytes(buf[offset:offset + 3], "big")
            new_offset = offset + 3
            target = packed + 526336
        else:
            target = int.from_bytes(buf[offset:offset + 4], "big")
            new_offset = offset + 4
        value, _ = self.decode(self._data_start + target)
        return value, new_offset

    def _decode_map(self, size: int, offset: int) -> tuple[dict, int]:
        result: dict[str, Any] = {}
        for _ in range(size):
            key, offset = self.decode(offset)
            val, offset = self.decode(offset)
            result[key] = val
        return result, offset

    def _decode_array(self, size: int, offset: int) -> tuple[list, int]:
        result: list[Any] = []
        for _ in range(size):
            val, offset = self.decode(offset)
            result.append(val)
        return result, offset


class _MMDBReader:
    def __init__(self, path: str):
        with open(path, "rb") as fh:
            self._buf = fh.read()
        meta_start = self._buf.rfind(_METADATA_MARKER)
        if meta_start < 0:
            raise ValueError("not a MaxMind DB (metadata marker missing)")
        meta_dec = _Decoder(self._buf, 0)
        metadata, _ = meta_dec.decode(meta_start + len(_METADATA_MARKER))
        self.node_count = int(metadata["node_count"])
        self.record_size = int(metadata["record_size"])
        self.ip_version = int(metadata["ip_version"])
        self._node_bytes = self.record_size * 2 // 8
        self._search_tree_size = self.node_count * self._node_bytes
        self._data_start = self._search_tree_size + 16
        self._decoder = _Decoder(self._buf, self._data_start)
        self._ipv4_start = self._compute_ipv4_start()

    def _read_node(self, node: int, index: int) -> int:
        base = node * self._node_bytes
        rs = self.record_size
        buf = self._buf
        if rs == 24:
            off = base + index * 3
            return int.from_bytes(buf[off:off + 3], "big")
        if rs == 28:
            if index == 0:
                b = buf[base:base + 3]
                mid = buf[base + 3]
                return ((mid & 0xF0) << 20) | int.from_bytes(b, "big")
            b = buf[base + 4:base + 7]
            mid = buf[base + 3]
            return ((mid & 0x0F) << 24) | int.from_bytes(b, "big")
        if rs == 32:
            off = base + index * 4
            return int.from_bytes(buf[off:off + 4], "big")
        raise ValueError(f"unsupported record size {rs}")

    def _compute_ipv4_start(self) -> int:
        if self.ip_version == 4:
            return 0
        node = 0
        for _ in range(96):
            if node >= self.node_count:
                break
            node = self._read_node(node, 0)
        return node

    def get(self, ip: str) -> Optional[dict]:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if isinstance(addr, ipaddress.IPv4Address):
            if self.ip_version == 6:
                node = self._ipv4_start
                bits = format(int(addr), "032b")
            else:
                node = 0
                bits = format(int(addr), "032b")
        else:
            if self.ip_version == 4:
                return None
            node = 0
            bits = format(int(addr), "0128b")
        for bit in bits:
            if node >= self.node_count:
                break
            node = self._read_node(node, int(bit))
        if node == self.node_count:
            return None  # empty leaf
        if node <= self.node_count:
            return None
        data_offset = node - self.node_count - 16 + self._data_start
        value, _ = self._decoder.decode(data_offset)
        return value if isinstance(value, dict) else None


def _get_reader(path: Optional[str] = None) -> "Optional[_MMDBReader]":
    global _reader, _reader_path, _reader_mtime
    db_path = path or DEFAULT_DB
    try:
        mtime = os.path.getmtime(db_path)
    except OSError:
        return None
    with _lock:
        if (_reader is None or _reader_path != db_path
                or _reader_mtime != mtime):
            try:
                _reader = _MMDBReader(db_path)
                _reader_path = db_path
                _reader_mtime = mtime
            except Exception:
                _reader = None
        return _reader


def available() -> bool:
    return _get_reader() is not None


def lookup(ip: str) -> Optional[dict]:
    """Return {'country_code','country_name'} for an IP, or None."""
    reader = _get_reader()
    if reader is None:
        return None
    raw = reader.get(ip)
    if not raw:
        return None
    country = raw.get("country") or raw.get("registered_country") or {}
    code = country.get("iso_code")
    names = country.get("names") or {}
    name = names.get("en")
    if not code and not name:
        return None
    return {"country_code": code, "country_name": name}


def lookup_many(ips: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    reader = _get_reader()
    if reader is None:
        return out
    for ip in ips:
        if ip in out:
            continue
        res = lookup(ip)
        if res:
            out[ip] = res
    return out
