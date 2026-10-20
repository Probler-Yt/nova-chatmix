#!/usr/bin/env bash
# How many people are using nova-chatmix: release downloads (every AUR install
# fetches the release tarball), AUR votes/popularity, and GitHub traffic.
set -euo pipefail
REPO="${REPO:-Probler-Yt/nova-chatmix}"
NAME=nova-chatmix

echo "Release downloads (each AUR install counts once):"
gh api "repos/$REPO/releases" --jq '.[] | "  \(.tag_name): \([.assets[].download_count] | add // 0)"'
gh api "repos/$REPO/releases" --jq '"  total: \([.[].assets[].download_count] | add // 0)"'

echo "AUR:"
curl -fsS "https://aur.archlinux.org/rpc/v5/info?arg[]=$NAME" | python3 -c "$(cat <<'PY'
import json, sys
r = json.load(sys.stdin)["results"]
if not r:
    print("  not published yet")
else:
    print(f"  votes: {r[0]['NumVotes']}   popularity: {r[0]['Popularity']:.2f}")
PY
)"

echo "GitHub (last 14 days):"
gh api "repos/$REPO/traffic/views" --jq '"  page views: \(.count) (\(.uniques) unique)"'
gh api "repos/$REPO/traffic/clones" --jq '"  clones:     \(.count) (\(.uniques) unique)"'
gh api "repos/$REPO" --jq '"  stars: \(.stargazers_count)   forks: \(.forks_count)   watchers: \(.subscribers_count)"'
