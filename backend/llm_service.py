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
    Step 1 — use compound-beta to fetch live team news.
    Tries compound-beta-mini first (smaller, fewer 413s), then compound-beta.
    Returns (news_text, source_urls). Silent on failure.
    """
    prompt = f"{home} vs {away} team news?"  # absolute minimum to avoid 413

    for model in ("compound-beta-mini", "compound-beta"):
        try:
            data = await _call(model, [
                {"role": "user", "content": prompt}
            ], max_tokens=150)
            text = data["choices"][0]["message"]["content"].strip()
            sources: List[str] = []
            for tool in data["choices"][0]["message"].get("executed_tools", []):
                for res in tool.get("results", [])[:3]:
                    url = res.get("url") or res.get("link")
                    if url:
                        sources.append(url)
            if text:
                return text, sources
        except Exception as e:
            err = str(e)
            if "413" in err or "request_too_large" in err:
                continue   # try next model silently
            print(f"[LLM] {model} news fetch failed: {e}")
            break

    return "", []


async def extract_model_adjustments(
    home: str, away: str, news_text: str
) -> Dict[str, Any]:
    """
    Step 2b — LLM reads the web search news and returns structured numerical
    adjustments the model should apply before outputting predictions.

    Returns a dict like:
    {
      "home_attack_modifier": -0.20,   # Haaland out → -20% home goals
      "away_attack_modifier": 0.0,
      "home_defense_modifier": 0.0,
      "away_defense_modifier": 0.10,   # key defender back → +10% defence
      "confidence_modifier": -0.05,    # less certain overall
      "flags": ["home_key_striker_out", "away_manager_change"],
      "reasoning": "Haaland ruled out injured..."
    }
    All modifiers are floats between -0.5 and +0.5.
    """
    if not GROQ_API_KEY or not news_text:
        return {}

    prompt = f"""You are a football data analyst. Given this team news for {home} vs {away}:

"{news_text}"

Extract ONLY concrete, confirmed facts and convert them to numerical adjustments.
Respond ONLY with valid JSON, no explanation:

{{
  "home_attack_modifier": <float -0.5 to 0.5, 0.0 if no info>,
  "away_attack_modifier": <float -0.5 to 0.5, 0.0 if no info>,
  "home_defense_modifier": <float -0.5 to 0.5, 0.0 if no info>,
  "away_defense_modifier": <float -0.5 to 0.5, 0.0 if no info>,
  "confidence_modifier": <float -0.15 to 0.0, negative when uncertain>,
  "flags": [<list of strings like "home_key_striker_out", "away_suspended_defender">],
  "reasoning": "<1 sentence explaining the main adjustment>"
}}

Rules:
- Key striker missing = -0.25 home_attack_modifier
- Key goalkeeper missing = -0.20 defense_modifier
- 2+ key players missing = -0.35 attack or defense
- Manager change (new, unproven) = -0.10 confidence_modifier
- If news is vague or unconfirmed, use 0.0
- If no relevant news found, return all zeros"""

    try:
        data = await _call("llama-3.3-70b-versatile", [
            {"role": "user", "content": prompt}
        ], max_tokens=300)
        text = data["choices"][0]["message"]["content"].strip()
        # Extract JSON from response
        import json, re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            adj = json.loads(match.group())
            # Clamp all modifiers to safe range
            for key in ["home_attack_modifier","away_attack_modifier",
                        "home_defense_modifier","away_defense_modifier"]:
                if key in adj:
                    adj[key] = max(-0.5, min(0.5, float(adj[key])))
            if "confidence_modifier" in adj:
                adj["confidence_modifier"] = max(-0.15, min(0.0, float(adj["confidence_modifier"])))
            print(f"[LLM] Adjustments for {home} vs {away}: {adj.get('reasoning','')}")
            return adj
    except Exception as e:
        print(f"[LLM] adjustment extraction failed: {e}")
    return {}


async def fetch_missing_results(home: str, away: str, date: str) -> Dict[str, Any]:
    """
    Search the web for a match result that isn't in our database yet.
    Returns {found: bool, home_goals: int, away_goals: int, result: str} or {found: False}.
    """
    if not GROQ_API_KEY:
        return {"found": False}

    prompt = f"What was the final score of {home} vs {away} on {date}? Reply with ONLY the score like '2-1' or 'not played yet'."

    for model in ("compound-beta-mini", "compound-beta"):
        try:
            data = await _call(model, [{"role": "user", "content": prompt}], max_tokens=50)
            text = data["choices"][0]["message"]["content"].strip()
            import re
            m = re.search(r'(\d+)\s*[-–]\s*(\d+)', text)
            if m:
                hg, ag = int(m.group(1)), int(m.group(2))
                result = "H" if hg > ag else ("A" if ag > hg else "D")
                print(f"[WebSearch] Found result: {home} {hg}-{ag} {away}")
                return {"found": True, "home_goals": hg, "away_goals": ag, "result": result, "source": "web_search"}
        except Exception:
            continue
    return {"found": False}


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
