#!/usr/bin/env python3
"""Fetch Sleeper league data and build sleeper_report.html (chopped league)."""
import json, statistics as st, sys, urllib.request, html
from pathlib import Path

LEAGUE = sys.argv[1] if len(sys.argv) > 1 else "1389695218609061888"
ROOT = Path(__file__).parent
OUT = ROOT / "index.html"
API = "https://api.sleeper.app/v1"
MY_OWNER = "TeamDebnar"  # Sleeper display_name of our team


def getu(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sleeper-report)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(path):
    return getu(f"{API}/{path}")


league = get(f"league/{LEAGUE}")
rosters = get(f"league/{LEAGUE}/rosters")
users = {u["user_id"]: u for u in get(f"league/{LEAGUE}/users")}
state = get("state/nfl")
cur = state["leg"] if state["season_type"] == "regular" else 18
pf = ROOT / "data" / "players.json"
if pf.exists():
    players = json.load(open(pf))
else:
    players = get("players/nfl")
    pf.parent.mkdir(exist_ok=True)
    json.dump(players, open(pf, "w"))

weeks = {}
for w in range(1, cur + 1):
    weeks[w] = get(f"league/{LEAGUE}/matchups/{w}")

# last completed week = last_scored_leg
done = league["settings"].get("last_scored_leg", cur - 1)
live = cur if cur > done else None

teams = {}
for r in rosters:
    u = users.get(r["owner_id"], {})
    name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Team {r['roster_id']}"
    teams[r["roster_id"]] = dict(
        id=r["roster_id"], name=name, owner=u.get("display_name", "?"),
        elim=r["settings"].get("eliminated", 0), scores={}, draft=r["settings"].get("draft_position"),
        faab=r["settings"].get("waiver_budget_used", 0),
    )

pts = {}  # (week, roster) -> points
player_tot = {}  # pid -> [pts, roster]
for w in range(1, done + 1):
    for m in weeks[w]:
        pts[(w, m["roster_id"])] = m["points"]
        for pid, p in (m.get("players_points") or {}).items():
            if pid in (m.get("starters") or []) and teams[m["roster_id"]]:
                player_tot.setdefault((w, pid, m["roster_id"]), p)

# Only count a team's scores through its elimination week (it scores 0 afterwards)
for t in teams.values():
    last = t["elim"] or done
    t["scores"] = {w: pts[(w, t["id"])] for w in range(1, min(last, done) + 1) if (w, t["id"]) in pts}
    s = list(t["scores"].values())
    t["total"] = sum(s)
    t["avg"] = st.mean(s) if s else 0
    t["sd"] = st.pstdev(s) if len(s) > 1 else 0
    t["best"] = max(t["scores"].items(), key=lambda x: x[1]) if s else (0, 0)
    t["worst"] = min(t["scores"].items(), key=lambda x: x[1]) if s else (0, 0)

# weekly alive pools, rank within week, margin above the chop line
week_info = {}
for w in range(1, done + 1):
    alive = [t for t in teams.values() if not t["elim"] or t["elim"] >= w]
    ranked = sorted(alive, key=lambda t: t["scores"].get(w, 0))
    chopped = ranked[0]
    week_info[w] = dict(
        alive=len(alive), chopped=chopped, low=chopped["scores"].get(w, 0),
        second=ranked[1]["scores"].get(w, 0),
        high=ranked[-1], hi=ranked[-1]["scores"].get(w, 0),
        avg=st.mean(t["scores"].get(w, 0) for t in alive),
        rank={t["id"]: len(alive) - i for i, t in enumerate(ranked)},
    )
for t in teams.values():
    t["ranks"] = {w: week_info[w]["rank"][t["id"]] for w in t["scores"]}
    # margin over the weekly chop line (lowest score) for each week survived
    t["margins"] = {w: t["scores"][w] - week_info[w]["low"] for w in t["scores"] if t["elim"] != w}
    t["closest"] = min(t["margins"].items(), key=lambda x: x[1]) if t["margins"] else None
    t["avg_rank"] = st.mean(t["ranks"].values()) if t["ranks"] else 0

alive_teams = sorted([t for t in teams.values() if not t["elim"]], key=lambda t: -t["total"])
elim_teams = sorted([t for t in teams.values() if t["elim"]], key=lambda t: -t["elim"])


def pname(pid):
    p = players.get(pid, {})
    return (p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip() or pid) if p else pid


def ppos(pid):
    return (players.get(pid) or {}).get("position", "")


# top individual starter performances
top_players = []
for w in range(1, done + 1):
    for m in weeks[w]:
        for pid in m.get("starters") or []:
            v = (m.get("players_points") or {}).get(pid)
            if v is not None:
                top_players.append((v, w, pid, m["roster_id"]))
top_players.sort(reverse=True)

# live week
live_rows = []
if live:
    live_rows = sorted(
        [(m["points"], teams[m["roster_id"]]) for m in weeks[live] if not teams[m["roster_id"]]["elim"]],
        key=lambda x: -x[0])

all_scores = [s for t in teams.values() for s in t["scores"].values()]
e = html.escape


def f1(x):
    return f"{x:,.1f}"



# ---------- our team analysis ----------
ME = next(t for t in teams.values() if t["owner"].lower() == MY_OWNER.lower())
POS = ["QB", "RB", "WR", "TE"]


def optimal(m):
    pp = m.get("players_points") or {}
    by = {p: sorted([pp[x] for x in pp if ppos(x) == p], reverse=True) for p in POS}
    tot = by["QB"][:1] + by["RB"][:2] + by["WR"][:2] + by["TE"][:1]
    flex = sorted(by["RB"][2:] + by["WR"][2:] + by["TE"][1:], reverse=True)[:2]
    return sum(tot) + sum(flex)


pos_wk = {}  # (roster, week) -> {pos: pts}
opt_gap = {}  # (roster, week) -> optimal - actual
for w in range(1, done + 1):
    for m in weeks[w]:
        rid = m["roster_id"]
        t = teams[rid]
        if t["elim"] and t["elim"] < w:
            continue
        d = {p: 0.0 for p in POS}
        for pid, v in zip(m["starters"], m["starters_points"]):
            if ppos(pid) in d:
                d[ppos(pid)] += v
        pos_wk[(rid, w)] = d
        opt_gap[(rid, w)] = optimal(m) - m["points"]


