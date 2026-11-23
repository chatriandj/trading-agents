#!/usr/bin/env bash
# Commits the bot's log and dashboard and pushes them, even if another run pushed first.
set -u
git config user.name "trading-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add logs docs/index.html 2>/dev/null || true
if git diff --cached --quiet; then
  echo "Nothing new to save."
  exit 0
fi
git commit -q -m "Bot log $(date -u '+%Y-%m-%d %H:%M') UTC"
for attempt in 1 2 3 4 5; do
  # Logs merge line-by-line (.gitattributes); for the dashboard this run's newer copy wins.
  if git pull -q --rebase -X theirs origin main && git push -q origin HEAD:main; then
    echo "Saved log and dashboard (attempt $attempt)."
    exit 0
  fi
  git rebase --abort 2>/dev/null || true
  echo "Save attempt $attempt failed, retrying..."
  sleep $((attempt * 5))
done
echo "Could not save the log after 5 attempts."
exit 1
