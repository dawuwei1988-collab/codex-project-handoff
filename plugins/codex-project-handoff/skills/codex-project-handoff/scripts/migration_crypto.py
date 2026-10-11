"""Chunked AES-256-GCM. Windows uses its built-in CNG, with no pip dependency."""
from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import zipfile

CHUNK = 4 * 1024 * 1024
TAG = 16


class CngGcm:
    def __init__(self, key: bytes):
        from ctypes import wintypes
        self.library = ctypes.WinDLL("bcrypt")
        pointer, ulong = ctypes.c_void_p, wintypes.ULONG
        signatures = {
            "BCryptOpenAlgorithmProvider": ([ctypes.POINTER(pointer), wintypes.LPCWSTR, wintypes.LPCWSTR, ulong], ctypes.c_long),
            "BCryptSetProperty": ([pointer, wintypes.LPCWSTR, pointer, ulong, ulong], ctypes.c_long),
            "BCryptGetProperty": ([pointer, wintypes.LPCWSTR, pointer, ulong, ctypes.POINTER(ulong), ulong], ctypes.c_long),
            "BCryptGenerateSymmetricKey": ([pointer, ctypes.POINTER(pointer), pointer, ulong, pointer, ulong, ulong], ctypes.c_long),
            "BCryptEncrypt": ([pointer, pointer, ulong, pointer, pointer, ulong, pointer, ulong, ctypes.POINTER(ulong), ulong], ctypes.c_long),
            "BCryptDecrypt": ([pointer, pointer, ulong, pointer, pointer, ulong, pointer, ulong, ctypes.POINTER(ulong), ulong], ctypes.c_long),
            "BCryptDestroyKey": ([pointer], ctypes.c_long),
            "BCryptCloseAlgorithmProvider": ([pointer, ulong], ctypes.c_long),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = arguments, result
        self.algorithm, self.key = pointer(), pointer()
        self.check(self.library.BCryptOpenAlgorithmProvider(ctypes.byref(self.algorithm), "AES", None, 0))
        chaining = ctypes.create_unicode_buffer("ChainingModeGCM")
        self.check(self.library.BCryptSetProperty(self.algorithm, "ChainingMode", chaining, ctypes.sizeof(chaining), 0))
        size, received = ulong(), ulong()
        self.check(self.library.BCryptGetProperty(self.algorithm, "ObjectLength", ctypes.byref(size), ctypes.sizeof(size), ctypes.byref(received), 0))
        self.object = ctypes.create_string_buffer(size.value)
        secret = ctypes.create_string_buffer(key)
        self.check(self.library.BCryptGenerateSymmetricKey(self.algorithm, ctypes.byref(self.key), self.object, len(self.object), secret, len(key), 0))

    @staticmethod
    def check(status):
        if status != 0:
            raise ValueError("AES-GCM failed: wrong key, damaged data, or unavailable Windows crypto (0x%08x)" % (status & 0xffffffff))

    def transform(self, data: bytes, nonce: bytes, aad: bytes, decrypt: bool = False) -> bytes:
        from ctypes import wintypes
        class Auth(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.ULONG), ("version", wintypes.ULONG), ("nonce", ctypes.c_void_p), ("nonceSize", wintypes.ULONG), ("aad", ctypes.c_void_p), ("aadSize", wintypes.ULONG), ("tag", ctypes.c_void_p), ("tagSize", wintypes.ULONG), ("mac", ctypes.c_void_p), ("macSize", wintypes.ULONG), ("aadCount", wintypes.ULONG), ("dataCount", ctypes.c_ulonglong), ("flags", wintypes.ULONG)]
        ciphertext, tag = (data[:-TAG], data[-TAG:]) if decrypt else (data, b"\0" * TAG)
        source, nonce_buffer, aad_buffer, tag_buffer = [ctypes.create_string_buffer(v) for v in (ciphertext, nonce, aad, tag)]
        auth = Auth()
        auth.cbSize, auth.version = ctypes.sizeof(auth), 1
        auth.nonce, auth.nonceSize = ctypes.cast(nonce_buffer, ctypes.c_void_p), len(nonce)
        auth.aad, auth.aadSize = ctypes.cast(aad_buffer, ctypes.c_void_p), len(aad)
        auth.tag, auth.tagSize = ctypes.cast(tag_buffer, ctypes.c_void_p), TAG
        destination, count = ctypes.create_string_buffer(max(1, len(ciphertext))), wintypes.ULONG()
        function = self.library.BCryptDecrypt if decrypt else self.library.BCryptEncrypt
        self.check(function(self.key, source, len(ciphertext), ctypes.byref(auth), None, 0, destination, len(ciphertext), ctypes.byref(count), 0))
        result = destination.raw[:count.value]
        return result if decrypt else result + tag_buffer.raw[:TAG]

    def close(self):
        if self.key:
            self.library.BCryptDestroyKey(self.key)
            self.key = None
        if self.algorithm:
            self.library.BCryptCloseAlgorithmProvider(self.algorithm, 0)
            self.algorithm = None