def pos_avg(rid, p):
    v = [d[p] for (r, w), d in pos_wk.items() if r == rid]
    return st.mean(v) if v else 0


pos_tbl = {}
for p in POS:
    allv = {rid: pos_avg(rid, p) for rid in teams}
    ranked = sorted(allv, key=lambda r: -allv[r])
    pos_tbl[p] = dict(mine=allv[ME["id"]], lg=st.mean(allv.values()), rank=ranked.index(ME["id"]) + 1)

opt_by = {rid: st.mean(v) for rid in teams if (v := [g for (r, w), g in opt_gap.items() if r == rid])}
opt_rank = sorted(opt_by, key=lambda r: opt_by[r]).index(ME["id"]) + 1  # 1 = fewest points left on bench
my_gap = [(w, opt_gap[(ME["id"], w)]) for w in range(1, done + 1)]
my_rows_rank = sorted(teams.values(), key=lambda t: -t["total"]).index(ME) + 1
alive_rank = ([t["id"] for t in alive_teams].index(ME["id"]) + 1) if not ME["elim"] else None
faab_left = 1000 - ME["faab"]
faab_med_left = st.median(1000 - t["faab"] for t in teams.values() if not t["elim"])

# current roster contributions (starter points for us, season to date)
roster7 = next(r for r in rosters if r["roster_id"] == ME["id"])
contrib = {}
for w in range(1, done + 1):
    m = next(x for x in weeks[w] if x["roster_id"] == ME["id"])
    for pid, v in (m.get("players_points") or {}).items():
        c = contrib.setdefault(pid, dict(start=0.0, bench=0.0, gp=0, recent=[]))
        if pid in m["starters"]:
            c["start"] += v
            c["gp"] += 1
        else:
            c["bench"] += v
        c["recent"].append((w, v))


def player_row(pid):
    c = contrib.get(pid, dict(start=0, bench=0, gp=0, recent=[]))
    rec = [v for w, v in c["recent"] if w >= done - 2]
    r3 = st.mean(rec) if rec else 0
    avg = (c["start"] + c["bench"]) / max(len(c["recent"]), 1)
    inj = (players.get(pid) or {}).get("injury_status") or ""
    st_tag = "Starter" if pid in roster7["starters"] else "Bench"
    return (c["start"] + c["bench"], pname(pid), ppos(pid), avg, r3, st_tag, inj)


rost = sorted([player_row(p) for p in roster7["players"]], key=lambda x: -x[0])
rost_rows = "".join(
    f'<tr><td class="l"><b>{e(n)}</b></td><td>{pos}</td><td>{stt}</td><td class="n">{tot:.0f}</td><td class="n">{a:.1f}</td>'
    f'<td class="n">{r3:.1f}</td><td>{e(inj)}</td></tr>' for tot, n, pos, a, r3, stt, inj in rost)

strengths, opps = [], []
for p, d in sorted(pos_tbl.items(), key=lambda x: x[1]["rank"]):
    line = f'{p}: {d["mine"]:.1f} pts/wk from starters vs {d["lg"]:.1f} league avg (#{d["rank"]} of 18)'
    if d["rank"] <= 6:
        strengths.append(line)
    elif d["rank"] >= 13:
        opps.append(line)
best_p = min(pos_tbl, key=lambda p: pos_tbl[p]["rank"])
worst_p = max(pos_tbl, key=lambda p: pos_tbl[p]["rank"])
if not strengths:
    strengths.append(f'Relative best position group: {best_p} (#{pos_tbl[best_p]["rank"]} of 18, {pos_tbl[best_p]["mine"]:.1f} pts/wk)')
if not opps:
    opps.append(f'Relative weakest position group: {worst_p} (#{pos_tbl[worst_p]["rank"]} of 18, {pos_tbl[worst_p]["mine"]:.1f} pts/wk)')

mrg = ME["margins"]
safe_w = sum(1 for v in mrg.values() if v > 15)
strengths.append(f'Has survived all {len(mrg)} chops, clearing the chop line by an average of {st.mean(mrg.values()):.1f} pts '
                 f'(league-wide survivors: {st.mean(v for t in alive_teams for v in t["margins"].values()):.1f}).')
if ME["sd"] < st.mean(t["sd"] for t in alive_teams):
    strengths.append(f'Consistency: week-to-week std dev {ME["sd"]:.1f} vs survivor avg {st.mean(t["sd"] for t in alive_teams):.1f}.')
big_w = max(ME["scores"].items(), key=lambda x: x[1])
strengths.append(f'Ceiling: {big_w[1]:.1f} in W{big_w[0]} (league high that week: {week_info[big_w[0]]["hi"]:.1f}).')
if faab_left > faab_med_left:
    strengths.append(f'FAAB flexibility: {faab_left} of 1,000 left vs survivor median {faab_med_left:.0f}.')

if ME["avg"] < st.mean(t["avg"] for t in alive_teams):
    opps.append(f'Scoring: {ME["avg"]:.1f} pts/wk is below the survivor average of {st.mean(t["avg"] for t in alive_teams):.1f} '
                f'(#{alive_rank} of {len(alive_teams)} survivors by total).')
low_w = [(w, v) for w, v in mrg.items() if v < 15]
if low_w:
    opps.append("Thin margins: " + ", ".join(f"W{w} (+{v:.1f})" for w, v in sorted(low_w, key=lambda x: x[1])[:4]) + " above the chop line. "
                "Any of these weeks was within one bad starter of elimination.")
mg = st.mean(g for w, g in my_gap)
opps.append(f'Lineup efficiency: an optimal lineup would have added {mg:.1f} pts/wk (#{opt_rank} fewest left on the bench among 18 teams; 1 = best). '
            f'Biggest miss: W{max(my_gap, key=lambda x: x[1])[0]} (+{max(g for w, g in my_gap):.1f}).')
dead = [r for r in rost if r[5] == "Starter" and r[4] < 8 and r[2] != "QB"]
if dead:
    opps.append("Starters trending cold (last 3 wks avg below 8): " + ", ".join(f"{n} ({r3:.1f})" for _, n, _, _, r3, _, _ in dead) + ".")
hot_b = [r for r in rost if r[5] == "Bench" and r[4] >= 10]
if hot_b:
    opps.append("Bench players running hot (last 3 wks avg 10+): " + ", ".join(f"{n} ({r3:.1f})" for _, n, _, _, r3, _, _ in hot_b) + ".")
