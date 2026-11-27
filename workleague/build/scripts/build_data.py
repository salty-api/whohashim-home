#!/usr/bin/env python3
"""
Build the Work League (ESPN 591271) dashboard from data/raw/{year}.json (see fetch_espn.py).
Writes data/processed/*.json and ../index.html (template with data embedded) + ../data.json.
Wins/champions come straight from ESPN (no payout data for this league).
"""
import json, re, statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"
TEMPLATE = ROOT / "dashboard" / "template.html"
SITE = ROOT.parent                      # whohashim-home/workleague/
LOGO_DIR = SITE / "logos"
OUT.mkdir(parents=True, exist_ok=True)

NAME_OVERRIDES = {"r saltiel": "Rob S"}   # (first last, lowercase) -> display name


def load_years():
    return {int(f.stem): json.loads(f.read_text()) for f in sorted(RAW.glob("*.json"))}


def build_owner_names(years):
    """Member ids are not stable across seasons (people recreate ESPN accounts), so merge on first+last name."""
    key = lambda m: f"{(m['firstName'] or '').strip()} {(m['lastName'] or '').strip()}".lower()
    latest = {}                            # key -> member from the most recent season (display spelling)
    for y in sorted(years):
        for m in years[y]["members"]:
            latest[key(m)] = m
    names, used = {}, {}
    for k, m in sorted(latest.items()):
        first = (m["firstName"] or "").strip().title().split()[0] if (m["firstName"] or "").strip() else m["displayName"]
        last = (m["lastName"] or "").strip()
        n = NAME_OVERRIDES.get(k) or (f"{first} {last[0].upper()}" if last else first)
        if n in used:                      # two people share first name + last initial -> use full last name
            n = f"{first} {last.title()}"
        used[n] = k
        names[k] = n
    id_to_name = {}
    for y in years:
        for m in years[y]["members"]:
            id_to_name[m["id"]] = names[key(m)]
    return id_to_name


