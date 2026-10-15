"""
Per-account saves, booking codes and stats.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import json
from auth import require_user
from fastapi import APIRouter, HTTPException, Request
from typing import Any, Dict, List, Optional

import main

router = APIRouter()


def _ukey(uid: str, suffix: str) -> str:
    return f"betiq:user:{uid}:{suffix}"


@router.get("/api/user/saves")
async def get_saves(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = main._get_redis()
    if not r: return []
    raw = r.get(main._ukey(uid, "saves"))
    return json.loads(raw) if raw else []


def _save_key(p: Dict[str, Any]) -> str:
    return f"{p.get('home')}:{p.get('away')}:{p.get('date')}"


def _set_saved(uid: str, pred: Dict[str, Any], want: Optional[bool]) -> Dict[str, Any]:
    """Save (want=True), remove (False) or toggle (None) one prediction."""
    r = main._get_redis()
    if not r: raise HTTPException(status_code=503, detail="No Redis")
    key = main._ukey(uid, "saves")
    raw = r.get(key)
    saves: List[Dict] = json.loads(raw) if raw else []
    match_key = main._save_key(pred)
    existing = next((i for i, s in enumerate(saves) if main._save_key(s) == match_key), None)
    if want is None:
        want = existing is None
    if want and existing is None:
        saves.insert(0, pred)
        saves = saves[:50]
    elif not want and existing is not None:
        saves.pop(existing)
    r.set(key, json.dumps(saves), ex=365 * 86400)
    return {"saved": want, "count": len(saves)}


@router.post("/api/user/saves")
async def toggle_save(request: Request, body: Dict[str, Any]):
    """Body: {prediction, saved?}. `saved` true/false sets the state explicitly
    (idempotent — safe to retry, and what Undo uses); omitted, it toggles."""
    uid = await require_user(request, body.get("uid", ""))
    pred = body.get("prediction", {})
    if not pred: raise HTTPException(status_code=400, detail="Missing prediction")
    want = body.get("saved")
    return main._set_saved(uid, pred, want if isinstance(want, bool) else None)


@router.delete("/api/user/saves")
async def delete_save(request: Request, home: str, away: str, date: str = "", uid: str = ""):
    """Remove one saved pick. Idempotent: removing something not saved is a no-op."""
    uid = await require_user(request, uid)
    return main._set_saved(uid, {"home": home, "away": away, "date": date}, False)


@router.get("/api/user/codes")
async def get_codes(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = main._get_redis()
    if not r: return []
    raw = r.get(main._ukey(uid, "codes"))
    return json.loads(raw) if raw else []


@router.get("/api/user/stats")
async def get_user_stats(request: Request, uid: str = ""):
    """The account's record from the codes it booked here, settled by the
    server from the results (never self-reported), and what it has saved."""
    import tickets
    uid = await require_user(request, uid)
    r = main._get_redis()
    if not r: return {}
    tickets_raw = r.get(main._ukey(uid, "tickets"))
    saves_raw = r.get(main._ukey(uid, "saves"))
    codes_raw = r.get(main._ukey(uid, "codes"))
    return {
        "tickets": tickets.summary(json.loads(tickets_raw) if tickets_raw else []),
        "saved_count": len(json.loads(saves_raw) if saves_raw else []),
        "codes_count": len(json.loads(codes_raw) if codes_raw else []),
    }
