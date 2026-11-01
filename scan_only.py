#!/usr/bin/env python3
"""Scan-only version: find bounties without AI calls."""
import subprocess, json, sys, os
from datetime import datetime, timedelta, timezone

repos_file = sys.argv[1] if len(sys.argv) > 1 else "repos.txt"
output_file = sys.argv[2] if len(sys.argv) > 2 else "bounty_scan.json"

BOUNTY_LABELS = ["bounty", "help wanted", "good first issue"]

with open(repos_file) as f:
    repos = [l.strip() for l in f if l.strip() and not l.startswith("#")]

print(f"Scanning {len(repos)} repos...")
all_issues = []
for repo in repos:
    for label in BOUNTY_LABELS:
        try:
            result = subprocess.run(
                ["gh", "issue", "list", "--repo", repo, "--label", label,
                 "--state", "all", "--limit", "10",
                 "--json", "number,title,body,labels,url,createdAt"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                issues = json.loads(result.stdout)
                for issue_data in issues:
                    all_issues.append({
                        "repo": repo,
                        "number": issue_data["number"],
                        "title": issue_data["title"],
                        "labels": [l["name"] for l in issue_data.get("labels", [])],
                        "url": issue_data.get("url", ""),
                        "body": (issue_data.get("body") or "")[:500],
                    })
        except Exception as e:
            print(f"  ⚠ Error scanning {repo}/{label}: {e}")

# Deduplicate
seen = set()
unique = []
for b in all_issues:
    key = (b["repo"], b["number"])
    if key not in seen:
        seen.add(key)
        unique.append(b)

with open(output_file, "w") as f:
    json.dump(unique, f, indent=2)

print(f"\nFound {len(unique)} total bounty issues:")
for b in unique:
    print(f"  #{b['number']} ({b['repo']}): {b['title'][:60]}")

print(f"\nResults saved to {output_file}")