recent_lines = [week_info[w]["low"] for w in range(max(1, done - 2), done + 1)]
opps.append(f'Chop line over the last 3 weeks averaged {st.mean(recent_lines):.1f}; our weekly floor so far is {ME["worst"][1]:.1f} (W{ME["worst"][0]}), '
            f'and our last-3 average is {st.mean(ME["scores"][w] for w in range(max(1, done - 2), done + 1)):.1f}.')

pos_rows = "".join(
    f'<tr><td>{p}</td><td class="n">{d["mine"]:.1f}</td><td class="n">{d["lg"]:.1f}</td>'
    f'<td class="n" style="color:{"#7fe0a0" if d["mine"]>=d["lg"] else "#ff8a8a"}">{d["mine"]-d["lg"]:+.1f}</td><td class="n">#{d["rank"]}</td></tr>'
    for p, d in pos_tbl.items())
me_html = f"""<h2 id="me">Team Debnar: strengths and opportunities</h2>
<div class="kpis">
<div class="kpi me"><div class="v">#{my_rows_rank}</div><div class="k">Total points rank (of 18)</div></div>
<div class="kpi me"><div class="v">{ME['total']:,.0f}</div><div class="k">Total points</div></div>
<div class="kpi me"><div class="v">{ME['avg']:.1f}</div><div class="k">Avg per week</div></div>
<div class="kpi me"><div class="v">{ME['avg_rank']:.1f}</div><div class="k">Avg weekly rank (1 = best)</div></div>
<div class="kpi me"><div class="v">{faab_left}</div><div class="k">FAAB left</div></div></div>
<div class="two"><div class="card good"><h3>Strengths</h3><ul>{''.join(f'<li>{e(x)}</li>' for x in strengths)}</ul></div>
<div class="card bad"><h3>Opportunities</h3><ul>{''.join(f'<li>{e(x)}</li>' for x in opps)}</ul></div></div>
<h3>Starter points by position (per week)</h3>
<table><tr><th>Position</th><th>Debnar</th><th>League avg</th><th>Diff</th><th>Rank</th></tr>{pos_rows}</table>
<h3>Current roster</h3><p class="sub">Points are season totals on our roster (starter + bench). Recent = last 3 weeks avg.</p>
<table><tr><th class="l">Player</th><th>Pos</th><th>Slot</th><th>Pts</th><th>Avg</th><th>Recent</th><th>Inj</th></tr>{rost_rows}</table>"""


# ---------- projections + free agents ----------
SC = league["scoring_settings"]
POSQ = "&".join(f"position[]={p}" for p in POS)


def lg_pts(stats):
    return sum(stats.get(k, 0) * v for k, v in SC.items() if k in stats and k in (
        "pass_int", "pass_2pt", "rec_td", "rush_td", "rec_2pt", "rec", "fum_lost", "rush_2pt", "pass_yd", "pass_td", "rush_yd", "fum", "rec_yd"))


proj = {}
proj_opp = {}
try:
    for d in getu(f"https://api.sleeper.com/projections/nfl/{league['season']}/{cur}?season_type=regular&{POSQ}"):
        if d.get("player_id") in players and d["stats"]:
            proj[d["player_id"]] = lg_pts(d["stats"]) if d["stats"].get("gp", 1) else 0
            proj_opp[d["player_id"]] = d.get("opponent") or ""
except Exception as ex:
    print("projections unavailable:", ex)

recent = {}  # pid -> [pts per week] for last 3 completed weeks
for w in range(max(1, done - 2), done + 1):
    try:
        for d in getu(f"https://api.sleeper.com/stats/nfl/{league['season']}/{w}?season_type=regular"):
            if d["stats"].get("gms_active", 0) or d["stats"].get("rec", 0) or d["stats"].get("rush_att", 0) or d["stats"].get("pass_att", 0):
                recent.setdefault(d["player_id"], {})[w] = lg_pts(d["stats"])
    except Exception as ex:
        print("stats unavailable", w, ex)


def r3avg(pid):
    v = list(recent.get(pid, {}).values())
    return st.mean(v) if v else 0.0


rostered = {p for r in rosters for p in (r["players"] or [])} | {p for r in rosters for p in (r.get("reserve") or [])}
fa = []
for pid, pj in proj.items():
    if pid in rostered:
        continue
    pl = players.get(pid, {})
    if pl.get("position") not in POS or pl.get("status") not in ("Active", "Injured Reserve", "Inactive", None, "Injured"):
        pass
    fa.append(dict(pid=pid, name=pname(pid), pos=pl.get("position"), team=pl.get("team") or "FA", opp=proj_opp.get(pid, ""),
                   proj=pj, r3=r3avg(pid), inj=pl.get("injury_status") or ""))
fa = [f for f in fa if f["team"] != "FA" and f["inj"] not in ("Out", "IR", "Sus", "PUP")]

# projected optimal lineup per team
def proj_lineup(pids):
    by = {p: sorted([proj.get(x, 0) for x in pids if ppos(x) == p], reverse=True) for p in POS}
    core = by["QB"][:1] + by["RB"][:2] + by["WR"][:2] + by["TE"][:1]
    flex = sorted(by["RB"][2:] + by["WR"][2:] + by["TE"][1:], reverse=True)[:2]
    return sum(core) + sum(flex)


proj_rows_data = []
for t in teams.values():
    if t["elim"]:
        continue
    r = next(x for x in rosters if x["roster_id"] == t["id"])
    live_pts = next((m["points"] for m in weeks.get(live, []) if m["roster_id"] == t["id"]), 0) if live else 0
    proj_rows_data.append((proj_lineup(r["players"]), t, live_pts))
proj_rows_data.sort(key=lambda x: -x[0])
proj_tbl = "".join(
    f'<tr{" class=me" if t is ME else ""}><td>{i+1}</td><td class="l">{e(t["name"])}</td><td class="n">{pj:.1f}</td><td class="n">{lp:.1f}</td>'
    f'<td class="n">{pj-proj_rows_data[-1][0]:+.1f}</td></tr>' for i, (pj, t, lp) in enumerate(proj_rows_data))
my_proj_rank = next(i for i, (pj, t, lp) in enumerate(proj_rows_data) if t is ME) + 1


