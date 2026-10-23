#!/usr/bin/env python3
"""
NBA Halftime Comeback Alerts
----------------------------
Watches every live NBA game. The moment a game hits halftime with one team
trailing by MIN_DEFICIT or more, it pulls DraftKings' current lines (2nd-half
spread/total + live full-game spread/total) and pushes an alert to your phone.

Every alert is logged to data/alerts.csv and automatically graded after the
game ends, so you can test the "trailing team covers the 2H" theory.

Standard library only - no pip installs needed.

Usage:
    python nba_halftime_alerts.py            # run the watcher
    python nba_halftime_alerts.py --forever  # never exit (for an always-on server)
    python nba_halftime_alerts.py --test     # send a sample push to your phone
    python nba_halftime_alerts.py --report   # print the theory's record so far
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------- settings
ET = ZoneInfo("America/New_York")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ODDS_BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
DK_LINK = "https://sportsbook.draftkings.com/leagues/basketball/nba"

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

MIN_DEFICIT = int(os.getenv("MIN_DEFICIT", "10"))          # trailing by >= this at half
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))        # how often to check live scores
RUN_MINUTES = int(os.getenv("RUN_MINUTES", "345"))         # max runtime (GitHub caps jobs at 6h)
IDLE_EXIT_MINUTES = int(os.getenv("IDLE_EXIT_MINUTES", "120"))  # exit if next tip is further off
ODDS_RETRY_SECONDS = 60                                    # wait between retries if DK 2H line isn't up
ODDS_MAX_RETRIES = 4

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
ALERTS_CSV = os.path.join(DATA_DIR, "alerts.csv")

MARKETS_FULL = "spreads,totals,spreads_h2,totals_h2"
MARKETS_FALLBACK = "spreads,totals"

FIELDS = [
    "date_et", "game_id", "matchup", "trailing", "leading", "trail_half", "lead_half",
    "deficit", "trail_is_home", "late_alert", "alert_time_et",
    "pregame_line", "pregame_total",
    "dk_live_spread_trail", "dk_live_spread_price", "dk_live_total",
    "dk_2h_spread_trail", "dk_2h_spread_price", "dk_2h_total",
    "odds_status",
    "final_trail", "final_lead", "trail_2h_pts", "lead_2h_pts", "trail_2h_margin",
    "cover_2h_spread", "cover_live_spread", "trail_won", "graded",
]


def log(msg):
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET] {msg}", flush=True)


# ----------------------------------------------------------------- http
def http_json(url, params=None, data=None, headers=None, retries=2, timeout=15):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    body = json.dumps(data).encode() if data is not None else None
    hdrs = {"User-Agent": "nba-halftime-alerts/1.0"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
                return (json.loads(raw) if raw else {}), dict(r.headers)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last = e
        except Exception as e:  # network blip
            last = e
        time.sleep(2 * (attempt + 1))
    raise last


# ----------------------------------------------------------------- scores (ESPN)
def et_date(dt_utc):
    return dt_utc.astimezone(ET).strftime("%Y%m%d")


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_games(scoreboard):
    games = []
    for ev in scoreboard.get("events", []):
        comp = ev["competitions"][0]
        status = comp.get("status") or ev.get("status") or {}
        stype = status.get("type", {})
        sides = {}
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            sides[c.get("homeAway")] = {
                "name": t.get("displayName", ""),
                "short": t.get("shortDisplayName") or t.get("name") or t.get("displayName", ""),
                "abbr": t.get("abbreviation", ""),
                "score": int(float(c.get("score") or 0)),
                "lines": [float(x.get("value", 0)) for x in c.get("linescores", [])],
            }
        if "home" not in sides or "away" not in sides:
            continue
        odds = (comp.get("odds") or [{}])[0] or {}
        start = parse_iso(ev["date"])
        games.append({
            "id": str(ev["id"]),
            "start": start,
            "date_et": et_date(start),
            "state": stype.get("state"),          # pre / in / post
            "status": stype.get("name"),          # STATUS_HALFTIME, STATUS_FINAL, ...
            "completed": bool(stype.get("completed")),
            "period": int(status.get("period") or 0),
            "clock": status.get("displayClock", ""),
            "home": sides["home"],
            "away": sides["away"],
            "pregame_line": odds.get("details", ""),
            "pregame_total": odds.get("overUnder", ""),
        })
    return games


def fetch_games(date_str=None):
    params = {"dates": date_str} if date_str else None
    data, _ = http_json(ESPN_SCOREBOARD, params)
    return parse_games(data)


def fetch_current_games():
    now_et = datetime.now(ET)
    dates = [now_et.strftime("%Y%m%d")]
    if now_et.hour < 5:  # late west-coast games still belong to yesterday's slate
        dates.insert(0, (now_et - timedelta(days=1)).strftime("%Y%m%d"))
    games = {}
    for d in dates:
        for g in fetch_games(d):
            games[g["id"]] = g
    return list(games.values())


def halftime_check(g):
    """Return (halftime_scores, late_flag) or (None, None)."""
    if g["state"] != "in":
        return None, None
    home, away = g["home"], g["away"]
    # Only trust ESPN's official status: a 0.0 clock can still change on a review or free throws.
    at_half = g["status"] == "STATUS_HALFTIME" or (
        g["period"] == 2 and g["status"] == "STATUS_END_PERIOD"
    )
    if at_half:
        return (home["score"], away["score"]), False
    # Watcher started after halftime ended: still log it from the box score, flagged late.
    if g["period"] == 3 and len(home["lines"]) >= 2 and len(away["lines"]) >= 2:
        return (int(sum(home["lines"][:2])), int(sum(away["lines"][:2]))), True
    return None, None


# ----------------------------------------------------------------- odds (DraftKings via The Odds API)
def mascot(name):
    n = name.lower().replace("trail blazers", "blazers").strip()
    return n.split()[-1] if n else n


_events_cache = {"t": 0, "events": []}


def find_odds_event(game):
    if time.time() - _events_cache["t"] > 600:
        events, _ = http_json(f"{ODDS_BASE}/events", {"apiKey": ODDS_API_KEY})  # free endpoint
        _events_cache.update(t=time.time(), events=events)
    h, a = mascot(game["home"]["name"]), mascot(game["away"]["name"])
    for ev in _events_cache["events"]:
        if mascot(ev.get("home_team", "")) == h and mascot(ev.get("away_team", "")) == a:
            return ev["id"]
    return None


def fetch_dk_lines(game, trailing_name):
    """Returns dict of DraftKings lines for the trailing team. Never raises."""
    out = {"odds_status": ""}
    if not ODDS_API_KEY:
        out["odds_status"] = "no ODDS_API_KEY"
        return out
    try:
        event_id = find_odds_event(game)
        if not event_id:
            _events_cache["t"] = 0
            out["odds_status"] = "game not found on odds feed"
            return out
        params = {"apiKey": ODDS_API_KEY, "bookmakers": "draftkings",
                  "markets": MARKETS_FULL, "oddsFormat": "american"}
        try:
            data, headers = http_json(f"{ODDS_BASE}/events/{event_id}/odds", params)
        except urllib.error.HTTPError as e:
            if e.code != 422:
                raise
            params["markets"] = MARKETS_FALLBACK
            data, headers = http_json(f"{ODDS_BASE}/events/{event_id}/odds", params)
        remaining = headers.get("x-requests-remaining") or headers.get("X-Requests-Remaining")
        if remaining:
            log(f"Odds API credits remaining: {remaining}")
        books = [b for b in data.get("bookmakers", []) if b.get("key") == "draftkings"]
        if not books:
            out["odds_status"] = "DraftKings lines not posted"
            return out
        tmascot = mascot(trailing_name)
        for m in books[0].get("markets", []):
            key = m.get("key")
            for o in m.get("outcomes", []):
                if key in ("spreads", "spreads_h2") and mascot(o.get("name", "")) == tmascot:
                    pre = "dk_live" if key == "spreads" else "dk_2h"
                    out[f"{pre}_spread_trail"] = o.get("point")
                    out[f"{pre}_spread_price"] = o.get("price")
                elif key in ("totals", "totals_h2") and o.get("name") == "Over":
                    out["dk_live_total" if key == "totals" else "dk_2h_total"] = o.get("point")
        out["odds_status"] = "ok" if "dk_2h_spread_trail" in out else "2H line not posted yet"
    except Exception as e:
        out["odds_status"] = f"odds error: {e}"
    return out


# ----------------------------------------------------------------- notifications
def fmt_pts(p):
    if p is None or p == "":
        return "?"
    p = float(p)
    s = f"{p:+.1f}".replace(".0", "") if p != 0 else "PK"
    return s


def fmt_price(p):
    if p is None or p == "":
        return ""
    p = int(float(p))
    return f" ({p:+d})"


def build_message(row, trail_short, lead_short, late_clock=None):
    title = f"{trail_short} down {row['deficit']} at half vs {lead_short}"
    lines = []
    if late_clock:
        lines.append(f"Caught late ({late_clock} Q3) - lines may have moved")
    lines.append(f"{trail_short} {row['trail_half']} - {lead_short} {row['lead_half']} at half")
    if row.get("dk_2h_spread_trail") not in (None, ""):
        lines.append(f"DK 2H: {trail_short} {fmt_pts(row['dk_2h_spread_trail'])}"
                     f"{fmt_price(row.get('dk_2h_spread_price'))} | 2H total {row.get('dk_2h_total') or '?'}")
    if row.get("dk_live_spread_trail") not in (None, ""):
        lines.append(f"DK live: {trail_short} {fmt_pts(row['dk_live_spread_trail'])}"
                     f"{fmt_price(row.get('dk_live_spread_price'))} | total {row.get('dk_live_total') or '?'}")
    if row.get("odds_status") not in ("ok", ""):
        lines.append(f"DK: {row['odds_status']}")
    if row.get("pregame_line"):
        pg = f"Pregame: {row['pregame_line']}"
        if row.get("pregame_total"):
            pg += f" (O/U {row['pregame_total']})"
        lines.append(pg)
    return title, "\n".join(lines)


def notify(title, message, priority=4):
    sent = False
    if NTFY_TOPIC:
        try:
            http_json(NTFY_SERVER, data={"topic": NTFY_TOPIC, "title": title, "message": message,
                                         "priority": priority, "tags": ["basketball"],
                                         "click": DK_LINK})
            sent = True
        except Exception as e:
            log(f"ntfy failed: {e}")
    if TELEGRAM_TOKEN and TELEGRAM_CHAT:
        try:
            http_json(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT, "text": f"{title}\n{message}"})
            sent = True
        except Exception as e:
            log(f"telegram failed: {e}")
    if not sent:
        log("No notification channel worked/configured - printing instead")
    log(f"ALERT >> {title} | {message.replace(chr(10), ' | ')}")


# ----------------------------------------------------------------- csv log
def load_rows():
    if not os.path.exists(ALERTS_CSV):
        return []
    with open(ALERTS_CSV, newline="") as f:
        return list(csv.DictReader(f))


def save_rows(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ALERTS_CSV + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    os.replace(tmp, ALERTS_CSV)


def result(margin):
    return "W" if margin > 0 else ("L" if margin < 0 else "P")


def grade_rows(rows, games_by_id):
    changed = False
    for r in rows:
        if r.get("graded") == "1":
            continue
        g = games_by_id.get(r["game_id"])
        if not g or not g["completed"]:
            continue
        home_trails = r["trail_is_home"] == "1"
        ft = g["home"]["score"] if home_trails else g["away"]["score"]
        fl = g["away"]["score"] if home_trails else g["home"]["score"]
        t2, l2 = ft - int(r["trail_half"]), fl - int(r["lead_half"])
        r.update(final_trail=ft, final_lead=fl, trail_2h_pts=t2, lead_2h_pts=l2,
                 trail_2h_margin=t2 - l2, trail_won="Y" if ft > fl else "N", graded="1")
        if r.get("dk_2h_spread_trail") not in ("", None):
            r["cover_2h_spread"] = result((t2 - l2) + float(r["dk_2h_spread_trail"]))
        if r.get("dk_live_spread_trail") not in ("", None):
            r["cover_live_spread"] = result((ft - fl) + float(r["dk_live_spread_trail"]))
        log(f"Graded {r['matchup']}: {r['trailing']} 2H margin {t2 - l2:+d} -> "
            f"2H spread {r.get('cover_2h_spread') or 'n/a'}, live spread {r.get('cover_live_spread') or 'n/a'}")
        changed = True
    return changed


def grade_backlog(rows):
    """Grade alerts from earlier days whose games ended after the last run."""
    dates = sorted({r["date_et"] for r in rows if r.get("graded") != "1"})
    games = {}
    for d in dates:
        try:
            for g in fetch_games(d):
                games[g["id"]] = g
        except Exception as e:
            log(f"backlog fetch {d} failed: {e}")
    return grade_rows(rows, games)


# ----------------------------------------------------------------- core
def make_alert(g, half_scores, late):
    hs, as_ = half_scores
    home_trails = hs < as_
    trail = g["home"] if home_trails else g["away"]
    lead = g["away"] if home_trails else g["home"]
    th, lh = (hs, as_) if home_trails else (as_, hs)
    row = {
        "date_et": g["date_et"], "game_id": g["id"],
        "matchup": f"{g['away']['abbr']} @ {g['home']['abbr']}",
        "trailing": trail["name"], "leading": lead["name"],
        "trail_half": th, "lead_half": lh, "deficit": lh - th,
        "trail_is_home": "1" if home_trails else "0",
        "late_alert": "1" if late else "0",
        "alert_time_et": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "pregame_line": g["pregame_line"], "pregame_total": g["pregame_total"],
    }
    row.update(fetch_dk_lines(g, trail["name"]))
    title, msg = build_message(row, trail["short"], lead["short"], g["clock"] if late else None)
    return row, title, msg, trail["short"]


def run(forever=False):
    rows = load_rows()
    if grade_backlog(rows):
        save_rows(rows)
    alerted = {r["game_id"] for r in rows}
    pending = {}  # game_id -> {"tries": n, "next": ts, "game": g, "short": name}
    started = time.time()
    log(f"Watching NBA games: alert when a team trails by {MIN_DEFICIT}+ at halftime")

    while True:
        if not forever and time.time() - started > RUN_MINUTES * 60:
            log("Reached max runtime, exiting")
            break
        try:
            games = fetch_current_games()
        except Exception as e:
            log(f"scoreboard error: {e}")
            time.sleep(POLL_SECONDS)
            continue

        by_id = {g["id"]: g for g in games}
        if grade_rows(rows, by_id):
            save_rows(rows)

        # 1) new halftime triggers
        for g in games:
            if g["id"] in alerted:
                continue
            half, late = halftime_check(g)
            if not half:
                continue
            if abs(half[0] - half[1]) < MIN_DEFICIT:
                alerted.add(g["id"])  # checked at half, didn't qualify; never re-check
                continue
            row, title, msg, short = make_alert(g, half, late)
            notify(title, msg)
            rows.append(row)
            save_rows(rows)
            alerted.add(g["id"])
            if row["odds_status"] in ("2H line not posted yet", "DraftKings lines not posted") \
                    or row["odds_status"].startswith(("odds error", "game not found")):
                pending[g["id"]] = {"tries": 0, "next": time.time() + ODDS_RETRY_SECONDS,
                                    "game": g, "short": short}

        # 2) follow-up if DK's 2H line wasn't up yet at the first ping
        for gid, p in list(pending.items()):
            if time.time() < p["next"]:
                continue
            row = next(r for r in rows if r["game_id"] == gid)
            g = by_id.get(gid, p["game"])
            if g["period"] >= 3 and g["status"] != "STATUS_HALFTIME":
                pending.pop(gid)  # 2nd half started; stop chasing the line
                continue
            lines = fetch_dk_lines(g, row["trailing"])
            p["tries"] += 1
            if lines.get("dk_2h_spread_trail") is not None:
                row.update(lines)
                save_rows(rows)
                notify(f"DK 2H line up: {p['short']} {fmt_pts(lines['dk_2h_spread_trail'])}",
                       f"{p['short']} {fmt_pts(lines['dk_2h_spread_trail'])}"
                       f"{fmt_price(lines.get('dk_2h_spread_price'))} | 2H total "
                       f"{lines.get('dk_2h_total') or '?'}\nLive: {fmt_pts(lines.get('dk_live_spread_trail'))}"
                       f"{fmt_price(lines.get('dk_live_spread_price'))}", priority=3)
                pending.pop(gid)
            elif p["tries"] >= ODDS_MAX_RETRIES:
                pending.pop(gid)
            else:
                p["next"] = time.time() + ODDS_RETRY_SECONDS

        # 3) decide how long to sleep / whether we're done for the night
        live = [g for g in games if g["state"] == "in"]
        upcoming = sorted(g["start"] for g in games if g["state"] == "pre")
        if live or pending:
            time.sleep(POLL_SECONDS)
            continue
        now = datetime.now(timezone.utc)
        next_tip = upcoming[0] if upcoming else None
        if not forever and (next_tip is None or next_tip - now > timedelta(minutes=IDLE_EXIT_MINUTES)):
            log("No live games and nothing tipping soon, exiting")
            break
        if next_tip is None:
            wait = 1800
        else:
            wait = (next_tip - now).total_seconds() - 120
        wait = max(POLL_SECONDS, min(wait, 1800))
        log(f"No live games; next check in {int(wait // 60)} min")
        time.sleep(wait)

    save_rows(rows)


# ----------------------------------------------------------------- report
def roi_unit(price, res):
    if res == "W":
        p = float(price) if price not in ("", None) else -110.0
        return p / 100 if p > 0 else 100 / abs(p)
    return -1.0 if res == "L" else 0.0


def report():
    rows = [r for r in load_rows() if r.get("graded") == "1"]
    print(f"\nGraded alerts: {len(rows)}  (ungraded/live: {len(load_rows()) - len(rows)})")
    if not rows:
        print("Nothing graded yet - check back after some games finish.\n")
        return

    def summarize(label, subset, col, price_col):
        bets = [r for r in subset if r.get(col) in ("W", "L", "P")]
        if not bets:
            return
        w = sum(r[col] == "W" for r in bets)
        l = sum(r[col] == "L" for r in bets)
        p = sum(r[col] == "P" for r in bets)
        units = sum(roi_unit(r.get(price_col), r[col]) for r in bets)
        pct = 100 * w / (w + l) if w + l else 0
        print(f"  {label:<22} {w}-{l}-{p}  cover {pct:5.1f}%  units {units:+.2f}  ROI {100 * units / len(bets):+.1f}%")

    buckets = [("All", lambda r: True),
               ("Down 10-14", lambda r: 10 <= int(r["deficit"]) <= 14),
               ("Down 15-19", lambda r: 15 <= int(r["deficit"]) <= 19),
               ("Down 20+", lambda r: int(r["deficit"]) >= 20),
               ("Trailing team home", lambda r: r["trail_is_home"] == "1"),
               ("Trailing team away", lambda r: r["trail_is_home"] == "0")]
    print("\nTrailing team, DraftKings 2nd-half spread (break-even 52.4% at -110):")
    for label, f in buckets:
        summarize(label, [r for r in rows if f(r)], "cover_2h_spread", "dk_2h_spread_price")
    print("\nTrailing team, DraftKings live full-game spread at halftime:")
    for label, f in buckets:
        summarize(label, [r for r in rows if f(r)], "cover_live_spread", "dk_live_spread_price")
    margins = [int(r["trail_2h_margin"]) for r in rows]
    won = sum(r["trail_won"] == "Y" for r in rows)
    print(f"\nAvg 2H margin for trailing team: {sum(margins) / len(margins):+.1f} pts | "
          f"full comebacks (won game): {won}/{len(rows)}\n")


# ----------------------------------------------------------------- main
if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--report" in args:
        report()
    elif "--test" in args:
        notify("TEST: Knicks down 14 at half vs Celtics",
               "Knicks 48 - Celtics 62 at half\nDK 2H: Knicks +6.5 (-110) | 2H total 112.5\n"
               "DK live: Knicks +10.5 (-112) | total 221.5\nPregame: BOS -3.5 (O/U 224.5)")
        if not (NTFY_TOPIC or (TELEGRAM_TOKEN and TELEGRAM_CHAT)):
            print("Set NTFY_TOPIC first, e.g.  NTFY_TOPIC=chance-nba-alerts-8x2k python nba_halftime_alerts.py --test")
    else:
        run(forever="--forever" in args)
