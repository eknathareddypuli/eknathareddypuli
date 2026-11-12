# Setup (one time, 3 minutes)

This repo must be named exactly `eknatha` (same as the username) and be public.

1. Copy everything from this folder into the repo root and push to `main`.
2. Repo → **Settings → Actions → General → Workflow permissions** → choose **Read and write permissions** → Save.
3. Repo → **Actions** tab → **Refresh live profile assets** → **Run workflow**.
   (The push also triggers it; a manual run is the quickest check.)
4. Open github.com/eknatha. The terminal now says `live · synced <date>`, and the pods show your real year.

## What refreshes, and how
| Asset | Source | Fallback |
|---|---|---|
| `terminal-*.svg` STATUS | HTTP probe of each EknathaLabs URL: 2xx/3xx = Running, 404 = Pending, 5xx = Error | unreachable → keeps last known status |
| `terminal-*.svg` AGE / RESTARTS | GitHub API: repo age; commits in last 30 days | `-` |
| `pods-*.svg` | GraphQL contribution calendar (workflow token), then public calendar page | last saved year in `data/state.json` |

Runs every 6 hours. The file only changes (and commits) when a status, count or the synced date changes, so expect about one bot commit per day.

## Edit things
- Add or rename a tool: edit `config.json` (name, repo, url, purpose).
- Change colors or layout: edit `scripts/generate.py`, then `python scripts/generate.py --out assets`.
- Preview without network: add `--offline`.

## Notes
- Private-repo contributions are not visible to the workflow token, so the pods show public activity. To include private work, add a personal access token (read:user scope) as a repo secret named `GH_TOKEN` and change the workflow's `GITHUB_TOKEN:` line to `${{ secrets.GH_TOKEN }}`.
- GitHub pauses scheduled workflows after 60 days without repo activity; bot commits usually keep it alive, but if it stops, press **Run workflow** once.
- Animations pause for visitors with "reduce motion" enabled.