def proj_bars():
    n = len(proj_rows_data)
    W, RH, L, R, T = 920, 26, 170, 60, 6
    H = T + n * RH + 6
    mx = max(max(pj for pj, _, _ in proj_rows_data), max(lp for _, _, lp in proj_rows_data), 1) * 1.05
    sx = lambda v: v * (W - L - R) / mx
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart">']
    for i, (pj, t, lp) in enumerate(proj_rows_data):
        yy = T + i * RH
        me = t is ME
        col = "#e50914" if me else "#777"
        o.append(f'<text x="{L-8}" y="{yy+RH/2+4:.1f}" fill="{"#fff" if me else "#bbb"}" font-size="12" font-weight="{700 if me else 400}" text-anchor="end">{e(t["name"][:24])}</text>')
        o.append(f'<rect x="{L}" y="{yy+3}" width="{sx(pj):.1f}" height="{RH-8}" fill="{col}" opacity="{1 if me else 0.7}"><title>{e(t["name"])}: proj {pj:.1f}, live {lp:.1f}</title></rect>')
        if lp > 0:
            o.append(f'<rect x="{L}" y="{yy+8}" width="{sx(lp):.1f}" height="{RH-18}" fill="#fff" opacity="0.85"/>')
        o.append(f'<text x="{L+sx(pj)+6:.1f}" y="{yy+RH/2+4:.1f}" fill="#ddd" font-size="11">{pj:.1f}</text>')
    o.append("</svg>")
    return "".join(o)

# free agents by position + upgrade check vs our roster
my_pids = roster7["players"]
fa_html = ""
upg = []
for p in POS:
    top = sorted([f for f in fa if f["pos"] == p], key=lambda f: -(f["proj"] + f["r3"] * 0.0))[:8]
    mine = sorted([(proj.get(x, 0), pname(x)) for x in my_pids if ppos(x) == p])
    floor = mine[0] if mine else (0, "-")
    best_fa = top[0] if top else None
    rows = "".join(
        f'<tr><td class="l"><b>{e(f["name"])}</b></td><td>{f["team"]}</td><td>{e(f["opp"])}</td><td class="n">{f["proj"]:.1f}</td>'
        f'<td class="n">{f["r3"]:.1f}</td><td>{e(f["inj"])}</td></tr>' for f in top)
    fa_html += (f'<div class="card"><h3>{p}</h3><table><tr><th class="l">Player</th><th>Team</th><th>Opp</th><th>Proj</th><th>L3 avg</th><th>Inj</th></tr>{rows}</table></div>')
    base = proj_lineup(my_pids)
    for f in top[:5]:
        g = proj_lineup(my_pids + [f["pid"]]) - base
        if g >= 1.0:
            upg.append((g, f, floor[1]))
upg = sorted(upg, key=lambda x: -x[0])[:6]
upg_rows = "".join(
    f'<tr><td class="l"><b>{e(f["name"])}</b> <small>{f["pos"]} {f["team"]}</small></td><td class="n">{f["proj"]:.1f}</td><td class="n">{f["r3"]:.1f}</td>'
    f'<td class="l">{e(d)}</td><td class="n" style="color:#7fe0a0">+{g:.1f}</td></tr>' for g, f, d in upg) or '<tr><td colspan=5 class="dim">No free agent projects meaningfully above our bench.</td></tr>'

my_roster_proj = sorted([(proj.get(x, 0), pname(x), ppos(x), (players.get(x) or {}).get("injury_status") or "", x in roster7["starters"]) for x in my_pids], reverse=True)
my_proj_rows = "".join(
    f'<tr><td class="l"><b>{e(n)}</b></td><td>{pos}</td><td class="n">{pj:.1f}</td><td>{e(inj)}</td><td>{"Starter" if stt else "Bench"}</td></tr>' for pj, n, pos, inj, stt in my_roster_proj)


def roster_bars():
    n = len(my_roster_proj)
    W, RH, L, R, T = 920, 24, 190, 60, 6
    H = T + n * RH + 6
    mx = max(max(r[0] for r in my_roster_proj), 1) * 1.05
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart">']
    for i, (pj, nm, pos, inj, stt) in enumerate(my_roster_proj):
        yy = T + i * RH
        w = pj * (W - L - R) / mx
        col = "#e50914" if stt else "#777"
        lab = f"{nm} ({pos})" + (f" {inj}" if inj else "")
        o.append(f'<text x="{L-8}" y="{yy+RH/2+4:.1f}" fill="{"#fff" if stt else "#bbb"}" font-size="12" font-weight="{700 if stt else 400}" text-anchor="end">{e(lab[:28])}</text>')
        o.append(f'<rect x="{L}" y="{yy+3}" width="{max(w, 0):.1f}" height="{RH-7}" fill="{col}" opacity="{1 if stt else 0.7}"><title>{e(nm)}: proj {pj:.1f} ({"Starter" if stt else "Bench"})</title></rect>')
        o.append(f'<text x="{L+max(w, 0)+6:.1f}" y="{yy+RH/2+4:.1f}" fill="#ddd" font-size="11">{pj:.1f}</text>')
    o.append("</svg>")
    return "".join(o)

set_pts = sum(proj.get(x, 0) for x in roster7["starters"])
lineup_tip = (f"Set lineup projects {set_pts:.1f}; best projected lineup from our roster is {proj_lineup(my_pids):.1f} "
              f"({proj_lineup(my_pids)-set_pts:+.1f}). Check bench players projected above starters." )
fa_section = f"""<h2 id="fa">Week {cur} projections and free agents</h2>
<p class="sub">Projections: Sleeper/RotoWire stat projections rescored with our league rules (PPR, 4 pt pass TD, 0.04/pass yd). Team totals use each roster's best projected lineup (QB, 2 RB, 2 WR, TE, 2 FLEX), not necessarily the lineup set in Sleeper. L3 avg = actual league points over the last 3 weeks. Injured Out/IR players are hidden.</p>
<h3>Projected scores, surviving teams</h3>
<p class="sub">Team Debnar projects #{my_proj_rank} of {len(proj_rows_data)}. The lowest projected team is the most likely chop; "vs low" is the gap to that team.</p>
<p class="sub">Bars = projected score; white inner bar = live points so far. Team Debnar in red.</p>{proj_bars()}
<table><tr><th>#</th><th class="l">Team</th><th>Proj</th><th>Live pts</th><th>vs low</th></tr>{proj_tbl}</table>
<h3>Top free agents by position</h3><div class="two">{fa_html}</div>
<h3>Suggested pickups for Team Debnar</h3><p class="sub">Gain = change in our best projected Week {cur} lineup if we add the player (QB, 2 RB, 2 WR, TE, 2 FLEX). Check FAAB (we have {faab_left} left) and roster space before dropping.</p>
<table><tr><th class="l">Add</th><th>Proj</th><th>L3 avg</th><th class="l">Worst at pos</th><th>Gain</th></tr>{upg_rows}</table>
<h3>Our roster, Week {cur} projections</h3>
<p class="sub">{lineup_tip}</p>
<p class="sub">Bars = projected points. Red = currently in the starting lineup, gray = bench.</p>{roster_bars()}
<table><tr><th class="l">Player</th><th>Pos</th><th>Proj</th><th>Inj</th><th>Slot</th></tr>{my_proj_rows}</table>"""

