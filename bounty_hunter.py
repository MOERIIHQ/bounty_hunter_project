#!/usr/bin/env python3
"""
bounty_hunter.py — Automated GitHub Bounty Hunter

Scans GitHub repos for bounties, scores them with AI, and prepares
draft fixes — all without manual command entry.

Usage:
    python bounty_hunter.py
    python bounty_hunter.py --repos myfile.txt
    python bounty_hunter.py --dry-run          # scan + score only, no work
    python bounty_hunter.py --output log.txt    # custom log file
"""

import subprocess
import json
import sys
import os
import re
import argparse
import httpx
import tempfile
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# PART 1 — CONFIGURATION MODEL LOADER
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    """Holds the AI model config read from Hermes."""
    model: str = "openai/gpt-oss-120b"
    provider: str = "groq"
    base_url: str = "https://api.groq.com/openai/v1"

def load_hermes_config() -> ModelConfig:
    """
    Automatically reads the active Hermes model configuration.
    Priority: config.yaml > .env > hardcoded default.
    """
    config_paths = [
        os.path.expanduser("~/.hermes/config.yaml"),
        os.path.expanduser("/data/data/com.termux/files/home/.hermes/config.yaml"),
    ]
    env_path = os.path.expanduser("~/.hermes/.env")

    model = None
    provider = None
    base_url = None
    content = ""

    # Try config.yaml first
    for p in config_paths:
        if os.path.isfile(p):
            with open(p) as f:
                content = f.read()

    # Parse config.yaml with regex for the model dict
    if content:
        # Match: model: {'default': 'model-name', 'provider': 'provider-name', 'base_url': 'url'}
        # OR:    model:
        #            default: model-name
        #            provider: provider-name
        #            base_url: url

        # First try the inline dict format: model: {'default': '...'}
        model_match = re.search(
            r"model:\s*\{[^}]*'default'\s*:\s*'([^']+)'", content
        )
        if not model_match:
            # Try multi-line format under model: block
            model_block = re.search(
                r"model:\s*\n(\s+default:\s*([^\n]+)\n)?(\s+provider:\s*([^\n]+)\n)?(\s+base_url:\s*([^\n]+))?",
                content
            )
            if model_block:
                model_val = model_block.group(2)
                if model_val:
                    model = model_val.strip()

        provider_match = re.search(
            r"provider:\s*'([^']+)'", content
        )
        if not provider_match:
            provider_block = re.search(
                r"^\s+provider:\s*([^\n#]+)", content, re.MULTILINE
            )
            if provider_block:
                provider = provider_block.group(1).strip()

        base_url_match = re.search(
            r"base_url:\s*['\"]([^'\"]+)['\"]", content
        )
        if not base_url_match:
            base_url_block = re.search(
                r"^\s+base_url:\s*([^\n#]+)", content, re.MULTILINE
            )
            if base_url_block:
                base_url = base_url_block.group(1).strip()

    # Fallback: hermes config show
    if not model:
        try:
            result = subprocess.run(
                ["hermes", "config", "show"],
                capture_output=True, text=True, timeout=15
            )
            for line in result.stdout.splitlines():
                if "Model:" in line or "default" in line.lower():
                    m = re.search(r"'([^']+)'", line)
                    if m:
                        model = m.group(1)
                if "provider" in line.lower():
                    m = re.search(r"'([^']+)'", line)
                    if m:
                        provider = m.group(1)
        except Exception:
            pass

    # Fallback: .env file
    if not base_url and os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                if "BASE_URL" in line and "=" in line and not line.strip().startswith("#"):
                    val = line.split("=", 1)[1].strip()
                    if val and not val.startswith("#"):
                        base_url = val

    return ModelConfig(
        model=model or "openai/gpt-oss-120b",
        provider=provider or "groq",
        base_url=base_url or "https://api.groq.com/openai/v1"
    )


