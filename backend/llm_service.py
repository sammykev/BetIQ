"""
Groq-powered match explanation service.

Two-step approach to avoid compound-beta's request size limits:
1. compound-beta (small prompt) → fetch live injury/team news
2. llama-3.3-70b-versatile → combine news + stats into a full explanation
"""

import os
import httpx
from typing import Dict, Any, List

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


async def _call(model: str, messages: list, max_tokens: int = 400) -> Dict:
    """Raw Groq API call. Returns parsed JSON or raises."""
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3},
        )
    if r.status_code != 200:
        raise RuntimeError(f"{model} {r.status_code}: {r.text[:120]}")
    return r.json()


async def _fetch_news(home: str, away: str) -> tuple[str, List[str]]:
    """
    Step 1 — use compound-beta with a tiny prompt to search for team news.
    Returns (news_text, source_urls).
    """
    try:
        data = await _call("compound-beta", [
            {"role": "user", "content":
                f"Search for the latest injury news, suspensions, and lineup updates for "
                f"{home} and {away} ahead of their upcoming match. "
                f"Return 2-3 sentences of key facts only."}
        ], max_tokens=200)

        text = data["choices"][0]["message"]["content"].strip()
        sources: List[str] = []
        for tool in data["choices"][0]["message"].get("executed_tools", []):
            for res in tool.get("results", [])[:3]:
                url = res.get("url") or res.get("link")
                if url:
                    sources.append(url)
        return text, sources
    except Exception as e:
        print(f"[LLM] compound-beta news fetch failed: {e}")
        return "", []


async def explain_match(
    home: str,
    away: str,
    analysis: Dict[str, Any],
    prediction: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a plain-language match explanation with live qualitative context.
    Returns {explanation, sources, model, error}
    """
    if not GROQ_API_KEY:
        return {"explanation": None, "sources": [], "model": None, "error": "no_key"}

    # Step 1 — fetch live news (small compound-beta call)
    news_text, sources = await _fetch_news(home, away)

    # Step 2 — generate the full explanation with llama
    elo  = analysis.get("elo", {})
    rec  = analysis.get("recommended", {})
    xg_h = analysis.get("xg_home", 0)
    xg_a = analysis.get("xg_away", 0)

    stats_block = (
        f"Elo: {home} {elo.get('home','?')} vs {away} {elo.get('away','?')} "
        f"(gap {elo.get('gap',0):+.0f} — {elo.get('label','?')}). "
        f"xG: {home} {xg_h:.2f} vs {away} {xg_a:.2f}. "
        f"Win probs: {home} {round(prediction.get('p_home',0)*100)}% / "
        f"Draw {round(prediction.get('p_draw',0)*100)}% / "
        f"{away} {round(prediction.get('p_away',0)*100)}%. "
        f"Best pick: {rec.get('label','?')} @ {round(rec.get('prob',0)*100)}%."
    )

    if news_text:
        news_block = f"Live team news (from web search today): {news_text}"
        news_instruction = (
            "Reference the live news above when relevant — flag injuries or absences "
            "that contradict the model's pick."
        )
    else:
        news_block = ""
        news_instruction = (
            "IMPORTANT: You do NOT have access to current team news for this match. "
            "Do NOT mention injuries, suspensions, or lineup changes — your training data "
            "is outdated and any such claims would be wrong. "
            "Focus only on the statistical data provided."
        )

    prompt = (
        f"You are a sharp football analyst. Write a 4-sentence match preview for "
        f"{home} vs {away}.\n\n"
        f"Stats: {stats_block}\n"
        f"{news_block}\n\n"
        f"{news_instruction} "
        f"Explain why the model favours one side using only the numbers given, "
        f"end with a confidence verdict. No bullet points — flowing prose only."
    )

    try:
        data = await _call("llama-3.3-70b-versatile", [
            {"role": "user", "content": prompt}
        ], max_tokens=350)

        text = data["choices"][0]["message"]["content"].strip()
        used_web = bool(sources)
        print(f"[LLM] Explained {home} vs {away} "
              f"({'compound-beta+llama' if used_web else 'llama-only'}, {len(sources)} sources)")
        return {
            "explanation": text,
            "sources": sources,
            "model": "compound-beta+llama" if used_web else "llama-3.3-70b-versatile",
            "error": None,
        }

    except Exception as e:
        print(f"[LLM] llama explanation failed: {e}")
        return {"explanation": None, "sources": [], "model": None, "error": "all_models_failed"}
