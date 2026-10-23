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
2. **Odds key:** sign up free at the-odds-api.com and copy your API key. The free plan's
   500 credits/month is plenty: only alerts use credits (about 4 each), never the score checks.
3. **GitHub:** create a new **public** repo (public = free unlimited Actions minutes; your
   keys stay hidden as secrets) and upload these files, keeping the `.github/workflows` folder.
4. In the repo: **Settings → Secrets and variables → Actions → New repository secret**:
   - `ODDS_API_KEY` = your key
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
