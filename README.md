# NBA Halftime Comeback Alerts

Pushes an alert to your phone the moment an NBA game hits halftime with a team down **10+**,
with DraftKings' current lines for the trailing team:

```
Knicks down 14 at half vs Celtics
Knicks 48 - Celtics 62 at half
DK 2H: Knicks +6.5 (-110) | 2H total 112.5
DK live: Knicks +10.5 (-112) | total 221.5
Pregame: BOS -3.5 (O/U 224.5)
```

Tapping the alert opens the DraftKings NBA page. If DK hasn't posted the 2H line yet at the
buzzer, you get the alert anyway, then a second push as soon as the line appears.

Every alert is saved to `data/alerts.csv` and graded automatically after the game
(2H spread result, live-spread result, whether the trailing team won).

## Setup (about 10 minutes, all doable from your iPhone)

1. **Phone alerts:** install the free **ntfy** app, tap +, and subscribe to a topic name only
   you would guess (e.g. `chance-nba-ht-7k2q`). Anyone who knows the name can read it.
2. **Odds key:** sign up free at the-odds-api.com with an email you don't already use there, and copy
   the API key. Use a key of its own, not Perseverance's: only alerts use credits (2 each: the
   DraftKings 2nd-half spread and total), never the score checks.
3. **GitHub:** create a new **public** repo (public = free unlimited Actions minutes; your
   keys stay hidden as secrets) and upload these files, keeping the `.github/workflows` folder.
4. In the repo: **Settings → Secrets and variables → Actions → New repository secret**:
   - `ODDS_API_KEY` = this bot's own key (since Nov 17 it is the old free key Perseverance used to share;
     Perseverance now has its own paid key in its own repo). `ODDS_API_KEY_2H`, if added, takes priority.
   - `NTFY_TOPIC` = your topic name
5. **Actions tab → NBA halftime alerts → Run workflow** to start it now. After that it starts
   itself every day (afternoon slates and the evening slate) and shuts down when games end.

## Checking the theory

The **Actions** run log prints the running record at the end of each night. It's broken out
by deficit size (10-14, 15-19, 20+) and home/away, with cover %, units and ROI at DK's actual
prices. Break-even at -110 is 52.4%. Give it a few hundred alerts before trusting a number.

## Settings (edit in the workflow file)

| Setting | Default | What it does |
|---|---|---|
| `MIN_DEFICIT` | 10 | Trailing by this much or more at the half |
| `POLL_SECONDS` | 20 | How often live scores are checked |

## Notes

- Scores come from ESPN's public scoreboard; DraftKings odds from The Odds API.
- "Caught late" alerts happen only if the bot started after a halftime ended; those lines
  have moved, so the report's numbers lean on the on-time alerts.
- DraftKings 2nd-half bets include overtime; grading uses the final score, so it matches.
- Optional Telegram: add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets.
- Run locally instead: `NTFY_TOPIC=... ODDS_API_KEY=... python3 nba_halftime_alerts.py --forever`
  (`--test` sends a sample push, `--report` prints the record.)

## Credits (Nov 17, 2026)
Until Nov 17 this bot used Perseverance's Odds API key and spent about half of that key's November credits
(4 per halftime lookup, more when it retried for a 2nd-half line), which Perseverance's budget could not see.
Now each lookup buys only the 2nd-half spread and total (2 credits; the full-game live line is no longer
recorded, so `cover_live_spread` stays blank for new rows), it reads `ODDS_API_KEY_2H` first, and on the shared
key it stopped at 60 credits.

Later on Nov 17 Perseverance moved to its own paid key, and the owner gave this bot the old free key's
remaining balance. The bot now treats `ODDS_API_KEY` as its own (floor 4 credits; set `SHARED_KEY=1` if another
app ever shares it again). Perseverance also refuses to spend from any key with a free-size plan, so it can't
touch this balance even if its secret is still the old key.
