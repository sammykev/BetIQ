"""
The European competitions model: Champions League, Europa League and
Conference League fixtures, trained and judged apart from the main model.

Why apart: many clubs in Europe play in leagues the main model doesn't
train on (Scotland, Turkey, Austria, Norway...). This model also learns
from those leagues (football_data_sync.EXTRA_DIVISIONS / NEW_LEAGUES, as
context rows: ratings and form, no training rows), and optionally puts every
club on one rating scale across leagues (predictor.LEAGUE_STRENGTH, learned
from the European results ESPN gives club_cups.py). None of that touches the
main model, whose league predictions stay exactly as they are.

It predicts only the competitions where it did at least as well as the
main model in the check (competitions()).

check() replays the last season and a bit of European matches (ESPN's
Champions League, Europa League and Conference League results) through each
configuration in CONFIGS: trained once on everything before, then every
match in date order — European ones predicted (without odds) and scored
before they update the ratings, league ones only updating them. "main" is
what the fixtures get without this model. A configuration that beats it by
MIN_GAIN in log loss on at least MIN_SCORED matches is adopted, stored in
Redis (CHECK_KEY), and trained on everything (train()) — nightly by
train_model.py, published through model_store under the name "europe".

    python europe_model.py            # check, then train and publish if adopted
    python europe_model.py --train    # train and publish with the stored verdict
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

CHECK_KEY = "betiq:europe_model:check"
MODEL_NAME = "europe"
EUROPE_CODES = {"CL", "EL", "UECL"}
EXTRA_LEAGUES = {"JPL", "SPL", "GSL", "TSL", "AUT", "SUI", "DEN", "NOR", "SWE", "POL", "ROU"}
# ...plus club_cups.LEAGUES from ESPN (Cyprus, Ireland, Israel, Malta, Wales,
# Northern Ireland, Czechia, Finland), added in inputs()
MIN_GAIN = 0.01        # log loss per match
MIN_SCORED = 150
KNOWN_MATCHES = 15     # a club with fewer (in the last KNOWN_DAYS) is "unknown"
KNOWN_DAYS = 400
CHECK_SEASONS = 4      # league seasons synced for the check

# name: (extra leagues as context rows, league strength, European rows as)
CONFIGS: Dict[str, Tuple[bool, bool, str]] = {
    "main": (False, False, "full"),
    "extras": (True, False, "full"),
    "extras_strength": (True, True, "strength"),
    "extras_strength_full": (True, True, "full"),
}


def season_start(d: date) -> pd.Timestamp:
    return pd.Timestamp(d.year if d.month >= 7 else d.year - 1, 7, 1)


# ── data ────────────────────────────────────────────────────────────────
def european_rows(espn: pd.DataFrame, ucl: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Every European match, once: the Champions League CSVs up to the
    test start, ESPN's rows (all three competitions) otherwise. KeyHome /
    KeyAway keep the source names, for comparing configurations."""
    parts = []
    if ucl is not None and not ucl.empty:
        u = ucl[ucl["Date"] < start].copy()
        u["Date"] = u["Date"].dt.normalize()
        parts.append(u.assign(league="CL", FromCSV=True))
        ucl_end = u["Date"].max()
    else:
        ucl_end = None
    if espn is not None and not espn.empty:
        e = espn[espn["league"].isin(EUROPE_CODES)].copy()
        if ucl_end is not None:   # the CSVs have those Champions League matches already
            e = e[~((e["league"] == "CL") & (e["Date"] <= ucl_end))]
        parts.append(e.assign(FromCSV=False))
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["KeyHome"], df["KeyAway"] = df["HomeTeam"], df["AwayTeam"]
    df["Europe"] = True
    return df.sort_values("Date", kind="stable").reset_index(drop=True)


def resolve(df: pd.DataFrame, names: set) -> pd.DataFrame:
    from team_names import UCL_ALIASES, TeamResolver
    if df.empty:
        return df
    return TeamResolver(names, aliases=UCL_ALIASES).resolve_frame(df)