class Gcm:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("AES-256 recovery key must be 32 bytes")
        if os.name == "nt":
            self.native = CngGcm(key)
            self.crypto = None
        else:
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            except ImportError as exc:
                raise ValueError("Non-Windows encrypted restore needs cryptography; use a Python runtime containing it.") from exc
            self.native, self.crypto = None, AESGCM(key)

    def encrypt(self, nonce, data, aad):
        return self.native.transform(data, nonce, aad) if self.native else self.crypto.encrypt(nonce, data, aad)

    def decrypt(self, nonce, data, aad):
        if self.native:
            return self.native.transform(data, nonce, aad, True)
        from cryptography.exceptions import InvalidTag
        try:
            return self.crypto.decrypt(nonce, data, aad)
        except InvalidTag as exc:
            raise ValueError("AES-GCM authentication failed: wrong recovery key or damaged package") from exc

    def close(self):
        if self.native:
            self.native.close()


def read_key(path: Path | None) -> bytes:
    if path is None:
        raise ValueError("Encrypted package requires --key-file. Keep its recovery key separate from the package.")
    raw = path.read_bytes()
    if len(raw) == 32:
        return raw
    record = json.loads(raw)
    if record.get("format") != "codex-migration-recovery-key":
        raise ValueError("invalid recovery key file")
    return bytes.fromhex(record["key"])


def create_key(path: Path) -> bytes:
    key = os.urandom(32)
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"format": "codex-migration-recovery-key", "key": key.hex()}, stream)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def wrap_package(source: Path, target: Path, key_file: Path, runtime: dict[str, bytes], summary: dict) -> None:
    key = read_key(key_file) if key_file.exists() else create_key(key_file)
    cipher = Gcm(key)
    prefix = os.urandom(8)
    size = source.stat().st_size
    metadata = {"algorithm": "AES-256-GCM", "chunkSize": CHUNK, "plainSize": size, "noncePrefix": prefix.hex()}
    aad_base = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    try:
        with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
            for name, data in runtime.items():
                archive.writestr(name, data)
            archive.writestr("capsule.json", json.dumps({"format": "codex-project-migration", "schemaVersion": 3, "encrypted": True, "encryption": metadata, "publicSummary": summary}))
            with source.open("rb") as reader, archive.open("encrypted.bin", "w", force_zip64=True) as writer:
                index = 0
                while data := reader.read(CHUNK):
                    counter = struct.pack(">I", index)
                    writer.write(cipher.encrypt(prefix + counter, data, aad_base + counter))
                    index += 1
    finally:
        cipher.close()


class DecryptedReader(io.RawIOBase):
    """Seekable authenticated view; no decrypted temporary archive on disk."""
    def __init__(self, outer: zipfile.ZipFile, metadata: dict, key: bytes):
        self.outer = outer
        self.size = int(metadata["plainSize"])
        self.chunk = int(metadata["chunkSize"])
        self.prefix = bytes.fromhex(metadata["noncePrefix"])
        if self.size < 0 or self.chunk != CHUNK or len(self.prefix) != 8 or metadata.get("algorithm") != "AES-256-GCM":
            raise ValueError("invalid encryption metadata")
        info = outer.getinfo("encrypted.bin")
        count = (self.size + self.chunk - 1) // self.chunk
        if count >= 2 ** 32 or info.compress_type != zipfile.ZIP_STORED or info.file_size != self.size + count * TAG:
            raise ValueError("invalid encrypted payload size")
        # Use the stored member's local header offset for O(1) random access.
        self.file = Path(outer.filename).open("rb")
        self.file.seek(info.header_offset)
        local = self.file.read(30)
        if local[:4] != b"PK\x03\x04":
            raise ValueError("invalid encrypted local header")
        name_size, extra_size = struct.unpack("<HH", local[26:30])
        self.offset = info.header_offset + 30 + name_size + extra_size
        self.cipher = Gcm(key)
        self.aad = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        self.position, self.cached_index, self.cached = 0, -1, b""

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        value = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if value < 0:
            raise ValueError("negative seek")
        self.position = value
        return value

    def read(self, size=-1):
        remaining = max(0, self.size - self.position)
        size = remaining if size < 0 else min(size, remaining)
        result = bytearray()
        while size:
            index, within = divmod(self.position, self.chunk)
            if index != self.cached_index:
                count = min(self.chunk, self.size - index * self.chunk)
                self.file.seek(self.offset + index * (self.chunk + TAG))
                ciphertext = self.file.read(count + TAG)
                counter = struct.pack(">I", index)
                self.cached = self.cipher.decrypt(self.prefix + counter, ciphertext, self.aad + counter)
                self.cached_index = index
            amount = min(size, len(self.cached) - within)
            if amount <= 0:
                raise ValueError("truncated encrypted chunk")
            result.extend(self.cached[within:within + amount])
            size -= amount
            self.position += amount
        return bytes(result)

    def close(self):
        if not self.closed:
            self.cipher.close()
            self.file.close()
            self.outer.close()
        super().close()