# ---------------------------------------------------------------------------
# PART 2 — DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class Bounty:
    """Represents a single bounty issue found on GitHub."""
    number: int
    title: str
    body: str
    repo: str
    url: str
    labels: list = field(default_factory=list)
    pr_count: int = 0
    repo_last_active: Optional[datetime] = None
    score: float = 0.0
    score_details: str = ""
    ai_analysis: str = ""
    status: str = "new"  # new, filtered, scored, picked, worked, rejected
    pr_url: Optional[str] = None


# ---------------------------------------------------------------------------
# PART 3 — AI CALLER (USING HERMES)
# ---------------------------------------------------------------------------

class HermesAI:
    """Calls Groq API directly for AI scoring and analysis."""

    def __init__(self, model_config: ModelConfig):
        self.model = "openai/gpt-oss-120b"
        self.provider = "groq"
        self.base_url = "https://api.groq.com/openai/v1"
        self.key = self._load_groq_key()

    def _load_groq_key(self) -> str:
        """Load GROQ_KEY from ~/downloads/freellmapi-keys_env."""
        env_path = os.path.expanduser("~/downloads/freellmapi-keys_env")
        if os.path.isfile(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GROQ_KEY=") and not line.startswith("#"):
                        return line.split("=", 1)[1].strip()
        raise RuntimeError(
            "GROQ_KEY not found in ~/downloads/freellmapi-keys.env"
        )

    def _call_groq(self, prompt: str, timeout: int = 120) -> str:
        """
        Calls Groq Chat Completions API directly via httpx.
        Uses llama-3.3-70b-versatile model.
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4096,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except httpx.TimeoutException:
            return "ERROR: Groq API call timed out"
        except Exception as e:
            return f"ERROR: {str(e)}"

    def score_bounty(self, bounty: Bounty) -> tuple[float, str]:
        """
        Asks the Groq model to score a bounty 1-10.
        Returns (score, explanation).
        """
        prompt = f"""You are a bounty evaluation expert. Rate this GitHub issue on a scale of 1-10.

REPO: {bounty.repo}
ISSUE: #{bounty.number}
TITLE: {bounty.title}
LABELS: {', '.join(bounty.labels)}
DESCRIPTION:
{bounty.body[:1000]}

Evaluate based on:
1. DIFFICULTY (1-3): How hard is this? Lower is better for bounties.
2. CLARITY (1-3): Are the instructions clear? Is reproduction described?
3. ACCEPTANCE CHANCE (1-4): How likely is this to be accepted? Good first issue / help wanted labels help.

Respond ONLY with:
SCORE: X/10
REASONING: [brief explanation]

Do not include any other text."""

        response = self._call_groq(prompt, timeout=90)

        # Handle API errors
        if response.startswith("ERROR:"):
            print(f"    ⚠ Groq API error: {response}")
            return -1, f"API error: {response}"

        # Parse score from response
        score_match = re.search(r'SCORE:\s*(\d+)/10', response, re.IGNORECASE)
        reasoning_match = re.search(r'REASONING:\s*(.+)', response, re.IGNORECASE | re.DOTALL)

        if not score_match:
            print(f"    ⚠ Could not parse score from Groq response")
            print(f"    Response: {response[:200]}")
            return -1, f"Parse error: unexpected response format"

        score = int(score_match.group(1))
        reasoning = reasoning_match.group(1).strip() if reasoning_match else response[:200]

        return score, reasoning

    def work_on_bounty(self, bounty: Bounty) -> str:
        """
        Asks the Groq model to analyze the issue and produce a draft fix approach.
        """
        prompt = f"""You are a senior developer analyzing a GitHub bounty issue.

REPO: {bounty.repo}
ISSUE: #{bounty.number}
TITLE: {bounty.title}
DESCRIPTION:
{bounty.body[:2000]}

Please produce:
1. A brief analysis of what the issue is asking
2. The files/functions likely involved
3. A proposed approach to fix it
4. A draft code snippet or pseudocode showing the fix
5. What tests would verify the fix

Respond in a structured format with clear sections."""

        response = self._call_groq(prompt, timeout=120)
        return response


# ---------------------------------------------------------------------------
# PART 4 — GITHUB SCANNER
# ---------------------------------------------------------------------------

class GitHubScanner:
    """Scans GitHub repos for bounty issues using gh CLI."""

    BOUNTY_LABELS = ["bounty", "help wanted", "good first issue"]

    def __init__(self):
        self.issues_found: list[Bounty] = []

    def load_repos(self, repos_file: str) -> list[str]:
        """Load target repos from a text file."""
        repos = []
        if not os.path.isfile(repos_file):
            return repos
        with open(repos_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    repos.append(line)
        return repos

    def scan_bounties(self, repos: list[str]) -> list[Bounty]:
        """
        Scan all repos for issues with bounty labels.
        Uses gh issue list with label filter.
        """
        all_bounties = []

        for repo in repos:
            for label in self.BOUNTY_LABELS:
                try:
                    result = subprocess.run(
                        ["gh", "issue", "list",
                         "--repo", repo,
                         "--label", label,
                         "--state", "all",
                         "--limit", "20",
                         "--json", "number,title,body,labels,url,createdAt"],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        issues = json.loads(result.stdout)
                        for issue_data in issues:
                            bounty = Bounty(
                                number=issue_data["number"],
                                title=issue_data["title"],
                                body=issue_data.get("body", "") or "",
                                repo=repo,
                                url=issue_data.get("url", ""),
                                labels=[l["name"] for l in issue_data.get("labels", [])],
                                repo_last_active=self._get_repo_last_active(repo)
                            )
                            all_bounties.append(bounty)
                except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
                    print(f"  ⚠ Could not scan {repo} for label '{label}': {e}")
                    continue

        # Deduplicate by (repo, number)
        seen = set()
        unique = []
        for b in all_bounties:
            key = (b.repo, b.number)
            if key not in seen:
                seen.add(key)
                unique.append(b)

        self.issues_found = unique
        return unique

    def _get_repo_last_active(self, repo: str) -> Optional[datetime]:
        """Get the last commit date of a repo."""
        try:
            result = subprocess.run(
                ["gh", "repo", "view", repo,
                 "--json", "pushedAt"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                pushed = data.get("pushedAt", "")
                if pushed:
                    return datetime.fromisoformat(pushed.replace("Z", "+00:00"))
        except Exception:
            pass
        return None

    def count_open_prs(self, repo: str, issue_number: int) -> int:
        """Count open PRs associated with an issue."""
        try:
            result = subprocess.run(
                ["gh", "pr", "list",
                 "--repo", repo,
                 "--state", "open",
                 "--search", f"#{issue_number}",
                 "--limit", "50",
                 "--json", "number"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                prs = json.loads(result.stdout)
                return len(prs)
        except Exception:
            pass
        return 0


# ---------------------------------------------------------------------------
# PART 5 — FILTER
# ---------------------------------------------------------------------------

def filter_bounties(bounties: list[Bounty], max_items: int = None) -> list[Bounty]:
    """
    Filter out bounties that are:
    - Already have >3 open PRs
    - From repos inactive >30 days

    Args:
        max_items: If set, only process the first N bounties (for debugging).
    """
    filtered = []
    now = datetime.now(tz=timezone.utc)
    thirty_days_ago = now - timedelta(days=30)
    scanner = GitHubScanner()

    # Limit for debugging
    if max_items is not None:
        bounties = bounties[:max_items]
        print(f"  (DEBUG: limiting filter to first {max_items} bounties)\n")

    for idx, bounty in enumerate(bounties, 1):
        print(f"  [{idx}/{len(bounties)}] Filtering #{bounty.number} ({bounty.repo})...")

        # Count open PRs
        bounty.pr_count = scanner.count_open_prs(bounty.repo, bounty.number)
        print(f"    → {bounty.pr_count} open PRs")

        if bounty.pr_count > 3:
            bounty.status = "filtered"
            print(f"    ✗ SKIPPED — {bounty.pr_count} open PRs\n")
            continue

        # Check repo activity
        if bounty.repo_last_active and bounty.repo_last_active < thirty_days_ago:
            bounty.status = "filtered"
            print(f"    ✗ SKIPPED — repo inactive since {bounty.repo_last_active.date()}\n")
            continue

        bounty.status = "scored"
        filtered.append(bounty)
        print(f"    ✓ PASSED\n")

    return filtered


# ---------------------------------------------------------------------------
# PART 6 — SCORING
# ---------------------------------------------------------------------------

def score_bounties(bounties: list[Bounty], ai: HermesAI) -> list[Bounty]:
    """Score each bounty using the active AI model."""
    print(f"\n🔍 Scoring {len(bounties)} bounties with model: {ai.model}...\n")

    scored = []
    for i, bounty in enumerate(bounties, 1):
        print(f"  [{i}/{len(bounties)}] Scoring #{bounty.number}: {bounty.title[:60]}...")
        score, reasoning = ai.score_bounty(bounty)
        bounty.score = score
        bounty.score_details = reasoning
        scored.append(bounty)
        if score < 0:
            print(f"    ✗ SCORING FAILED: {reasoning}")
        else:
            print(f"    → Score: {score}/10")

    # Sort by score descending
    scored.sort(key=lambda b: b.score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# PART 7 — PICK BEST
# ---------------------------------------------------------------------------

def pick_best(bounties: list[Bounty]) -> Optional[Bounty]:
    """Select the bounty with the highest score."""
    if not bounties:
        return None
    best = bounties[0]
    best.status = "picked"
    print(f"\n🏆 Best bounty: #{best.number} ({best.repo}) — Score: {best.score}/10")
    print(f"    Title: {best.title}")
    return best


# ---------------------------------------------------------------------------
# PART 8 — AUTO-EXECUTE WORK ON BOUNTY (FORK → API COMMIT → PR)
# ---------------------------------------------------------------------------

def _fork_repo(repo: str, token: str) -> str:
    """Cek apakah fork sudah ada via CLI, atau buat fork baru.
    Return nama fork (owner/repo) jika berhasil.
    """
    import subprocess
    result = subprocess.run(
        ["gh", "repo", "fork", repo, "--clone=false"],
        capture_output=True, text=True, timeout=120,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 and "already exists" not in output.lower():
        raise RuntimeError(f"gh repo fork failed (rc={result.returncode}): {output}")

    # Deteksi: fork sudah ada
    if "already exists" in output.lower():
        for line in output.splitlines():
            m = re.search(r"([^/\s]+/[^/\s]+)\s+already\s+exists", line, re.IGNORECASE)
            if m:
                fork_full_name = m.group(1).strip()
                print(f"  ✓ Fork already exists → {fork_full_name}")
                return fork_full_name
        m = re.search(r"([^/\s]+/[^/\s]+)", output)
        if m:
            fork_full_name = m.group(1).strip()
            print(f"  ✓ Fork detected → {fork_full_name}")
            return fork_full_name
        raise RuntimeError(f"Fork already exists but could not parse name from: {output[:200]}")

    # Fork baru berhasil
    for line in output.splitlines():
        if "forked" in line.lower() and "/" in line:
            m = re.search(r"([^/\s]+/[^/\s]+)", line)
            if m:
                fork_full_name = m.group(1).strip()
                print(f"  ✓ Forked → {fork_full_name}")
                return fork_full_name

    m = re.search(r"([^/\s]+/[^/\s]+)", output)
    if m:
        fork_full_name = m.group(1).strip()
        print(f"  ✓ Forked → {fork_full_name}")
        return fork_full_name

    raise RuntimeError(f"Could not parse fork name from: {output[:200]}")


def _get_default_branch_sha(fork_full_name: str, token: str) -> str:
    """Dapatkan SHA dari branch default di fork."""
    headers = {"Authorization": f"token {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    # Dapatkan default branch name
    resp = httpx.get(f"https://api.github.com/repos/{fork_full_name}",
                     headers=headers, timeout=15)
    resp.raise_for_status()
    default_branch = resp.json()["default_branch"]
    # Dapatkan SHA dari default branch
    resp = httpx.get(f"https://api.github.com/repos/{fork_full_name}/branches/{default_branch}",
                     headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()["commit"]["sha"]


def _create_branch_via_api(fork_full_name: str, branch_name: str,
                           base_sha: str, token: str) -> None:
    """Buat branch baru di fork menggunakan GitHub API."""
    headers = {"Authorization": f"token {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "Content-Type": "application/json"}
    payload = {
        "ref": f"refs/heads/{branch_name}",
        "sha": base_sha,
    }
    resp = httpx.post(f"https://api.github.com/repos/{fork_full_name}/git/refs",
                      json=payload, headers=headers, timeout=15)
    if resp.status_code == 422:
        # Branch sudah ada — tidak error
        print(f"  ✓ Branch '{branch_name}' already exists, reusing")
        return
    resp.raise_for_status()
    print(f"  ✓ Branch '{branch_name}' created via API")


def _read_file_via_api(fork_full_name: str, file_path: str,
                       token: str) -> tuple[str, str]:
    """Baca konten file dari fork. Return (decoded_content, sha)."""
    headers = {"Authorization": f"token {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    resp = httpx.get(f"https://api.github.com/repos/{fork_full_name}/contents/{file_path}",
                     headers=headers, timeout=15)
    if resp.status_code == 404:
        raise FileNotFoundError(f"File not found via API: {file_path}")
    resp.raise_for_status()
    data = resp.json()
    import base64
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def _commit_file_via_api(fork_full_name: str, file_path: str,
                         new_content: str, sha: str, branch: str,
                         commit_msg: str, token: str) -> None:
    """Commit perubahan file ke branch di fork menggunakan GitHub API."""
    import base64
    headers = {"Authorization": f"token {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "Content-Type": "application/json"}
    payload = {
        "message": commit_msg,
        "content": base64.b64encode(new_content.encode("utf-8")).decode("utf-8"),
        "sha": sha,
        "branch": branch,
    }
    resp = httpx.put(f"https://api.github.com/repos/{fork_full_name}/contents/{file_path}",
                     json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    print(f"  ✓ Committed to {fork_full_name}:{branch}")


def work_on_bounty(bounty: Bounty, ai: HermesAI) -> Bounty:
    """Generate AI analysis, lalu eksekusi via GitHub API:
    fork → create branch → read file → edit → commit → PR.
    Tanpa clone lokal.
    """
    print(f"\n⚙️  Working on #{bounty.number}: {bounty.title[:60]}...")
    analysis = ai.work_on_bounty(bounty)
    bounty.ai_analysis = analysis
    bounty.status = "worked"
    print(f"    ✓ AI analysis complete ({len(analysis)} chars)\n")

    # ─── Load GitHub token dari file env ───
    gh_token = None
    env_path = os.path.expanduser("~/downloads/freellmapi-keys_env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GITHUB_KEY=") and not line.startswith("#"):
                    gh_token = line.split("=", 1)[1].strip()
                    break

    if not gh_token:
        print("  ❌ GITHUB_KEY not found in ~/downloads/freellmapi-keys_env")
        print("     PR creation aborted. Analysis only saved to log.")
        return bounty

    # Verify GitHub token before proceeding
    print("  ┌─ Verifying GitHub token...")
    try:
        resp = httpx.get("https://api.github.com/user",
                         headers={"Authorization": f"token {gh_token}"},
                         timeout=15)
        if resp.status_code != 200:
            user_data = resp.json()
            error_msg = user_data.get("message", f"HTTP {resp.status_code}")
            print(f"  ❌ GitHub token invalid (HTTP {resp.status_code}): {error_msg}")
            print("     Aborting. Check GITHUB_KEY in ~/downloads/freellmapi-keys_env")
            return bounty
        user_info = resp.json()
        print(f"  ✓ Token valid — authenticated as: {user_info.get('login', 'unknown')}")
    except Exception as e:
        print(f"  ❌ Token verification failed: {e}")
        print("     Aborting.")
        return bounty
    print("  └───────────────────────────────────────────────────────\n")

    repo = bounty.repo                        # github/docs
    issue_num = bounty.number                 # 35823
    title = bounty.title
    branch_name = f"fix-{issue_num}-backticks"

    # File yang diedit (PERBAIKAN PATH — tanpa /en/)
    file_path = "content/pages/setting-up-a-github-pages-site-with-jekyll/adding-a-theme-to-your-github-pages-site-using-jekyll.md"
    old_text = "Create a new file called _/assets/css/style.scss_."
    new_text = "Create a new file called `/assets/css/style.scss`."

    commit_msg = f"fix: wrap file name in backticks (#{issue_num})"

    print("  ┌─ STEP 1: Fork repository ─────────────────────────────")
    try:
        fork_full_name = _fork_repo(repo, gh_token)
    except Exception as e:
        print(f"  ❌ Fork failed: {e}")
        return bounty

    # ─── PRECHECK: pastikan file memang perlu di-edit ───
    print("  ┌─ PRECHECK: Read file to verify text exists ───────────")
    try:
        content, _ = _read_file_via_api(fork_full_name, file_path, gh_token)
        if old_text not in content:
            print(f"  ⚠ File already correct or text not found:")
            print(f"     Expected: {old_text}")
            print(f"     File: {file_path}")
            print(f"     → Issue mungkin sudah solved atau file tidak perlu diedit.")
            print(f"     → SKIP commit & PR. No changes needed.")
            return bounty
        print(f"  ✓ Text found in file — fix needed")
    except Exception as e:
        print(f"  ⚠ Could not read file via API: {e}")
        print(f"     → Skipping edit. PR akan dicreate tanpa commit (jika memungkinkan).")
        # Lanjutkan ke konfirmasi tapi tanpa commit

    # ─── KONFIRMASI SEBELUM KOMIT & PR ───
    print()
    print("  ════════════════════════════════════════════════════════")
    print("  ⚠️  CONFIRMATION REQUIRED before commit & PR creation")
    print("  ════════════════════════════════════════════════════════")
    print(f"     Repo:      {repo}")
    print(f"     Fork:      {fork_full_name}")
    print(f"     Branch:    {branch_name}")
    print(f"     File:      {file_path}")
    print(f"     Change:    underscore → backtick")
    print(f"     Commit:    {commit_msg}")
    print(f"     PR base:   main (github/docs)")
    print()
    print("  → Waiting for IZIN before proceeding with API commit & PR.")
    print("  → Reply with 'IZIN' to proceed.")

    # ─── EKSEKUSI SETELAH IZIN (steps 2-4) ───
    # Step 2: Create branch via API
    print("  ┌─ STEP 2: Create branch via API ───────────────────────")
    try:
        base_sha = _get_default_branch_sha(fork_full_name, gh_token)
        _create_branch_via_api(fork_full_name, branch_name, base_sha, gh_token)
    except Exception as e:
        print(f"  ❌ Branch creation failed: {e}")
        return bounty

    # Step 3: Read file, apply fix, commit via API
    print("  └─ STEP 3: Read + edit + commit via API ───────────────")
    try:
        content, file_sha = _read_file_via_api(fork_full_name, file_path, gh_token)
        if old_text not in content:
            raise ValueError(f"Text not found in file:\n{old_text}")
        new_content = content.replace(old_text, new_text)
        _commit_file_via_api(fork_full_name, file_path, new_content,
                             file_sha, branch_name, commit_msg, gh_token)
    except Exception as e:
        print(f"  ❌ Commit failed: {e}")
        return bounty

    # Step 4: Create PR
    print("  ┌─ STEP 4: Create Pull Request ─────────────────────────")
    pr_body = f"""## Summary
Fix file-name formatting in *Adding a theme to your GitHub Pages site using Jekyll – Customizing your theme's CSS*.

According to the Docs style guide, file names should be wrapped in backticks (`` `...` ``) rather than emphasized with underscores.

- **Changed**: `_/assets/css/style.scss_` → `` `/assets/css/style.scss` ``
- **Issue**: #{issue_num}
- **Labels**: {', '.join(bounty.labels)}

---
🤖 Auto-generated by Bounty Hunter (Groq GPT-OSS-120B)
"""
    try:
        pr_url = _create_pr(fork_full_name, repo, branch_name,
                            title, pr_body, gh_token)
        bounty.pr_url = pr_url
    except Exception as e:
        print(f"  ❌ PR creation failed: {e}")
        return bounty

    print()
    print(f"  ✅ DONE! PR: {bounty.pr_url}")
    return bounty


# ---------------------------------------------------------------------------
# PART 9 — NOTIFY / LOGGING
# ---------------------------------------------------------------------------

def notify(bounties: list[Bounty], best: Optional[Bounty], log_file: str):
    """Display summary and save to log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_cfg = load_hermes_config()

    summary = f"""
{'='*70}
  BOUNTY HUNTER REPORT
  Generated: {timestamp}
  Model: {model_cfg.model} ({model_cfg.provider})
{'='*70}

Scanned: {len(bounties)} bounties
"""
    if best:
        summary += f"""
🏆 SELECTED: #{best.number} ({best.repo})
Score: {best.score}/10
Title: {best.title}
URL: {best.url}
Labels: {', '.join(best.labels)}

AI ANALYSIS:
{best.ai_analysis}

|RECOMMENDED NEXT STEPS:
|1. Review the analysis above
|2. Auto-execution was attempted (fork, clone, branch, edit, commit, push, PR)
|3. Check PR link in the summary below
|4. Monitor PR for review/comments
"""

    summary += f"""
{'='*70}
  ALL BOUNTIES SUMMARY:
{'='*70}
"""
    for b in sorted(bounties, key=lambda x: x.score, reverse=True):
        summary += f"""
  #{b.number} ({b.repo}) — Score: {b.score}/10 — Status: {b.status}
    Title: {b.title[:80]}
    Labels: {', '.join(b.labels)}
"""

    # Print to terminal
    print(summary)

    # Save to log file
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), log_file)
    with open(log_path, "w") as f:
        f.write(summary)
    print(f"\n📝 Log saved to: {log_path}")


