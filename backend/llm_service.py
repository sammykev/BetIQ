"""
LLM routing for match analysis:
- Groq (compound-beta)              → live web search / news fetching
- Groq (deepseek-r1-distill-llama-70b) → fast match explanations
- DeepSeek API (deepseek-reasoner)  → analytical stat → number extraction
"""

import os
import httpx
from typing import Dict, Any, List

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Groq-hosted DeepSeek R1 distill — fast, good reasoning
_GROQ_R1 = "deepseek-r1-distill-llama-70b"


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


async def _call_deepseek(messages: list, max_tokens: int = 400) -> Dict:
    """DeepSeek API call using deepseek-reasoner (R1). No temperature param — reasoner sets it internally."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-reasoner", "messages": messages, "max_tokens": max_tokens},
        )
    if r.status_code != 200:
        raise RuntimeError(f"deepseek-reasoner {r.status_code}: {r.text[:120]}")
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
    if not news_text:
        return {}
    if not DEEPSEEK_API_KEY and not GROQ_API_KEY:
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

    import json, re

    async def _parse_adj(text: str) -> Dict[str, Any]:
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            return {}
        adj = json.loads(m.group())
        for key in ["home_attack_modifier", "away_attack_modifier",
                    "home_defense_modifier", "away_defense_modifier"]:
            if key in adj:
                adj[key] = max(-0.5, min(0.5, float(adj[key])))
        if "confidence_modifier" in adj:
            adj["confidence_modifier"] = max(-0.15, min(0.0, float(adj["confidence_modifier"])))
        return adj

    # Primary: DeepSeek reasoner (R1) — best for analytical inference
    if DEEPSEEK_API_KEY:
        try:
            data = await _call_deepseek([{"role": "user", "content": prompt}], max_tokens=400)
            text = data["choices"][0]["message"]["content"].strip()
            adj = await _parse_adj(text)
            if adj:
                print(f"[LLM/DeepSeek-R1] Adjustments for {home} vs {away}: {adj.get('reasoning','')}")
                return adj
        except Exception as e:
            print(f"[LLM] deepseek-reasoner adjustment failed, falling back to Groq: {e}")

    # Fallback: Groq R1 distill
    if GROQ_API_KEY:
        try:
            data = await _call(_GROQ_R1, [{"role": "user", "content": prompt}], max_tokens=300)
            text = data["choices"][0]["message"]["content"].strip()
            adj = await _parse_adj(text)
            if adj:
                print(f"[LLM/Groq-R1] Adjustments for {home} vs {away}: {adj.get('reasoning','')}")
                return adj
        except Exception as e:
            print(f"[LLM] Groq R1 adjustment extraction failed: {e}")

    return {}


async def fetch_team_form_web(team: str, competition: str = "", debug: bool = False) -> Dict[str, Any]:
    """
    Use compound-beta web search + R1 extraction to fetch a team's
    last 10 results with goals and xG (when available).

    `competition` (e.g. "FIFA World Cup", "AFCON") narrows the search when
    known — national teams play across friendlies, qualifiers, and
    tournaments, and stats sites like Opta/The Analyst, FotMob, and FBref
    publish tournament-specific xG that a generic team-name search often
    misses. Naming the competition explicitly steers the web search toward
    that data instead of whatever's most recently indexed for the team.

    Returns:
    {
      "matches": [
        {"date": "2026-06-19", "opponent": "Haiti", "home": true,
         "scored": 3, "conceded": 0, "xg_for": 2.8, "xg_against": 0.4, "result": "W"}
      ],
      "avg_scored": 2.1,
      "avg_conceded": 0.6,
      "avg_xg_for": 1.9,       # null when xG not found in search
      "avg_xg_against": 0.7,   # null when xG not found in search
    }
    Returns {} on failure — or, if debug=True, a dict with "_debug_stage"
    ("no_api_key" | "search" | "extraction") and "_debug_detail" describing
    exactly where and why it failed, plus the raw search text if the search
    step succeeded (useful to tell "search found nothing" apart from "search
    found data but extraction couldn't parse it").
    """
    def _fail(stage: str, detail: str, **extra) -> Dict[str, Any]:
        if not debug:
            return {}
        return {"_debug_stage": stage, "_debug_detail": detail, **extra}

    if not GROQ_API_KEY:
        return _fail("no_api_key", "GROQ_API_KEY not set")

    comp_hint = f" {competition}" if competition else ""
    search_prompt = (
        f"{team}{comp_hint} football last 10 match results 2025 2026 "
        f"goals scored conceded xG expected goals statistics"
    )

    search_text = ""
    search_errors = []
    for model in ("compound-beta-mini", "compound-beta"):
        try:
            data = await _call(model, [{"role": "user", "content": search_prompt}], max_tokens=300)
            text = data["choices"][0]["message"]["content"].strip()
            if text:
                search_text = text
                break
        except Exception as e:
            err = str(e)
            search_errors.append(f"{model}: {err}")
            if "413" in err or "request_too_large" in err:
                continue
            print(f"[WebForm] {model} search failed for {team}: {e}")
            break

    if not search_text:
        detail = "; ".join(search_errors) if search_errors else "both compound-beta models returned empty content"
        return _fail("search", detail)

    extract_prompt = f"""Extract {team}'s last 10 football match results from this text.
Text: "{search_text}"

Reply ONLY with valid JSON, no extra text:
{{
  "matches": [
    {{"date": "YYYY-MM-DD", "opponent": "TeamName", "home": true,
      "scored": 2, "conceded": 1, "xg_for": 1.8, "xg_against": 0.7, "result": "W"}}
  ],
  "avg_scored": 1.5,
  "avg_conceded": 0.8,
  "avg_xg_for": null,
  "avg_xg_against": null
}}
Rules:
- result: "W" win / "D" draw / "L" loss  (from {team}'s perspective)
- home: true if {team} played at home
- xg_for / xg_against: null if not mentioned
- avg_xg_for / avg_xg_against: average over matches (null if xG unavailable)
- Only include matches with confirmed final scores — skip future/pending matches
- Most recent match first"""

    import json, re

    async def _try_parse(text: str) -> Dict:
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            return {}
        try:
            parsed = json.loads(m.group())
            if isinstance(parsed.get("matches"), list) and parsed["matches"]:
                return parsed
        except json.JSONDecodeError:
            pass
        return {}

    extraction_errors = []

    # Primary: DeepSeek R1 reasoner (best structured extraction)
    if DEEPSEEK_API_KEY:
        try:
            data = await _call_deepseek([{"role": "user", "content": extract_prompt}], max_tokens=600)
            raw = data["choices"][0]["message"]["content"]
            result = await _try_parse(raw)
            if result:
                print(f"[WebForm/R1] {team}: {len(result['matches'])} matches, "
                      f"xG={'yes' if result.get('avg_xg_for') else 'no'}")
                return result
            extraction_errors.append(f"DeepSeek: parsed but no usable 'matches' — raw: {raw[:200]!r}")
        except Exception as e:
            print(f"[WebForm] DeepSeek extraction failed for {team}: {e}")
            extraction_errors.append(f"DeepSeek: {e}")

    # Fallback: Groq R1 distill
    if GROQ_API_KEY:
        try:
            data = await _call(_GROQ_R1, [{"role": "user", "content": extract_prompt}], max_tokens=600)
            raw = data["choices"][0]["message"]["content"]
            result = await _try_parse(raw)
            if result:
                print(f"[WebForm/Groq] {team}: {len(result['matches'])} matches, "
                      f"xG={'yes' if result.get('avg_xg_for') else 'no'}")
                return result
            extraction_errors.append(f"Groq R1: parsed but no usable 'matches' — raw: {raw[:200]!r}")
        except Exception as e:
            print(f"[WebForm] Groq extraction failed for {team}: {e}")
            extraction_errors.append(f"Groq R1: {e}")

    return _fail("extraction", "; ".join(extraction_errors) or "no extraction backend available",
                search_text=search_text[:500])


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
    if not GROQ_API_KEY and not DEEPSEEK_API_KEY:
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

    used_web = bool(sources)
    label_prefix = "compound-beta+" if used_web else ""

    # Primary: Groq R1 distill — fast, strong reasoning for match previews
    if GROQ_API_KEY:
        try:
            data = await _call(_GROQ_R1, [{"role": "user", "content": prompt}], max_tokens=350)
            text = data["choices"][0]["message"]["content"].strip()
            model_tag = f"{label_prefix}deepseek-r1-distill"
            print(f"[LLM] Explained {home} vs {away} ({model_tag}, {len(sources)} sources)")
            return {"explanation": text, "sources": sources, "model": model_tag, "error": None}
        except Exception as e:
            print(f"[LLM] Groq R1 explanation failed, trying DeepSeek API: {e}")

    # Fallback: DeepSeek API reasoner
    if DEEPSEEK_API_KEY:
        try:
            data = await _call_deepseek([{"role": "user", "content": prompt}], max_tokens=350)
            text = data["choices"][0]["message"]["content"].strip()
            model_tag = f"{label_prefix}deepseek-reasoner"
            print(f"[LLM] Explained {home} vs {away} ({model_tag}, {len(sources)} sources)")
            return {"explanation": text, "sources": sources, "model": model_tag, "error": None}
        except Exception as e:
            print(f"[LLM] DeepSeek reasoner explanation failed: {e}")

    return {"explanation": None, "sources": [], "model": None, "error": "all_models_failed"}
