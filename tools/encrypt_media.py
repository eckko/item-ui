#!/usr/bin/env python3
"""The file format of encrypted media on a subject site (not a CLI).

A book's own pictures and clips are plain files (media/<id>.<ext>). When a
book is published, build_book.py encrypts each one with the SUBJECT key as
quiz/media/<id>.enc, and build_pool.py stores it in the pool.

The file format is the 12-byte nonce followed by AES-GCM data. The file key
comes from the key with HKDF-SHA-256 (info "file:<ID>"); the ID (such as
u03/i07/f02) is the authenticated data. The nonce is a keyed hash of the
content, so the same file always gives the same bytes.
"""

import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_BYTES = 12
LIMITS = {"one file": 50_000_000, "book": 100_000_000}


def derive(key, info, length):
    """HKDF-SHA-256 of the key for one purpose."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None,
                info=info.encode()).derive(key)


def decrypt(key, file_id, data):
    """The original bytes of one stored file; raises if the key or id is
    wrong."""
    file_key = derive(key, "file:" + file_id, 32)
    return AESGCM(file_key).decrypt(
        data[:NONCE_BYTES], data[NONCE_BYTES:], file_id.encode())


def encrypt(key, file_id, content):
    """The bytes to store for one file."""
    file_key = derive(key, "file:" + file_id, 32)
    nonce_key = derive(key, "nonce:" + file_id, 32)
    nonce = hmac.new(nonce_key, content, hashlib.sha256).digest()[
        :NONCE_BYTES]
    sealed = AESGCM(file_key).encrypt(nonce, content, file_id.encode())
    return nonce + sealed