# ---------- charts ----------
PAL = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46a2c4", "#c4a000", "#f032e6", "#6b8e23",
       "#008080", "#9a6324", "#800000", "#808000", "#000075", "#e377c2", "#7f7f7f", "#17becf", "#bcbd22"]


def line_chart():
    W, H, L, R, T, B = 920, 340, 46, 120, 14, 30
    lo = min(all_scores) // 20 * 20
    hi = max(all_scores) // 20 * 20 + 20
    x = lambda w: L + (w - 1) * (W - L - R) / max(done - 1, 1)
    y = lambda v: T + (hi - v) * (H - T - B) / (hi - lo)
    s = [f'<svg viewBox="0 0 {W} {H}" class="chart">']
    for v in range(int(lo), int(hi) + 1, 20):
        s.append(f'<line x1="{L}" x2="{W-R}" y1="{y(v)}" y2="{y(v)}" stroke="#2a2a2a"/>'
                 f'<text x="{L-6}" y="{y(v)+4}" fill="#888" font-size="11" text-anchor="end">{v}</text>')
    for w in range(1, done + 1):
        s.append(f'<text x="{x(w)}" y="{H-8}" fill="#888" font-size="11" text-anchor="middle">W{w}</text>')
    ordered = sorted(teams.values(), key=lambda t: t is ME)  # draw our team last, on top
    for t in ordered:
        for w in sorted(t["scores"]):
            v = t["scores"][w]
            if t is ME:
                s.append(f'<circle cx="{x(w):.1f}" cy="{y(v):.1f}" r="6" fill="#e50914" stroke="#fff" stroke-width="1.5"><title>{e(t["name"])} W{w}: {v:.1f}</title></circle>')
            else:
                s.append(f'<circle cx="{x(w):.1f}" cy="{y(v):.1f}" r="3.5" fill="#777" opacity="0.6"><title>{e(t["name"])} W{w}: {v:.1f}</title></circle>')
    if ME["scores"]:
        lw = max(ME["scores"])
        s.append(f'<text x="{x(lw)+12:.1f}" y="{y(ME["scores"][lw])+4:.1f}" fill="#e50914" font-size="12" font-weight="700">{e(ME["name"][:20])}</text>')
    s.append("</svg>")
    return "".join(s)


def heat(v, lo, hi):
    if v is None:
        return ""
    p = (v - lo) / (hi - lo) if hi > lo else 0.5
    # red (low) -> neutral -> green (high)
    if p < 0.5:
        a = (0.5 - p) * 1.4
        return f"background:rgba(229,9,20,{a:.2f})"
    a = (p - 0.5) * 1.2
    return f"background:rgba(70,200,110,{a:.2f})"


lo_s, hi_s = min(all_scores), max(all_scores)
hm_rows = []
order = sorted(teams.values(), key=lambda t: (-(t["elim"] == 0), -(t["elim"] or 0) if t["elim"] else 0, -t["total"]))
order = alive_teams + elim_teams
for t in order:
    cells = []
    for w in range(1, done + 1):
        v = t["scores"].get(w)
        if v is None:
            cells.append('<td class="dim">-</td>')
        else:
            cls = " chop" if t["elim"] == w else ""
            cells.append(f'<td class="n{cls}" style="{heat(v, lo_s, hi_s)}">{v:.0f}</td>')
    if live:
        lv = next((m["points"] for m in weeks[live] if m["roster_id"] == t["id"]), 0)
        cells.append(f'<td class="n dim">{"" if t["elim"] else f"{lv:.0f}"}</td>')
    status = f'<span class="tag out">Chopped W{t["elim"]}</span>' if t["elim"] else '<span class="tag in">Alive</span>'
    hm_rows.append(f'<tr{" class=me" if t is ME else ""}><td class="l"><b>{e(t["name"])}</b><br><small>{e(t["owner"])}</small></td><td>{status}</td>'
                   + "".join(cells) + f'<td class="n"><b>{t["total"]:,.0f}</b></td><td class="n">{t["avg"]:.1f}</td></tr>')

hm_head = "".join(f"<th>W{w}</th>" for w in range(1, done + 1)) + (f'<th class="dim">W{live}*</th>' if live else "")

chop_rows = "".join(
    f'<tr><td>W{w}</td><td class="l"><b>{e(i["chopped"]["name"])}</b></td><td class="n">{i["low"]:.1f}</td>'
    f'<td class="n">{i["second"]:.1f}</td><td class="n">{i["second"]-i["low"]:.1f}</td>'
    f'<td class="l">{e(i["high"]["name"])}</td><td class="n">{i["hi"]:.1f}</td><td class="n">{i["avg"]:.1f}</td>'
    f'<td class="n">{i["alive"]}</td></tr>' for w, i in week_info.items())

# survivors: consistency / danger
surv_rows = []
for t in alive_teams:
    cl = t["closest"]
    surv_rows.append(
        f'<tr{" class=me" if t is ME else ""}><td class="l"><b>{e(t["name"])}</b></td><td class="n">{t["total"]:,.0f}</td><td class="n">{t["avg"]:.1f}</td>'
        f'<td class="n">{t["sd"]:.1f}</td><td class="n">{t["avg_rank"]:.1f}</td>'
        f'<td class="n">W{t["best"][0]} ({t["best"][1]:.0f})</td><td class="n">W{t["worst"][0]} ({t["worst"][1]:.0f})</td>'
        f'<td class="n">{"W%d: +%.1f" % (cl[0], cl[1]) if cl else "-"}</td><td class="n">{t["faab"]}</td></tr>')

