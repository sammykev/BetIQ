"""
Copy every key from one Redis to another (Upstash to this server's Redis):
strings, hashes, lists, sets and sorted sets, each with its time to live.
A key that already exists on the destination is replaced.

    python redis_copy.py              from $UPSTASH_REDIS_URL to $LOCAL_REDIS_URL
    python redis_copy.py --dry-run    count what would be copied, write nothing
    python redis_copy.py --check      compare the two key by key (counts and types)

On the server (deploy/vm/CONTABO.md, "Redis on this server"):
    sudo docker compose exec api python redis_copy.py
"""

import argparse
import os
import sys
import time
from collections import Counter
from typing import Dict, Tuple

import redis

BATCH = 200


def connect(url: str, name: str) -> "redis.Redis":
    if not url:
        raise SystemExit(f"{name} isn't set")
    r = redis.from_url(url, decode_responses=False, socket_timeout=120)
    r.ping()
    return r


def _read(src, keys):
    """Each key's (type, ttl in ms or -1, value)."""
    p = src.pipeline(transaction=False)
    for k in keys:
        p.type(k)
        p.pttl(k)
    meta = p.execute()
    types = [t.decode() if isinstance(t, bytes) else t for t in meta[0::2]]
    ttls = meta[1::2]
    p = src.pipeline(transaction=False)
    for k, t in zip(keys, types):
        if t == "string":
            p.get(k)
        elif t == "hash":
            p.hgetall(k)
        elif t == "list":
            p.lrange(k, 0, -1)
        elif t == "set":
            p.smembers(k)
        elif t == "zset":
            p.zrange(k, 0, -1, withscores=True)
        else:
            p.exists(k)   # placeholder: types not copied (streams, none)
    return list(zip(keys, types, ttls, p.execute()))


def _write(dst, rows) -> Tuple[Counter, int, Counter]:
    copied, skipped, size = Counter(), Counter(), 0
    p = dst.pipeline(transaction=False)
    for k, t, ttl, v in rows:
        if t not in ("string", "hash", "list", "set", "zset") or v is None or v == {} or v == [] or v == set():
            skipped[t] += 1
            continue
        p.delete(k)
        if t == "string":
            p.set(k, v)
            size += len(v)
        elif t == "hash":
            p.hset(k, mapping=v)
            size += sum(len(a) + len(b) for a, b in v.items())
        elif t == "list":
            p.rpush(k, *v)
            size += sum(len(x) for x in v)
        elif t == "set":
            p.sadd(k, *v)
            size += sum(len(x) for x in v)
        else:
            p.zadd(k, {m: s for m, s in v})
            size += sum(len(m) for m, _ in v)
        if isinstance(ttl, int) and ttl > 0:
            p.pexpire(k, ttl)
        copied[t] += 1
    p.execute()
    return copied, size, skipped


def copy(src, dst, dry_run: bool = False) -> Dict:
    copied, skipped, size, seen = Counter(), Counter(), 0, 0
    started = time.time()
    batch = []
    for k in src.scan_iter(count=1000):
        batch.append(k)
        if len(batch) >= BATCH:
            c, s, sk = _flush(src, dst, batch, dry_run)
            copied += c
            size += s
            skipped += sk
            seen += len(batch)
            batch = []
            print(f"  {seen} keys, {size / 1e6:.1f} MB", flush=True)
    if batch:
        c, s, sk = _flush(src, dst, batch, dry_run)
        copied += c
        size += s
        skipped += sk
        seen += len(batch)
    return {"keys": seen, "copied": dict(copied), "skipped": dict(skipped), "mb": round(size / 1e6, 1),
            "seconds": round(time.time() - started, 1)}


def _flush(src, dst, keys, dry_run):
    rows = _read(src, keys)
    if dry_run:
        c = Counter(t for _, t, _, _ in rows)
        size = sum(len(v) for _, t, _, v in rows if t == "string" and v)
        return c, size, Counter()
    return _write(dst, rows)


def check(src, dst) -> int:
    """Keys whose type or length differ (0: the copy matches)."""
    bad = 0
    n = 0
    for k in src.scan_iter(count=1000):
        n += 1
        t = src.type(k).decode()
        if dst.type(k).decode() != t:
            bad += 1
            print(f"  missing or different type: {k!r}")
            continue
        size = {"string": "strlen", "hash": "hlen", "list": "llen", "set": "scard", "zset": "zcard"}.get(t)
        if size and getattr(src, size)(k) != getattr(dst, size)(k):
            bad += 1
            print(f"  different size: {k!r}")
    print(f"{n} keys checked, {bad} differ")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=os.getenv("UPSTASH_REDIS_URL", ""))
    ap.add_argument("--to", dest="dst", default=os.getenv("LOCAL_REDIS_URL", ""))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.src and a.src == a.dst:
        raise SystemExit("The source and destination are the same Redis: nothing to copy")
    src = connect(a.src, "the source (UPSTASH_REDIS_URL)")
    dst = connect(a.dst, "the destination (LOCAL_REDIS_URL)")
    print(f"Source has {src.dbsize()} keys; destination has {dst.dbsize()}")
    if a.check:
        sys.exit(1 if check(src, dst) else 0)
    got = copy(src, dst, a.dry_run)
    print(("Would copy" if a.dry_run else "Copied") + f": {got}")
    if not a.dry_run:
        print(f"Destination now has {dst.dbsize()} keys. Check with: python redis_copy.py --check")


if __name__ == "__main__":
    main()
