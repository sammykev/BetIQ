"""
Groq-powered match explanation service.
Uses compound-beta (web-search enabled) to combine statistical data
with live qualitative context (injuries, lineups, team news).
"""

import os
import httpx
from typing import Dict, Any, List

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM = """You are a sharp, concise football analyst for BetIQ.

Given a match prediction with statistical data, write a 4-5 sentence analysis:
1. Explain WHY the model favours one side — reference Elo gap, xG, and form.
2. Search the web for the LATEST injury news, suspensions, and lineup updates for BOTH teams.
3. If key players are missing for the favoured side, flag it clearly as a risk.
4. End with a one-sentence confidence verdict.

Style: punchy and direct — no bullet points, no headers, just flowing analyst prose."""


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

    elo  = analysis.get("elo", {})
    rec  = analysis.get("recommended", {})
    xg_h = analysis.get("xg_home", 0)
    xg_a = analysis.get("xg_away", 0)

    user_msg = (
        f"Match: **{home} vs {away}**\n\n"
        f"Statistical snapshot:\n"
        f"- Elo: {home} {elo.get('home','?')} · {away} {elo.get('away','?')} "
        f"(gap {elo.get('gap',0):+.0f} pts — {elo.get('label','?')})\n"
        f"- xG: {home} {xg_h:.2f} vs {away} {xg_a:.2f}\n"
        f"- Win probs: {home} {round(prediction.get('p_home',0)*100)}% / "
        f"Draw {round(prediction.get('p_draw',0)*100)}% / "
        f"{away} {round(prediction.get('p_away',0)*100)}%\n"
        f"- Best pick: {rec.get('label','?')} @ {round(rec.get('prob',0)*100)}% confidence\n"
        f"- Tip: {prediction.get('tip_1x2','?')} | Goals: {prediction.get('tip_goals','?')}\n\n"
        f"Search the web for the latest team news, injuries, and lineup updates for "
        f"**{home}** and **{away}**, then write your analysis."
    )

    for model in ("compound-beta", "llama-3.3-70b-versatile"):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": SYSTEM},
                            {"role": "user",   "content": user_msg},
                        ],
                        "max_tokens": 450,
                        "temperature": 0.3,
                    },
                )

            if r.status_code != 200:
                print(f"[LLM] {model} → {r.status_code}: {r.text[:200]}")
                continue

            data   = r.json()
            text   = data["choices"][0]["message"]["content"].strip()
            sources: List[str] = []

            # Extract web search sources from compound-beta tool calls
            for tool in data["choices"][0]["message"].get("executed_tools", []):
                for result in tool.get("results", [])[:3]:
                    url = result.get("url") or result.get("link")
                    if url:
                        sources.append(url)

            print(f"[LLM] Explained {home} vs {away} via {model} ({len(sources)} sources)")
            return {"explanation": text, "sources": sources, "model": model, "error": None}

        except Exception as e:
            print(f"[LLM] {model} error: {e}")
            continue

    return {"explanation": None, "sources": [], "model": None, "error": "all_models_failed"}