tp_rows = "".join(
    f'<tr><td class="l"><b>{e(pname(pid))}</b> <small>{ppos(pid)}</small></td><td class="l">{e(teams[r]["name"])}</td>'
    f'<td class="n">W{w}</td><td class="n">{v:.1f}</td></tr>' for v, w, pid, r in top_players[:12])

# ---------- transactions (moves) ----------
import datetime
txs = []
for w in range(1, cur + 1):
    try:
        txs += [dict(t, wk=w) for t in get(f"league/{LEAGUE}/transactions/{w}")]
    except Exception as ex:
        print("transactions unavailable", w, ex)
txs = [t for t in txs if t.get("status") == "complete"]
txs.sort(key=lambda t: -(t.get("status_updated") or t.get("created") or 0))


def tx_players(d):
    return ", ".join(f"{e(pname(p))} <small>{ppos(p)}</small>" for p in (d or {}))


def tx_row(t):
    ts = datetime.datetime.fromtimestamp((t.get("status_updated") or t["created"]) / 1000).strftime("%b %d %H:%M")
    rids = t.get("roster_ids") or []
    who = ", ".join(e(teams[r]["name"]) for r in rids if r in teams)
    bid = (t.get("settings") or {}).get("waiver_bid")
    typ = t["type"]
    bid_s = f"${bid}" if t["type"] == "waiver" and bid is not None else ""
    adds, drops = tx_players(t.get("adds")), tx_players(t.get("drops"))
    if t["type"] == "trade":
        adds = "; ".join(f'{e(teams[r]["name"])} gets ' + ", ".join(e(pname(p)) for p, rr in (t.get("adds") or {}).items() if rr == r)
                         for r in rids if r in teams)
        drops = f'{len(t.get("draft_picks") or [])} pick(s) moved' if t.get("draft_picks") else ""
    me = ME["id"] in rids
    return (f'<tr{" class=me" if me else ""}><td>W{t["wk"]}</td><td>{ts}</td><td class="l"><b>{who}</b></td><td>{typ}</td><td class="n">{bid_s}</td>'
            f'<td class="l" style="color:#7fe0a0">{adds}</td><td class="l" style="color:#ff8a8a">{drops}</td></tr>')


tx_html = (f'<h2 id="moves">Moves (latest {min(len(txs), 20)} of {len(txs)})</h2><p class="sub">Completed adds, drops and trades, newest first. Waiver bids in FAAB dollars. Team Debnar highlighted.</p>'
           '<table><tr><th>Wk</th><th>When</th><th class="l">Team</th><th>Type</th><th>Bid</th><th class="l">Added</th><th class="l">Dropped</th></tr>'
           + "".join(tx_row(t) for t in txs[:20]) + "</table>")

live_html = ""
if live:
    n = len(live_rows)
    live_html = (f'<h2>Week {live} (in progress)</h2><p class="sub">{n} teams alive; the lowest score at week end is chopped.</p>'
                 '<table><tr><th class="l">Team</th><th>Pts</th></tr>'
                 + "".join(f'<tr{" class=me" if t is ME else ""}><td class="l">{e(t["name"])}</td><td class="n">{p:.1f}</td></tr>' for p, t in live_rows)
                 + "</table>")

# headline numbers
hi_w = max(week_info.items(), key=lambda x: x[1]["hi"])
tight = min(week_info.items(), key=lambda x: x[1]["second"] - x[1]["low"])
big = max(((w, t, s) for t in teams.values() for w, s in t["scores"].items()), key=lambda x: x[2])
upset = [(w, i["chopped"]) for w, i in week_info.items() if i["chopped"]["total"] / max(len(i["chopped"]["scores"]), 1) > st.mean(all_scores)]
best_avg = max(teams.values(), key=lambda t: t["avg"])
unlucky = [(w, i["chopped"]) for w, i in week_info.items()
           if i["low"] > st.mean(all_scores) - 0.0 and True]
chopped_hi = max(week_info.items(), key=lambda x: x[1]["low"])

kpis = [
    ("Teams alive", f'{len(alive_teams)} / {len(teams)}'),
    ("League avg score", f1(st.mean(all_scores))),
    ("Highest week", f'{big[2]:.1f}<small> {e(big[1]["name"])} W{big[0]}</small>'),
    ("Closest chop", f'{tight[1]["second"]-tight[1]["low"]:.1f} pts<small> W{tight[0]}</small>'),
    ("Highest score chopped", f'{chopped_hi[1]["low"]:.1f}<small> {e(chopped_hi[1]["chopped"]["name"])} W{chopped_hi[0]}</small>'),
    ("Best avg", f'{best_avg["avg"]:.1f}<small> {e(best_avg["name"])}</small>'),
]
kpi_html = "".join(f'<div class="kpi"><div class="v">{v}</div><div class="k">{k}</div></div>' for k, v in kpis)

# narrative bullets
bul = []
lead = alive_teams[0]
bul.append(f'{e(lead["name"])} leads the survivors with {lead["total"]:,.0f} total points ({lead["avg"]:.1f} per week).')
c8 = week_info[done]["chopped"]
bul.append(f'Most recent chop (W{done}): {e(c8["name"])} with {week_info[done]["low"]:.1f}, '
           f'{week_info[done]["second"]-week_info[done]["low"]:.1f} behind the next lowest. '
           f'They ranked #{sorted(teams.values(), key=lambda t:-t["total"]).index(c8)+1} of 18 in total points.')
bul.append(f'Chop line (lowest score) has ranged from {min(i["low"] for i in week_info.values()):.1f} '
           f'to {max(i["low"] for i in week_info.values()):.1f}.')
ties = [w for w, i in week_info.items() if i["second"] == i["low"]]
if ties:
    bul.append("Tie for the chop in " + ", ".join(f"W{w}" for w in ties) + " (identical lowest scores, broken by Sleeper's tiebreaker).")
dang = sorted([t for t in alive_teams if t["closest"]], key=lambda t: t["closest"][1])[:3]
bul.append("Closest survivor brushes with the chop: " + "; ".join(
    f'{e(t["name"])} (+{t["closest"][1]:.1f} in W{t["closest"][0]})' for t in dang) + ".")