def build(league: pd.DataFrame, extras: pd.DataFrame, europe: pd.DataFrame, config: str,
          start: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """One configuration's rows, in date order. `league` is the main model's
    data without its European rows. "main" is the main model as it runs:
    before `start` it trains on the Champions League CSVs only, not ESPN."""
    use_extras, _, mode = CONFIGS[config]
    if config == "main" and start is not None and europe is not None and not europe.empty:
        europe = europe[europe["FromCSV"].fillna(False).astype(bool) | (europe["Date"] >= start)]
    names = set(league["HomeTeam"]) | set(league["AwayTeam"])
    parts = [league.assign(Context=False, StrengthOnly=False, Europe=False)]
    if use_extras and extras is not None and not extras.empty:
        parts.append(extras.assign(Context=True, StrengthOnly=False, Europe=False))
        names |= set(extras["HomeTeam"]) | set(extras["AwayTeam"])
    if europe is not None and not europe.empty:
        e = resolve(europe, names)
        parts.append(e.assign(Context=False, StrengthOnly=(mode == "strength")))
    df = pd.concat(parts, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date", kind="stable").reset_index(drop=True)


def make_model(config: str):
    from predictor import LeaguePredictor
    m = LeaguePredictor()
    m.use_league_strength = CONFIGS[config][1]
    return m


def recent_counts(data: pd.DataFrame, today: date, days: int = KNOWN_DAYS) -> Dict[str, int]:
    """{team: matches in the last `days`} (context rows included)."""
    since = pd.Timestamp(today) - pd.Timedelta(days=days)
    recent = data[pd.to_datetime(data["Date"]) >= since]
    return {str(k): int(v) for k, v in
            pd.concat([recent["HomeTeam"], recent["AwayTeam"]]).value_counts().items()}


# ── the check ───────────────────────────────────────────────────────────
def _known(m, team: str, day: str) -> bool:
    stats = m.team_stats.get(team) or {}
    last = m.last_match_date.get(team)
    if len(stats.get("pts") or []) < KNOWN_MATCHES or not last:
        return False
    return (pd.Timestamp(day) - pd.Timestamp(last)).days <= KNOWN_DAYS


def replay(data: pd.DataFrame, config: str, start: pd.Timestamp,
           log: Callable[[str], None] = print) -> Dict[tuple, Dict[str, Any]]:
    """Train on everything before `start`, then go through the rest in date
    order: European matches are predicted (no odds) and scored, then update
    the ratings as the configuration says; everything else updates them."""
    mode = CONFIGS[config][2]
    train, test = data[data["Date"] < start], data[data["Date"] >= start]
    if len(train) < 200:
        log(f"[Europe] {config}: only {len(train)} rows before {start.date()} — nothing to replay")
        return {}
    log(f"[Europe] {config}: training on {len(train)} rows, replaying {len(test)}")
    m = make_model(config)
    m.train(train.drop(columns=["Europe", "KeyHome", "KeyAway", "FromCSV"], errors="ignore"))
    out: Dict[tuple, Dict[str, Any]] = {}
    for r in test.to_dict("records"):
        day = str(pd.Timestamp(r["Date"]).date())
        lg = str(r.get("league") or "")
        h, a = m.canon(r["HomeTeam"]), m.canon(r["AwayTeam"])
        if r.get("Europe") is True:
            f = m._feats(h, a, 0, 0, 0, match_date=day, league=lg)
            p_h, p_d, p_a, _, _ = m.predict_proba(f)
            out[(day, r["KeyHome"], r["KeyAway"])] = {
                "p": [p_h, p_d, p_a], "result": r["Result"], "league": lg,
                "known": _known(m, h, day) and _known(m, a, day)}
            if mode == "strength":
                m._strength_update(h, a, r["Result"], lg)
            else:
                m._update(h, a, r["Result"], r["FTHG"], r["FTAG"], match_date=day, competition=lg,
                          hst=r.get("HST"), ast=r.get("AST"))
        elif r.get("StrengthOnly") is True:
            m._strength_update(h, a, r["Result"], lg)
        elif r.get("Context") is True:
            m.context_update(r)
        else:
            m._update(h, a, r["Result"], r["FTHG"], r["FTAG"],
                      hyc=r.get("HY"), ayc=r.get("AY"), hrc=r.get("HR"), arc=r.get("AR"),
                      match_date=day, competition=lg, hst=r.get("HST"), ast=r.get("AST"))
    return out


def _scores(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    import backtest
    if not recs:
        return {"matches": 0}
    s = backtest._scores_1x2([r["p"] for r in recs], [r["result"] for r in recs])
    return {"matches": len(recs), **{k: round(v, 4) for k, v in s.items()}}


# Per-competition calibration (predictor.calibrate): fitted on the season
# before the test season, kept only when it helps the test season by CAL_GAIN
CAL_GAIN = 0.005
T_GRID = [round(0.4 + 0.1 * i, 1) for i in range(11)]    # 0.4 .. 1.4
W_GRID = [round(0.05 * i, 2) for i in range(21)]         # 0 .. 1


def rates(europe: pd.DataFrame, code: str, before: pd.Timestamp) -> List[float]:
    """A competition's home / draw / away rates before a date (all European
    matches' when it has under 60)."""
    rows = europe[(europe["Date"] < before) & (europe["league"] == code)]
    if len(rows) < 60:
        rows = europe[europe["Date"] < before]
    vc = rows["Result"].value_counts(normalize=True)
    return [round(float(vc.get(o, 1 / 3)), 4) for o in ("H", "D", "A")]


def _calibrated(recs: List[Dict[str, Any]], cal: Optional[Dict]) -> List[Dict[str, Any]]:
    from predictor import calibrate
    if not cal:
        return recs
    return [{**r, "p": list(calibrate(r["p"], cal))} for r in recs]


def fit_calibration(recs: List[Dict[str, Any]], base: List[float]) -> Optional[Dict[str, Any]]:
    """The temperature and blend weight with the lowest log loss on `recs`."""
    import backtest
    if len(recs) < 40:
        return None
    best, best_ll = None, None
    for t in T_GRID:
        for w in W_GRID:
            cal = {"t": t, "w": w, "base": base}
            ll = backtest._scores_1x2([r["p"] for r in _calibrated(recs, cal)],
                                      [r["result"] for r in recs])["log_loss"]
            if best_ll is None or ll < best_ll:
                best, best_ll = cal, ll
    return best


def _diagnose(recs: Dict[tuple, Dict], main: Dict[tuple, Dict], keys: List[tuple], base: List[float]) -> Dict:
    """Where a competition's log loss comes from: matches with clubs the main
    model knows vs not, and the average forecast against what happened."""
    rows = [recs[k] for k in keys]
    if not rows:
        return {}
    out = {"known_to_main": _scores([recs[k] for k in keys if main[k]["known"]]),
           "unknown_to_main": _scores([recs[k] for k in keys if not main[k]["known"]]),
           "known_here": _scores([r for r in rows if r["known"]]),
           "naive": _scores([{"p": base, "result": r["result"]} for r in rows]),
           "forecast_hda": [round(sum(r["p"][i] for r in rows) / len(rows), 3) for i in range(3)],
           "actual_hda": [round(sum(r["result"] == o for r in rows) / len(rows), 3) for o in ("H", "D", "A")]}
    return out


def check(league: pd.DataFrame, extras: pd.DataFrame, espn: pd.DataFrame, ucl: pd.DataFrame,
          today: Optional[date] = None, log: Callable[[str], None] = print) -> Dict[str, Any]:
    today = today or date.today()
    start = season_start(today) - pd.DateOffset(years=1)
    cal_start = start - pd.DateOffset(years=1)
    europe = european_rows(espn, ucl, start)
    result: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "test_from": str(start.date()),
                              "configs": {}, "adopted": None}
    if europe.empty or (europe["Date"] >= start).sum() < MIN_SCORED:
        result["reason"] = "not enough European matches to test on"
        return result
    runs = {name: replay(build(league, extras, europe, name, start), name, start, log) for name in CONFIGS}
    keys = sorted(set.intersection(*(set(r) for r in runs.values())))
    known_main = [k for k in keys if runs["main"][k]["known"]]
    # A constant guess (the European matches' own rates before the test)
    before = europe[europe["Date"] < start]["Result"].value_counts(normalize=True)
    naive = [float(before.get(o, 1 / 3)) for o in ("H", "D", "A")]
    result["naive"] = _scores([{"p": naive, "result": runs["main"][k]["result"]} for k in keys])
    for name, recs in runs.items():
        result["configs"][name] = {
            "all": _scores([recs[k] for k in keys]),
            "clubs_main_knows": _scores([recs[k] for k in known_main]),
            "both_known_here": _scores([recs[k] for k in keys if recs[k]["known"]]),
            "by_competition": {c: _scores([recs[k] for k in keys if recs[k]["league"] == c])
                               for c in sorted({recs[k]["league"] for k in keys})},
        }
    base = result["configs"]["main"]["all"]["log_loss"] if keys else None
    best = min((n for n in CONFIGS if n != "main"),
               key=lambda n: result["configs"][n]["all"].get("log_loss", 9))
    ll = result["configs"][best]["all"].get("log_loss")
    if len(keys) >= MIN_SCORED and ll is not None and base is not None and ll <= base - MIN_GAIN:
        result["adopted"] = best
    result["reason"] = (f"{best}: {ll} vs main {base} on {len(keys)} matches "
                        f"(needs {MIN_GAIN} better on {MIN_SCORED}+)")

    # Per competition: which model, and whether a calibration fitted on the
    # season before helps this one
    names = ["main"] + ([result["adopted"]] if result["adopted"] else [])
    earlier = {}
    for name in names:
        recs = replay(build(league, extras, europe, name, cal_start), name, cal_start, log)
        earlier[name] = [r for k, r in recs.items() if k[0] < str(start.date())]
    plan, diagnostics = {}, {}
    for code in sorted({runs["main"][k]["league"] for k in keys}):
        ck = [k for k in keys if runs["main"][k]["league"] == code]
        fit_base, prod_base = rates(europe, code, cal_start), rates(europe, code, start)
        options = []
        diagnostics[code] = {}
        for name in names:
            test = [runs[name][k] for k in ck]
            plain = _scores(test)
            cal = fit_calibration([r for r in earlier[name] if r["league"] == code], fit_base)
            with_cal = _scores(_calibrated(test, cal)) if cal else None
            diagnostics[code][name] = {**_diagnose(runs[name], runs["main"], ck, prod_base),
                                       "calibration_fitted_before": cal, "calibrated": with_cal}
            options.append((plain["log_loss"], name, False, plain))
            if with_cal and with_cal["log_loss"] <= plain["log_loss"] - CAL_GAIN:
                options.append((with_cal["log_loss"], name, True, with_cal))
        if len(ck) < MIN_COMPETITION_MATCHES:
            options = [o for o in options if o[1] == "main" and not o[2]]
        score, name, use_cal, sc = min(options, key=lambda o: o[0])
        plan[code] = {
            "model": "main" if name == "main" else "europe", "config": name,
            # For live fixtures: refitted on the test season, the most recent
            "calibration": fit_calibration([runs[name][k] for k in ck], prod_base) if use_cal else None,
            "test": sc, "main_plain": _scores([runs["main"][k] for k in ck]),
        }
    result["plan"], result["diagnostics"] = plan, diagnostics
    result["competitions"] = competitions(result)
    return result


# ── production ──────────────────────────────────────────────────────────
def inputs(app, seasons: int = 2) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(league rows, extra leagues, ESPN European rows, Champions League CSV
    rows) from the app's own loaders; league rows are the main model's data
    without its European rows."""
    import club_cups
    import model_store
    assembled = app._assemble_training_data()
    if assembled is None:
        raise RuntimeError("no training data")
    _, combined, _ = assembled
    league = combined
    if "Source" in league.columns:
        ucl = league[league["Source"] == "ucl"]
        league = league[league["Source"] != "ucl"]
    else:
        ucl = pd.DataFrame()
    if "league" in league.columns:
        # National teams have nothing to do with European club matches: left
        # out, the model is smaller and quicker to train and load
        league = league[~league["league"].isin(EUROPE_CODES | {"INT"})]
    league = league.drop(columns=["StrengthOnly", "Source"], errors="ignore")
    ucl = ucl.drop(columns=["StrengthOnly", "Source", "Context"], errors="ignore")
    names = set(league["HomeTeam"]) | set(league["AwayTeam"])
    extras = app._load_extra_leagues(names, leagues=EXTRA_LEAGUES)
    r = model_store._client()
    small = espn_leagues(club_cups.load(r, club_cups.LEAGUES_KEY), names | set(extras.get("HomeTeam", [])) |
                         set(extras.get("AwayTeam", [])))
    if not small.empty:
        extras = pd.concat([extras, small], ignore_index=True) if not extras.empty else small
    espn = club_cups.rows_frame(club_cups.load(r), EUROPE_CODES)
    return league, extras, espn, ucl


def espn_leagues(data: Dict[str, Any], known: set) -> pd.DataFrame:
    """The small domestic leagues ESPN gives club_cups (Cyprus, Ireland,
    Israel...) as context rows; a club whose name clashes with a known club
    elsewhere gets its league added, as main._load_extra_leagues does."""
    import club_cups
    from team_names import normalise
    df = club_cups.rows_frame(data, club_cups.LEAGUE_CODES)
    if df.empty:
        return df
    clash = {normalise(n) for n in known}
    for col in ("HomeTeam", "AwayTeam"):
        df[col] = [f"{n} ({lg})" if normalise(n) in clash else n for n, lg in zip(df[col], df["league"])]
    df["Context"] = True
    print(f"[Europe] ESPN leagues: {len(df)} matches for ratings only "
          f"({', '.join(f'{k} {v}' for k, v in df['league'].value_counts().items())})")
    return df


MIN_COMPETITION_MATCHES = 50


def competitions(verdict: Dict[str, Any]) -> List[str]:
    """The competitions the adopted configuration predicts at least as well
    as the main model (on MIN_COMPETITION_MATCHES+ of their matches); the
    rest keep the main model. The first check: better in the Champions and
    Europa League, worse in the Conference League."""
    if verdict.get("plan"):
        return sorted(c for c, v in verdict["plan"].items() if v.get("model") == "europe" and verdict.get("adopted"))
    adopted = verdict.get("adopted")
    configs = verdict.get("configs") or {}
    if not adopted or adopted not in configs or "main" not in configs:
        return []
    ours, main = configs[adopted].get("by_competition") or {}, configs["main"].get("by_competition") or {}
    out = []
    for code, sc in ours.items():
        base = (main.get(code) or {}).get("log_loss")
        if sc.get("matches", 0) >= MIN_COMPETITION_MATCHES and base is not None \
                and sc.get("log_loss") is not None and sc["log_loss"] <= base:
            out.append(code)
    return sorted(out)


def calibrations(verdict: Dict[str, Any]) -> Dict[str, Dict[str, Dict]]:
    """{"main": {competition: calibration}, "europe": {...}} from the plan."""
    out: Dict[str, Dict[str, Dict]] = {"main": {}, "europe": {}}
    for code, v in (verdict.get("plan") or {}).items():
        if v.get("calibration"):
            model = v.get("model") if v.get("model") == "main" or verdict.get("adopted") else None
            if model:
                out[model][code] = v["calibration"]
    return out


def load_check(r) -> Dict[str, Any]:
    raw = r.get(CHECK_KEY) if r is not None else None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def train(league, extras, espn, ucl, config: str, today: Optional[date] = None):
    """(model, recent match counts per club) on everything, as `config`."""
    europe = european_rows(espn, ucl, pd.Timestamp.max)
    data = build(league, extras, europe, config)
    m = make_model(config)
    m.train(data.drop(columns=["Europe", "KeyHome", "KeyAway", "FromCSV"], errors="ignore"))
    return m, recent_counts(data, today or date.today())


def train_and_publish(app, config: str) -> Dict[str, Any]:
    import model_store
    from predictor import MODEL_CACHE_VERSION
    t0 = time.time()
    league, extras, espn, ucl = inputs(app)
    m, counts = train(league, extras, espn, ucl, config)
    meta = model_store.publish(m.to_bytes(), MODEL_CACHE_VERSION, {
        "source": "github-actions", "config": config, "recent_counts": counts,
        "competitions": competitions(load_check(model_store._client())),
        "extras": len(extras), "european_rows": len(espn) + len(ucl),
        "commit": os.getenv("GITHUB_SHA", "")[:7]}, name=MODEL_NAME)
    print(f"[Europe] Published the Europe model ({config}): {meta['size'] / 1e6:.1f} MB, "
          f"{time.time() - t0:.0f}s")
    return meta


# Leagues of European clubs football-data.co.uk doesn't carry: ESPN slugs to
# try (probe_leagues), mostly the Conference League's smaller countries
PROBE_SLUGS = ["cyp.1", "irl.1", "cro.1", "cze.1", "srb.1", "hun.1", "isr.1", "bul.1", "svn.1", "svk.1",
               "fin.1", "isl.1", "ukr.1", "bih.1", "arm.1", "kaz.1", "mlt.1", "aze.1", "geo.1", "mda.1",
               "blr.1", "ltu.1", "lva.1", "est.1", "alb.1", "mkd.1", "kos.1", "mne.1", "gib.1", "wal.1",
               "nir.1", "lux.1", "and.1", "fro.1",
               # covered by football-data.co.uk, for comparison
               "aut.1", "sui.1", "den.1", "nor.1", "swe.1", "pol.1", "rou.1", "gre.1", "tur.1", "bel.1", "sco.1"]


async def probe_leagues(days: int = 45) -> Dict[str, Any]:
    """Which ESPN league scoreboards answer, and with how many matches."""
    from curl_cffi.requests import AsyncSession
    import international_fixtures as intl
    end = date.today()
    start = end - pd.Timedelta(days=days)
    out: Dict[str, Any] = {}
    async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=30) as session:
        for slug in PROBE_SLUGS:
            try:
                # The default view (the current round) first: a date range can
                # answer 400 where the league is fine
                r = await session.get(f"{intl.ESPN_BASE}/{slug}/scoreboard", headers=intl._ESPN_HEADERS)
                data = r.json() if r.status_code == 200 else {}
                if r.status_code == 200 and not data.get("events"):
                    r2 = await session.get(f"{intl.ESPN_BASE}/{slug}/scoreboard", headers=intl._ESPN_HEADERS,
                                           params={"dates": f"{start:%Y%m%d}-{end:%Y%m%d}"})
                    if r2.status_code == 200:
                        data = r2.json()
            except Exception as e:
                out[slug] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
                continue
            events = data.get("events") or []
            league = ((data.get("leagues") or [{}])[0]).get("name")
            teams = sorted({c["team"]["displayName"] for ev in events[:30]
                            for c in (ev.get("competitions") or [{}])[0].get("competitors") or []})[:8]
            out[slug] = {"status": r.status_code, "league": league, "events": len(events), "teams": teams}
            await asyncio.sleep(0.5)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", action="store_true", help="only train and publish, with the stored verdict")
    ap.add_argument("--probe-leagues", action="store_true", help="only list which ESPN leagues answer")
    args = ap.parse_args(argv)
    if args.probe_leagues:
        for slug, v in asyncio.run(probe_leagues()).items():
            print(f"{slug:7} {json.dumps(v, ensure_ascii=False)}")
        return 0
    import main as app
    import model_store
    from football_data_sync import recent_seasons, sync, sync_extra
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set. Skipping.")
        return 0
    seasons = recent_seasons(date.today(), CHECK_SEASONS)
    asyncio.run(sync(app.FOOTBALL_DATA_DIR, seasons=seasons))
    print("[Europe] Extra leagues:", json.dumps(asyncio.run(sync_extra(app.EXTRA_DATA_DIR, seasons=seasons))))
    verdict = load_check(r)
    if not args.train:
        league, extras, espn, ucl = inputs(app)
        print(f"[Europe] {len(league)} league rows, {len(extras)} extra-league rows, "
              f"{len(espn)} ESPN European rows, {len(ucl)} Champions League CSV rows")
        verdict = check(league, extras, espn, ucl)
        r.set(CHECK_KEY, json.dumps(verdict))
        print("[Europe] Check:", json.dumps(verdict, indent=1))
    if verdict.get("adopted"):
        train_and_publish(app, verdict["adopted"])
    else:
        print("[Europe] No configuration adopted:", verdict.get("reason"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
