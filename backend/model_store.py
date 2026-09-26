"""
Share the trained model through Upstash Redis, so the API server never has
to train it on its own small CPU.

GitHub Actions trains nightly (train_model.py) and publishes; the server
loads the published model at startup in seconds. A server that does train
(no fresh shared model yet) publishes its result too, so its next restart
loads instead of retraining.

The model (a few MB) is stored in chunks under a new generation each time,
and the metadata key is switched last, so a reader never mixes an old and a
new model. Old chunks are deleted after the switch.
"""

import hashlib
import json
import os
import time
from typing import Dict, Optional, Tuple

PREFIX = "betiq:model"
CHUNK = 4 * 1024 * 1024            # Upstash allows 10 MB per request
TTL = 14 * 86400                   # a shared model nobody refreshes expires
MAX_AGE_HOURS = float(os.getenv("SHARED_MODEL_MAX_AGE_HOURS", "36"))
OLD_CHUNKS_TTL = 15 * 60           # a replaced model stays readable this long


def _client(url: Optional[str] = None):
    url = url or os.getenv("UPSTASH_REDIS_URL", "")
    if not url:
        return None
    import redis
    # Binary values: the app's own client decodes responses as text
    return redis.from_url(url, decode_responses=False, socket_timeout=60)


def _prefix(name: str) -> str:
    """Several models can be shared: "" is the main one, others by name
    ("europe": the European competitions model, europe_model.py)."""
    return f"{PREFIX}:{name}" if name else PREFIX


def _meta_key(version: int, name: str = "") -> str:
    return f"{_prefix(name)}:v{version}:meta"


def _chunk_key(version: int, gen: str, i: int, name: str = "") -> str:
    return f"{_prefix(name)}:v{version}:{gen}:{i}"


def publish(blob: bytes, version: int, info: Optional[Dict] = None, client=None, name: str = "") -> Dict:
    """Store the model; returns its metadata."""
    r = client or _client()
    if r is None:
        raise RuntimeError("UPSTASH_REDIS_URL is not set")
    old = describe(version, client=r, name=name)
    gen = f"{time.time():.3f}"
    parts = [blob[i:i + CHUNK] for i in range(0, len(blob), CHUNK)] or [b""]
    for i, part in enumerate(parts):
        r.set(_chunk_key(version, gen, i, name), part, ex=TTL)
    meta = {"gen": gen, "trained_at": time.time(), "size": len(blob), "chunks": len(parts),
            "sha256": hashlib.sha256(blob).hexdigest(), **(info or {})}
    r.set(_meta_key(version, name), json.dumps(meta), ex=TTL)
    if old and old.get("gen") != gen:
        # Expire the old chunks instead of deleting them: a server reading the
        # old generation right now (it read the meta a moment ago) still gets
        # a whole model. Deleting them sent a restarting server into training
        # its own, which ran it out of memory.
        for i in range(int(old.get("chunks", 0))):
            r.expire(_chunk_key(version, old["gen"], i, name), OLD_CHUNKS_TTL)
    return meta


def describe(version: int, client=None, name: str = "") -> Optional[Dict]:
    """Metadata of the published model, or None."""
    r = client or _client()
    if r is None:
        return None
    raw = r.get(_meta_key(version, name))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def fetch(version: int, max_age_hours: float = MAX_AGE_HOURS,
          client=None, name: str = "") -> Optional[Tuple[bytes, Dict]]:
    """(model bytes, metadata) if a model no older than max_age_hours is
    published and arrives intact, else None."""
    r = client or _client()
    meta = describe(version, client=r, name=name) if r is not None else None
    if not meta:
        return None
    age_h = (time.time() - float(meta.get("trained_at", 0))) / 3600
    if age_h > max_age_hours:
        print(f"[ModelStore] Shared model is {age_h:.0f}h old (limit {max_age_hours:.0f}h) — not using it.")
        return None
    parts = [r.get(_chunk_key(version, meta["gen"], i, name)) for i in range(int(meta["chunks"]))]
    if any(p is None for p in parts):
        newer = describe(version, client=r, name=name)   # replaced while reading?
        if newer and newer.get("gen") != meta.get("gen"):
            meta = newer
            parts = [r.get(_chunk_key(version, meta["gen"], i, name)) for i in range(int(meta["chunks"]))]
    if any(p is None for p in parts):
        print("[ModelStore] Shared model is incomplete — not using it.")
        return None
    blob = b"".join(parts)
    if hashlib.sha256(blob).hexdigest() != meta.get("sha256"):
        print("[ModelStore] Shared model failed its checksum — not using it.")
        return None
    return blob, meta