volatile = max(alive_teams, key=lambda t: t["sd"])
steady = min(alive_teams, key=lambda t: t["sd"])
bul.append(f'Most volatile survivor: {e(volatile["name"])} (std dev {volatile["sd"]:.1f}); steadiest: {e(steady["name"])} ({steady["sd"]:.1f}).')
bul_html = "".join(f"<li>{b}</li>" for b in bul)

# ---------- This Week tab: projected score + lineups per team ----------
SLOTS = [s for s in league["roster_positions"] if s != "BN"]
# team -> game status this week (complete / in_game / pre_game); no game = bye
gstat = {}
try:
    for g in getu(f"https://api.sleeper.com/schedule/nfl/regular/{league['season']}"):
        if g.get("week") == cur:
            gstat[g["home"]] = gstat[g["away"]] = g["status"]
except Exception as ex:
    print("schedule unavailable:", ex)


def blend(pid, live):
    """Final game: actual. In progress: max(live, proj). Not started: proj. Bye: 0."""
    stt = gstat.get((players.get(pid) or {}).get("team"))
    pj = proj.get(pid, 0)
    live = live or 0
    if stt == "complete":
        return live, "F"
    if stt == "in_game":
        return max(live, pj), "L"
    if stt == "pre_game":
        return pj, "P"
    return (live, "F") if not gstat else (live, "BYE")


wk_cards = []
wk_bar = []
for m in weeks.get(cur, []):
    t = teams[m["roster_id"]]
    if t["elim"]:
        continue
    r = next(x for x in rosters if x["roster_id"] == t["id"])
    starters = m.get("starters") or r.get("starters") or []
    spts = m.get("starters_points") or [0] * len(starters)
    ppl = m.get("players_points") or {}
    set_proj = sum(proj.get(x, 0) for x in starters if x != "0")
    est = sum(blend(x, lp)[0] for x, lp in zip(starters, spts) if x != "0")
    best_proj = proj_lineup(r["players"])
    live_pts = m.get("points") or 0
    rows = []
    for slot, pid, lp in zip(SLOTS, starters, spts):
        if pid == "0":
            rows.append(f'<tr><td class="slot">{slot}</td><td class="l dim">EMPTY</td><td></td><td></td><td></td></tr>')
            continue
        pl = players.get(pid) or {}
        inj = pl.get("injury_status") or ""
        flag = f' <span class="tag out">{e(inj)}</span>' if inj else ""
        bv, bs = blend(pid, lp)
        rows.append(
            f'<tr><td class="slot">{slot}</td><td class="l">{e(pname(pid))}{flag} <small>{e(pl.get("team") or "FA")} {e(proj_opp.get(pid, ""))}</small></td>'
            f'<td class="n">{proj.get(pid, 0):.1f}</td><td class="n">{lp or 0:.1f}</td><td class="n st{bs}"><b>{bv:.1f}</b> <small>{bs}</small></td></tr>')
    bench = [x for x in (r["players"] or []) if x not in starters and x != "0"]
    bn = sorted(bench, key=lambda x: -proj.get(x, 0))
    worst_start = min((proj.get(x, 0) for x in starters if x != "0"), default=0)
    bn_html = ", ".join(
        f'<span class="{"up" if proj.get(x, 0) > worst_start and ppos(x) in POS else "dim"}">{e(pname(x))} {proj.get(x, 0):.1f}</span>'
        for x in bn[:8])
    gap = best_proj - set_proj
    wk_bar.append((est, live_pts, t))
    wk_cards.append((est, f'''<div class="wcard{" me" if t is ME else ""}">
<div class="whead"><b>{e(t["name"])}</b><span class="n big">{est:.1f}<small> est</small></span></div>
<div class="sub">{e(t["owner"])} | pure proj {set_proj:.1f} | live {live_pts:.1f} | best possible lineup {best_proj:.1f} ({gap:+.1f})</div>
<table><tr><th>Slot</th><th class="l">Player</th><th>Proj</th><th>Live</th><th>Est</th></tr>{"".join(rows)}</table>
<div class="bn"><small>Bench (green = projects above a starter):</small> {bn_html}</div></div>'''))
wk_cards.sort(key=lambda x: -x[0])
def wk_chart():
    d = sorted(wk_bar, key=lambda x: -x[0])
    W, RH, L, R, T = 920, 28, 190, 70, 22
    H = T + len(d) * RH + 8
    mx = max(x[0] for x in d) * 1.05
    sx = lambda v: v * (W - L - R) / mx
    low = d[-1][0]
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart">']
    for i, (est, lv, t) in enumerate(d):
        y = T + i * RH
        me = t is ME
        done_w = sx(min(lv, est))
        rem_w = sx(max(est - lv, 0))
        o.append(f'<text x="{L-8}" y="{y+RH/2+4:.1f}" fill="{"#fff" if me else "#bbb"}" font-size="12" font-weight="{700 if me else 400}" text-anchor="end">{e(t["name"][:26])}</text>')
        o.append(f'<rect x="{L}" y="{y+4}" width="{done_w:.1f}" height="{RH-8}" fill="#e50914"><title>{e(t["name"])}: {lv:.1f} accumulated</title></rect>')
        o.append(f'<rect x="{L+done_w:.1f}" y="{y+4}" width="{rem_w:.1f}" height="{RH-8}" fill="#e50914" opacity="0.3"><title>{e(t["name"])}: {est-lv:.1f} still projected</title></rect>')
        o.append(f'<text x="{L+done_w+rem_w+6:.1f}" y="{y+RH/2+4:.1f}" fill="#ddd" font-size="11">{est:.1f} <tspan fill="#888">({lv:.1f} in)</tspan></text>')
    lx = L + sx(low)
    o.append(f'<line x1="{lx:.1f}" x2="{lx:.1f}" y1="{T-4}" y2="{H-4}" stroke="#f5c542" stroke-dasharray="4 3"/>')
    o.append(f'<text x="{lx:.1f}" y="12" fill="#f5c542" font-size="11" text-anchor="middle">current chop line {low:.1f}</text>')
    o.append("</svg>")
    return "".join(o)


week_html = (f'<h2>Week {cur} lineups</h2><p class="sub">Est = blended score for the lineup set in Sleeper: F = game final, use actual points; L = game in progress, use the higher of live points and projection; P = not started, use projection. '
             f'Sorted by Est; the lowest is the likeliest chop. Projections: Sleeper/RotoWire rescored with league rules. Team Debnar highlighted.</p>'
             f'<p class="sub">Solid bar = points accumulated so far; faded extension = remaining projected points (end of bar = Est). Dashed line = lowest Est.</p>{wk_chart()}'
             f'<div class="wgrid" style="margin-top:16px">{"".join(c for _, c in wk_cards)}</div>')