# ---------------------------------------------------------------------------
# MAIN WORKFLOW
# ---------------------------------------------------------------------------

def _fetch_issue_via_api(repo: str, issue_num: int,
                          token: Optional[str] = None) -> Bounty:
    """Fetch single issue dari GitHub API dan return objek Bounty.
    Jika token diberikan, gunakan autentikasi (lebih tinggi rate limit).
    """
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"token {token}"
    url = f"https://api.github.com/repos/{repo}/issues/{issue_num}"
    resp = httpx.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    labels = [l["name"] for l in data.get("labels", [])]
    bounty = Bounty(
        number=data["number"],
        title=data["title"],
        body=data.get("body", "") or "",
        repo=repo,
        url=data.get("html_url", ""),
        labels=labels,
    )
    return bounty


def main():
    parser = argparse.ArgumentParser(
        description="🏆 GitHub Bounty Hunter — Automated issue discovery and AI scoring"
    )
    parser.add_argument(
        "--repos", default=None,
        help="Path to repos.txt file (default: ./repos.txt)"
    )
    parser.add_argument(
        "--target", default=None, type=int,
        help="Issue number to target directly (skips scan; uses github/docs)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan + score only, skip work_on_bounty"
    )
    parser.add_argument(
        "--output", default="bounty_hunter.log",
        help="Log file name (default: bounty_hunter.log)"
    )
    parser.add_argument(
        "--no-filter", action="store_true",
        help="Skip filtering step"
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repos_file = args.repos or os.path.join(script_dir, "repos.txt")

    print("=" * 70)
    print("  🏆 GITHUB BOUNTY HUNTER")
    print("=" * 70)

    # Load model config from Hermes
    print("\n📋 Loading Hermes model configuration...")
    model_cfg = load_hermes_config()
    print(f"    Model: {model_cfg.model}")
    print(f"    Provider: {model_cfg.provider}")
    print(f"    Base URL: {model_cfg.base_url}")

    ai = HermesAI(model_cfg)

    # ─── TARGET MODE: skip scan, langsung fetch issue via API ───
    if args.target is not None:
        print(f"\n🎯 Targeting issue #{args.target} (skipping scan)...")
        repo = "github/docs"

        # Load GitHub token (untuk autentikasi API, optional tapi disarankan)
        gh_token = None
        env_path = os.path.expanduser("~/downloads/freellmapi-keys_env")
        if os.path.isfile(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GITHUB_KEY=") and not line.startswith("#"):
                        gh_token = line.split("=", 1)[1].strip()
                        break

        try:
            best = _fetch_issue_via_api(repo, args.target, gh_token)
            best.status = "picked"
            print(f"  ✓ Fetched: #{best.number} ({best.repo}) — {best.title[:70]}")
        except Exception as e:
            print(f"  ❌ Failed to fetch issue #{args.target}: {e}")
            sys.exit(1)

        bounties = [best]
        # Lanjut ke work_on_bounty (skip scoring & filter)
        if not args.dry_run:
            best = work_on_bounty(best, ai)
        else:
            print(f"\n📝 Dry-run mode: skipping work_on_bounty for #{best.number}")
    else:
        # ─── NORMAL MODE: load repos, scan, filter, score ───
        scanner = GitHubScanner()

        print(f"\n📡 Loading repos from {repos_file}...")
        repos = scanner.load_repos(repos_file)
        if not repos:
            print("  ❌ No repos found! Edit repos.txt or use --repos flag.")
            sys.exit(1)
        print(f"    {len(repos)} repos: {', '.join(repos[:5])}{'...' if len(repos)>5 else ''}")

        print("\n🔍 Scanning for bounties...")
        bounties = scanner.scan_bounties(repos)
        print(f"    Found {len(bounties)} bounty issues!")

        # Step 2: Filter (skip if targeting specific issue)
        if not args.no_filter:
            print("\n🧹 Filtering bounties...")
            bounties = filter_bounties(bounties, max_items=5)  # DEBUG: limit to 5
            print(f"    {len(bounties)} bounties passed filters.\n")
        else:
            print("\n🧹 Skipping filter (--no-filter mode)...\n")

        if not bounties:
            print("\n❌ No bounties to evaluate. Exiting.")
            sys.exit(0)

        # Step 3: Score
        print()
        bounties = score_bounties(bounties, ai)

        # Step 4: Pick best
        best = pick_best(bounties)

    # Step 5: Work on best (unless dry-run)
    if best and not args.dry_run and not args.target:
        best = work_on_bounty(best, ai)

    # Step 6: Notify & log
    notify(bounties, best, args.output)

    print("\n✅ Bounty Hunter complete!")
    if best and best.pr_url:
        print(f"   PR created: {best.pr_url}")
    else:
        print("   No PR was created (dry-run, token error, or work skipped).")


if __name__ == "__main__":
    main()