def main():
    years = load_years()
    owner_of = build_owner_names(years)
    cur_year = max(years)
    seasons, matchups, live = [], [], None

    for y in sorted(years):
        d = years[y]
        teams = {t["id"]: t for t in d["teams"]}
        po_n = d["settings"]["scheduleSettings"]["playoffTeamCount"]
        reg_weeks = d["settings"]["scheduleSettings"]["matchupPeriodCount"]
        cur_week = d["status"].get("currentMatchupPeriod") or 0
        done = y < cur_year or _season_over(d)   # in-progress seasons have no champion yet
        own = {tid: owner_of[t["primaryOwner"]] for tid, t in teams.items()}

        # games (skip byes + consolation brackets: only the championship bracket counts as playoffs)
        games = []
        for s in d["schedule"]:
            if "away" not in s: continue
            tier = s.get("playoffTierType")
            if tier not in ("NONE", "WINNERS_BRACKET"): continue
            wk = s["matchupPeriodId"]
            played = (y < cur_year) or wk < cur_week or (done and y == cur_year)
            h, a = s["home"], s["away"]
            games.append({"week": wk, "playoff": tier == "WINNERS_BRACKET", "a": h["teamId"], "b": a["teamId"],
                          "a_pts": (h.get("totalPoints") or 0) if played else 0.0,
                          "b_pts": (a.get("totalPoints") or 0) if played else 0.0,
                          "played": played and ((h.get("totalPoints") or 0) + (a.get("totalPoints") or 0)) > 0,
                          "live": (h.get("totalPointsLive"), a.get("totalPointsLive"))})

        reg_scores = [x for g in games if not g["playoff"] and g["played"] for x in (g["a_pts"], g["b_pts"])]
        avg = statistics.mean(reg_scores) if reg_scores else None
        by_week = defaultdict(list)
        for g in games:
            if g["played"]: by_week[g["week"]] += [g["a_pts"], g["b_pts"]]
        for g in games:
            for me, op, ms, os_ in ((g["a"], g["b"], g["a_pts"], g["b_pts"]), (g["b"], g["a"], g["b_pts"], g["a_pts"])):
                rank = (1 + sum(1 for x in by_week[g["week"]] if x > ms)) if g["played"] else None
                matchups.append({"year": y, "week": g["week"], "owner": own[me], "opponent": own[op],
                                 "score": round(ms, 2), "opp_score": round(os_, 2),
                                 "result": 1 if (g["played"] and ms > os_) else 0,
                                 "game_type": "Playoffs" if g["playoff"] else "Regular Season",
                                 "score_rank": rank, "avg_score": avg,
                                 "score_index": round(ms / avg, 4) if (avg and g["played"] and not g["playoff"]) else None,
                                 "source": "espn"})

        # season rows. In-progress seasons derive the record from played regular-season games.
        reg = defaultdict(list)
        for m in matchups:
            if m["year"] == y and m["game_type"] == "Regular Season" and (m["score"] or m["opp_score"]):
                reg[m["owner"]].append(m)
        rows = []
        for tid, t in teams.items():
            o = own[tid]; ov = t["record"]["overall"]
            g = reg.get(o, [])
            wins = sum(1 for m in g if m["score"] > m["opp_score"]); losses = sum(1 for m in g if m["score"] < m["opp_score"])
            if done:
                wins, losses = ov["wins"], ov["losses"]
            pf = round(sum(m["score"] for m in g), 2) if not done else round(ov["pointsFor"], 2)
            pa = round(sum(m["opp_score"] for m in g), 2) if not done else round(ov["pointsAgainst"], 2)
            rows.append({"season": y, "owner": o, "team_name": t["name"], "current_owner": False, "wins": wins, "losses": losses,
                         "points_for": pf, "points_against": pa, "_tid": tid, "_seed": t.get("playoffSeed"), "_final": t.get("rankCalculatedFinal")})
        order = sorted(rows, key=lambda r: (-r["wins"], -r["points_for"]))
        pf_order = sorted(rows, key=lambda r: -r["points_for"])
        lg_pf = statistics.mean(r["points_for"] for r in rows) or 1
        lg_pa = statistics.mean(r["points_against"] for r in rows) or 1
        for r in rows:
            seed = r["_seed"] if done and r["_seed"] else order.index(r) + 1
            r["reg_season_finish"] = seed
            r["finish"] = r["_final"] if done else None
            r["playoff_finish"] = r["finish"] if (done and seed <= po_n) else None
            r["championships"] = 1 if r["finish"] == 1 else 0
            r["playoff_appearances"] = 1 if (done and seed <= po_n) else 0
            r["reg_season_1s"] = 1 if (done and seed == 1) else 0
            r["buy_in"] = r["won"] = r["profit"] = None
            r["qb"] = r["top_rb"] = r["top_wr"] = r["te"] = r["rules"] = ""
            r["pts_for_index"] = round(r["points_for"] / lg_pf, 4)
            r["pts_against_index"] = round(r["points_against"] / lg_pa, 4)
            r["reg_season_pts_rank"] = pf_order.index(r) + 1
            half = len(rows) // 2
            r["weekly_top6_scores"] = float(sum(1 for m in matchups if m["year"] == y and m["owner"] == r["owner"]
                                                and m["game_type"] == "Regular Season" and m["score_rank"] and m["score_rank"] <= half))
            for k in ("_tid", "_seed", "_final"): del r[k]
            seasons.append(r)

        if y == cur_year and not done:
            gm = [g for g in games if g["week"] == cur_week]
            live = {"year": y, "week": cur_week, "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "games": [{"a": own[g["a"]], "b": own[g["b"]],
                               "a_pts": round(g["live"][0] or 0, 2), "b_pts": round(g["live"][1] or 0, 2)} for g in gm]}

    cur_owners = sorted({s["owner"] for s in seasons if s["season"] == cur_year})
    for s in seasons:
        s["current_owner"] = s["owner"] in cur_owners
    owners = sorted({s["owner"] for s in seasons})
    meta = {"owners": owners, "current_owners": cur_owners, "years": sorted(years), "matchup_years": sorted(years),
            "coverage_note": f"ESPN league 591271, {min(years)}-{max(years)}. Records and champions only (no payout data)."}

    h2h = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "pf": 0.0, "pa": 0.0}))
    for m in matchups:
        if not m["score"]: continue
        r = h2h[m["owner"]][m["opponent"]]
        r["wins"] += m["result"]; r["losses"] += 1 - m["result"]; r["pf"] += m["score"]; r["pa"] += m["opp_score"]
    h2h = [{"owner": o, "opponent": p, **v} for o, d_ in h2h.items() for p, v in d_.items()]

    for n, v in (("seasons", seasons), ("matchups", matchups), ("h2h", h2h), ("meta", meta)):
        (OUT / f"{n}.json").write_text(json.dumps(v, indent=1))
    if live: (OUT / "live.json").write_text(json.dumps(live, indent=1))
    elif (OUT / "live.json").exists(): (OUT / "live.json").unlink()

    slug = lambda o: re.sub(r"[^a-z0-9]+", "-", o.lower()).strip("-")
    by_slug = {slug(o): o for o in owners}
    logos = {f"{f.parent.name}|{by_slug[f.stem]}": f"logos/{f.parent.name}/{f.name}"
             for f in sorted(LOGO_DIR.glob("*/*")) if f.stem in by_slug and f.parent.name.isdigit()}
    lc = OUT / "logo_colors.json"
    pal = ["#3b82f6", "#22c55e", "#f59e0b", "#a78bfa", "#ef4444", "#06b6d4", "#f97316", "#ec4899", "#84cc16", "#14b8a6", "#8b5cf6", "#eab308", "#e879f9", "#38bdf8"]
    owner_colors = {o: pal[i % len(pal)] for i, o in enumerate(cur_owners)}
    html = TEMPLATE.read_text()
    for k, v in (("__SEASONS__", seasons), ("__MATCHUPS__", matchups), ("__H2H__", h2h), ("__META__", meta),
                 ("__LIVE__", live), ("__LOGOS__", logos), ("__OWNER_COLORS__", owner_colors),
                 ("__LOGO_COLORS__", json.loads(lc.read_text()) if lc.exists() else {})):
        html = html.replace(k, json.dumps(v, separators=(",", ":")))
    html = html.replace("__UPDATED__", date.today().isoformat())
    (SITE / "index.html").write_text(html)
    (SITE / "data.json").write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                 "meta": meta, "seasons": seasons, "matchups": matchups, "h2h": h2h, "live": live}, separators=(",", ":")))
    print(f"{len(seasons)} season rows, {len(matchups)} matchup rows, {len(owners)} owners ({len(cur_owners)} current), live={'yes' if live else 'no'}")
    champs = [(s["season"], s["owner"], s["team_name"]) for s in seasons if s["championships"]]
    print("champions:", *[f"{y} {o}" for y, o, _ in champs], sep="\n  ")


def _season_over(d):
    """Season is over when the championship game (last WINNERS_BRACKET week) has been played."""
    wb = [s for s in d["schedule"] if s.get("playoffTierType") == "WINNERS_BRACKET" and "away" in s]
    if not wb: return False
    last = max(s["matchupPeriodId"] for s in wb)
    return all(s.get("winner") not in (None, "UNDECIDED") for s in wb if s["matchupPeriodId"] == last)


if __name__ == "__main__":
    main()
