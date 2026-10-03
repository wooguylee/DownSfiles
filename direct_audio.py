"""HTTP media resolution and streaming helpers for workspace downloads."""

import base64
import hashlib
import io
import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def request(url):
    return urlopen(Request(url, headers={"User-Agent": "DownSfiles/1.0"}), timeout=30)


def resolve_media(song_url):
    parsed = urlparse(song_url)
    if parsed.scheme != "https" or parsed.hostname != "suno.com" or not re.fullmatch(
        r"/song/[0-9a-f-]{36}", parsed.path
    ):
        raise ValueError("Provide an https://suno.com/song/<id> URL")
    with request(song_url) as response:
        page = response.read().decode("utf-8")
    # Server-rendered React payload escapes its embedded JSON quotes.
    decoded = page.replace('\\"', '"')
    content_id = parsed.path.rsplit("/", 1)[-1]
    for match in re.finditer(r'"media_urls"\s*:\s*', decoded):
        try:
            entries, _ = json.JSONDecoder().raw_decode(decoded[match.end():])
        except json.JSONDecodeError:
            continue
        for entry in entries:
            url = entry.get("url", "")
            if (entry.get("delivery") == "progressive" and url.startswith("https://")
                    and content_id in url):
                return entry
    raise RuntimeError("This page does not advertise a progressive media URL")


def playback_decryptor(song_url, media):
    if not media.get("encoding"):
        return None
    content_id = urlparse(song_url).path.rsplit("/", 1)[-1]
    body = json.dumps({"content_params": {"content_id": content_id, "content_type": "clip"}}).encode()
    req = Request("https://studio-api-prod.suno.com/api/mango/rights", data=body,
                  headers={"Content-Type": "application/json", "User-Agent": "DownSfiles/1.0"})
    with urlopen(req, timeout=30) as response:
        rights = json.load(response)
    # Reproduce the published player's anonymous playback protocol.
    # The server must grant rights; tokens/keys stay in memory and are never logged.
    wrapper = AESGCM(hashlib.sha256(rights["glt"].encode()).digest())
    def unwrap(value):
        wrapped = base64.b64decode(value, validate=True)
        return wrapper.decrypt(wrapped[:12], wrapped[12:], content_id.encode())
    return Cipher(algorithms.AES(unwrap(rights["key"])), modes.CTR(unwrap(rights["iv"]))).decryptor()


class RecordingReader(io.RawIOBase):
    """Feed the decoder directly from HTTP, retaining every received byte."""

    def __init__(self, response, target, decryptor):
        super().__init__()
        self.response = response
        self.target = target
        self.decryptor = decryptor
        self.count = 0
        self.sha256 = hashlib.sha256()
        self.wire_sha256 = hashlib.sha256()
        self.md5 = hashlib.md5()

    def readable(self):
        return True

    def seekable(self):
        return False

    def tell(self):
        return self.count

    def read(self, size=-1):
        data = self.response.read(size)
        if data:
            self.wire_sha256.update(data)
            self.md5.update(data)
            self.count += len(data)
            if self.decryptor is not None:
                data = self.decryptor.update(data)
            self.target.write(data)
            self.sha256.update(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)
