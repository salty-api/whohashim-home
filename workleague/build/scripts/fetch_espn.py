#!/usr/bin/env python3
"""
Pull every season of ESPN league 591271 -> data/raw/{year}.json (trimmed: members, teams, schedule, settings).
Cookies come from env only (ESPN_S2, SWID); they are never written to disk.
Finished seasons are cached; the current season is always refetched. Use --all to refetch everything.
"""
import json, os, sys, time
from pathlib import Path
from urllib.parse import unquote
import requests

LEAGUE = 591271
from datetime import date
_t = date.today()
FIRST, LAST = 2011, int(os.environ.get("WL_YEAR", _t.year if _t.month >= 8 else _t.year - 1))  # NFL season year
RAW = Path(__file__).parent.parent / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
if not (os.environ.get("ESPN_S2") and os.environ.get("SWID")):
    sys.exit("ESPN_S2 and SWID env vars are required")
COOKIES = {"espn_s2": unquote(os.environ["ESPN_S2"]), "SWID": os.environ["SWID"]}
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"


def fetch(year):
    if year >= LAST:
        url = f"{BASE}/seasons/{year}/segments/0/leagues/{LEAGUE}"
        params = {"view": ["mTeam", "mSettings", "mMatchupScore", "mStatus"]}
    else:
        url = f"{BASE}/leagueHistory/{LEAGUE}"
        params = {"seasonId": year, "view": ["mTeam", "mSettings", "mMatchupScore", "mStatus"]}
    r = requests.get(url, params=params, cookies=COOKIES, timeout=60)
    if r.status_code in (401, 403):
        sys.exit(f"{year}: ESPN says {r.status_code} - cookies expired or not authorized")
    r.raise_for_status()
    d = r.json()
    return d[0] if isinstance(d, list) else d


def trim(d):
    ss = d["settings"]
    return {
        "seasonId": d["seasonId"],
        "settings": {"name": ss.get("name"), "scheduleSettings": ss.get("scheduleSettings"),
                     "scoringType": ss.get("scoringSettings", {}).get("scoringType")},
        "status": {k: d["status"].get(k) for k in ("currentMatchupPeriod", "finalScoringPeriod", "isActive", "latestScoringPeriod")},
        "members": [{k: m.get(k) for k in ("id", "firstName", "lastName", "displayName")} for m in d.get("members", [])],
        "teams": [{"id": t["id"], "name": t.get("name") or f"{t.get('location','')} {t.get('nickname','')}".strip(),
                   "abbrev": t.get("abbrev"), "logo": t.get("logo"), "owners": t.get("owners"), "primaryOwner": t.get("primaryOwner"),
                   "playoffSeed": t.get("playoffSeed"), "rankCalculatedFinal": t.get("rankCalculatedFinal"),
                   "record": t.get("record"), "points": t.get("points")} for t in d["teams"]],
        "schedule": [{"matchupPeriodId": s["matchupPeriodId"], "playoffTierType": s.get("playoffTierType"),
                      "winner": s.get("winner"),
                      "home": {"teamId": s["home"]["teamId"], "totalPoints": s["home"].get("totalPoints"),
                               "totalPointsLive": s["home"].get("totalPointsLive")},
                      **({"away": {"teamId": s["away"]["teamId"], "totalPoints": s["away"].get("totalPoints"),
                                   "totalPointsLive": s["away"].get("totalPointsLive")}} if "away" in s else {})}
                     for s in d.get("schedule", [])],
    }


def main():
    force = "--all" in sys.argv
    for y in range(FIRST, LAST + 1):
        f = RAW / f"{y}.json"
        if f.exists() and y < LAST and not force:
            print(f"{y}: cached"); continue
        t = trim(fetch(y))
        f.write_text(json.dumps(t, separators=(",", ":")))
        print(f"{y}: {len(t['teams'])} teams, {len(t['schedule'])} games")
        time.sleep(0.4)


if __name__ == "__main__":
    main()