page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(league['name'])} - League Report</title>
<style>
body{{background:#141414;color:#e5e5e5;font-family:'Helvetica Neue',Arial,sans-serif;margin:0;padding:32px;max-width:1100px;margin:auto}}
h1{{font-size:28px;margin:0 0 4px}} h2{{font-size:18px;margin:34px 0 8px;border-bottom:1px solid #333;padding-bottom:6px}}
.sub{{color:#999;font-size:13px;margin:0 0 10px}} .kpis{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}
.kpi{{background:#1f1f1f;border-radius:6px;padding:12px 14px;flex:1;min-width:140px}} .kpi .v{{font-size:22px;font-weight:700}}
.kpi small{{font-size:11px;color:#999;font-weight:400}} .kpi .k{{font-size:11px;color:#999;text-transform:uppercase;margin-top:2px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th{{color:#999;font-weight:600;padding:6px 8px;text-align:center;border-bottom:1px solid #333;font-size:11px;text-transform:uppercase}}
td{{padding:6px 8px;border-bottom:1px solid #222;text-align:center}} .l{{text-align:left}} th.l{{text-align:left}} .n{{font-variant-numeric:tabular-nums}} small{{color:#888}}
.dim{{color:#666}} .chop{{outline:2px solid #e50914;outline-offset:-2px;font-weight:700}}
.tag{{font-size:10px;padding:2px 6px;border-radius:3px;white-space:nowrap}} .tag.in{{background:#1d4d2e;color:#7fe0a0}} .tag.out{{background:#4d1d1d;color:#ff8a8a}}
.chart{{width:100%;background:#1a1a1a;border-radius:6px}} ul{{line-height:1.7;font-size:14px}}
.wrap{{overflow-x:auto}} tr.me td{{background:rgba(229,9,20,.18)!important;font-weight:600;box-shadow:inset 0 1px 0 #e50914,inset 0 -1px 0 #e50914}}
.kpi.me{{border-left:3px solid #e50914}} .two{{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}} .card{{flex:1;min-width:420px;background:#1f1f1f;border-radius:6px;padding:4px 16px}}
.card.good{{border-top:3px solid #46c86e}} .card.bad{{border-top:3px solid #e50914}} h3{{font-size:14px;margin:16px 0 6px}} .card h3{{font-size:15px}} .card li{{font-size:13px}}
.tabs{{display:flex;gap:6px;margin:16px 0;border-bottom:1px solid #333}} .tabs button{{background:none;border:0;color:#999;font-size:14px;font-weight:600;padding:10px 16px;cursor:pointer;border-bottom:3px solid transparent}}
.tabs button.on{{color:#fff;border-bottom-color:#e50914}} .pane{{display:none}} .pane.on{{display:block}}
.wgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}} .wcard{{background:#1f1f1f;border-radius:6px;padding:12px 14px}} .wcard.me{{border-left:3px solid #e50914}}
.whead{{display:flex;justify-content:space-between;align-items:baseline}} .whead .big{{font-size:20px;font-weight:700}} .slot{{color:#888;font-size:11px;width:40px}}
.wcard table{{font-size:12px}} .wcard td{{padding:4px 6px}} .bn{{font-size:11px;margin-top:8px;line-height:1.6}} .up{{color:#7fe0a0}} .stF b{{color:#fff}} .stL b{{color:#f5c542}} .stP b{{color:#8ab4ff}} .stBYE b{{color:#777}}
body{{max-width:1400px}}
</style></head><body>
<div class="tabs"><button class="on" data-t="week">This Week</button><button data-t="league">League</button></div>
<div class="pane on" id="pane-week">{week_html}</div>
<div class="pane" id="pane-league">
<h1>{e(league['name'])}</h1>
<p class="sub">{league['season']} season | Sleeper chopped league | {len(teams)} teams | through Week {done}{f' (Week {live} in progress)' if live else ''} | lowest score each week is eliminated</p>
<div class="kpis">{kpi_html}</div>
<div class="kpis"><div class="kpi me" style="flex:2"><div class="v">{e(ME['name'])}: {'ALIVE' if not ME['elim'] else 'CHOPPED W'+str(ME['elim'])}</div><div class="k">Our team | #{my_rows_rank} in total points | {ME['total']:,.0f} pts | <a href="#me" style="color:#ff8a8a">jump to analysis</a></div></div></div>
<h2>Highlights</h2><ul>{bul_html}</ul>
<h2>Weekly scores</h2><p class="sub">Red outline marks the chop week. Colors scale across all weekly scores. * = in progress.</p>
<div class="wrap"><table><tr><th class="l">Team</th><th>Status</th>{hm_head}<th>Total</th><th>Avg</th></tr>{''.join(hm_rows)}</table></div>
<h2>Score trend</h2>{line_chart()}
<h2>Weekly chop results</h2>
<table><tr><th>Wk</th><th class="l">Chopped</th><th>Score</th><th>Next lowest</th><th>Gap</th><th class="l">High scorer</th><th>High</th><th>Avg</th><th>Alive</th></tr>{chop_rows}</table>
<h2>Survivor profile</h2><p class="sub">Std dev = week-to-week volatility. Avg rank is within the weekly pool of live teams (1 = best). Closest call is the smallest margin above the chop line. FAAB used of 1,000.</p>
<table><tr><th class="l">Team</th><th>Total</th><th>Avg</th><th>Std dev</th><th>Avg rank</th><th>Best week</th><th>Worst week</th><th>Closest call</th><th>FAAB</th></tr>{''.join(surv_rows)}</table>
<h2>Top starter performances</h2>
<table><tr><th class="l">Player</th><th class="l">Team</th><th>Week</th><th>Pts</th></tr>{tp_rows}</table>
{me_html}
{fa_section}
{tx_html}
{live_html}
</div>
<script>document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.tabs button,.pane').forEach(x=>x.classList.remove('on'));b.classList.add('on');document.getElementById('pane-'+b.dataset.t).classList.add('on');}});</script>
</body></html>"""
OUT.write_text(page)
print("wrote", OUT)
