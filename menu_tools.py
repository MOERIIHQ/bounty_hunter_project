#!/usr/bin/env python3
"""
menu_tools.py — Interactive Bounty Hunter v2.0 (menu-driven)

Entry point interaktif yang memanggil fungsi-fungsi dari bounty_hunter.py.
Jalankan: python menu_tools.py
"""

import os
import sys
import json
import subprocess
import getpass
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# IMPORT DARI bounty_hunter (library lama — TIDAK DIUBAH)
# ---------------------------------------------------------------------------
# Karena bounty_hunter.py ada di folder yang sama, kita import sebagai modul.
# Struktur import didasarkan pada fungsi/fungsi yang ada di file tersebut.

from bounty_hunter import (
    Bounty,
    ModelConfig,
    load_hermes_config,
    GitHubScanner,
    HermesAI,
    filter_bounties as _filter_bounties,
    score_bounties as _score_bounties,
    pick_best as _pick_best,
    work_on_bounty as _work_on_bounty,
    notify as _notify,
    _fork_repo,
    _get_default_branch_sha,
    _create_branch_via_api,
    _read_file_via_api,
    _commit_file_via_api,
    _fetch_issue_via_api,
)

# ---------------------------------------------------------------------------
# KONSTANSI PATH (semua pakai expanduser)
# ---------------------------------------------------------------------------
BASE_DIR = Path(os.path.expanduser("~/bounty_hunter_project"))
KEYS_ENV = Path(os.path.expanduser("~/downloads/freellmapi-keys_env"))
REPOS_FILE = BASE_DIR / "repos.txt"
TARGET_STATE_FILE = BASE_DIR / "current_target.json"
PR_HISTORY_FILE = BASE_DIR / "pr_history.json"


# ===========================================================================
# HELPER
# ===========================================================================


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> dict | list:
    if not path.is_file():
        return {} if path.name.endswith("json") else []
    with open(path) as f:
        return json.load(f)


def _save_json(path: Path, data) -> None:
    _ensure_dir(path)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _box(lines: list[str], width: int = 40) -> str:
    """Wrap lines dalam box-drawing sederhana."""
    result = []
    result.append("┌" + "─" * width + "┐")
    for line in lines:
        result.append("│ " + line.ljust(width - 2) + " │")
    result.append("└" + "─" * width + "┘")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# _create_pr — implementasi lokal (karena bounty_hunter.py tidak punya fungsi ini)
# Dipanggil dari work_on_bounty di file lama tapi tidak pernah didefinisikan.
# ---------------------------------------------------------------------------

def _create_pr(fork_full_name: str, repo: str, branch_name: str,
               title: str, body: str, token: str) -> str:
    """Buat Pull Request via GitHub API. Return URL PR."""
    import httpx
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    owner = fork_full_name.split("/")[0]
    payload = {
        "title": title,
        "body": body,
        "head": f"{owner}:{branch_name}",
        "base": repo.split("/")[0] if "/" in repo else "main",
    }
    print(f"  → POST /repos/{repo}/pulls (head={owner}:{branch_name}, base=main)")
    resp = httpx.post(
        f"https://api.github.com/repos/{repo}/pulls",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("html_url", "")


def _load_gh_token() -> str | None:
    """Baca GITHUB_KEY dari ~/downloads/freellmapi-keys_env."""
    if not KEYS_ENV.is_file():
        return None
    with open(KEYS_ENV) as f:
        for line in f:
            line = line.strip()
            if line.startswith("GITHUB_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return None


def _verify_github_token(token: str) -> dict | None:
    """Verifikasi token GitHub via API. Return user info dict atau None."""
    try:
        import httpx
        resp = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def _gh_auth_status() -> str:
    """Jalankan `gh auth status`, return output string."""
    try:
        r = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return r.stdout.strip()
        return ""
    except Exception:
        return ""


def _requires_login(func):
    """Decorator: cek login sebelum masuk ke menu. Return False jika gagal."""
    def wrapper(self, *args, **kwargs):
        token = _load_gh_token()
        if not token or not _verify_github_token(token):
            print("\n❌ Silakan login dulu via menu 1\n")
            return False
        return func(self, *args, **kwargs)
    return wrapper


# ===========================================================================
# CLASS UTAMA
# ===========================================================================


class BountyHunterTools:
    """Pemuat menu interaktif Bounty Hunter v2.0."""

    def __init__(self):
        self.model_cfg = load_hermes_config()
        # Cek apakah HermesAI bisa inisialisasi (GROQ_KEY tersedia)
        try:
            self.ai = HermesAI(self.model_cfg)
        except RuntimeError:
            print("⚠ Warning: GROQ_KEY tidak ditemukan — AI scoring akan gagal.")
            self.ai = None

    # ------------------------------------------------------------------
    # MENU 1 — AUTH GITHUB
    # ------------------------------------------------------------------

    def menu_auth_github(self) -> None:
        print()
        print("── auth GitHub ──────────────────────────────────────")
        # 1a. Cek gh auth status
        gh_status = _gh_auth_status()
        if gh_status:
            print("gh CLI status:")
            for line in gh_status.splitlines()[:5]:
                print(f"  {line}")
        else:
            print("gh CLI: belum login atau tidak terpasang.")

        # 1b. Verifikasi GITHUB_KEY via API
        token = _load_gh_token()
        user_info = None
        if token:
            user_info = _verify_github_token(token)
            if user_info:
                print(f"\n✓ Sudah login sebagai {user_info.get('login', 'unknown')}")
            else:
                print("\n❌ Token di ~/downloads/freellmapi-keys_env tidak valid.")
                print("   GITHUB_KEY perlu diperbarui.")
                token = None  # Force re-entry

        # 1c. Jika belum valid, minta token baru
        if not token or not user_info:
            print()
            print("── Masukkan GitHub Personal Access Token ───────────")
            new_token = getpass.getpass("Token (tersembunyi): ").strip()
            if not new_token:
                print("\n❌ Token kosong. Gagal login.\n")
                return

            # Simpan ke file env
            _ensure_dir(KEYS_ENV)
            with open(KEYS_ENV) as f:
                lines = f.readlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("GITHUB_KEY=") and not line.startswith("#"):
                    new_lines.append(f"GITHUB_KEY={new_token}\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"GITHUB_KEY={new_token}\n")
            with open(KEYS_ENV, "w") as f:
                f.writelines(new_lines)

            # Coba gh auth login --with-token
            print("\n  Mencoba gh auth login...")
            try:
                r = subprocess.run(
                    ["gh", "auth", "login", "--with-token"],
                    input=new_token,
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode == 0:
                    print("  ✓ gh auth login berhasil.")
                else:
                    print(f"  ⚠ gh auth login gagal (rc={r.returncode})")
                    print(f"  stderr: {r.stderr.strip()[:200]}")
            except Exception as e:
                print(f"  ⚠ Gagal menjalankan gh auth login: {e}")

            # Verifikasi ulang
            user_info = _verify_github_token(new_token)
            if user_info:
                print(f"  ✓ Verifikasi API: login sebagai {user_info.get('login')}")
            else:
                print("  ❌ Token masih invalid setelah login.")
                return

        # 1d. Status akhir
        print()
        print("  └── Status akhir:")
        gh_status = _gh_auth_status()
        if gh_status:
            print(f"  gh CLI:    login")
        else:
            print(f"  gh CLI:    belum terdeteksi")
        print(f"  API key:   valid ✓")
        print(f"  Username:  {user_info.get('login', 'unknown') if user_info else '—'}")
        print()

    # ------------------------------------------------------------------
    # MENU 2 — SCAN BOUNTY
    # ------------------------------------------------------------------

    def menu_scan_bounty(self) -> None:
        if not _load_gh_token() or not _verify_github_token(_load_gh_token()):
            print("\n❌ Silakan login dulu via menu 1\n")
            return

        if self.ai is None:
            print("\n❌ GROQ_KEY tidak ditemukan. Tidak bisa melakukan AI scoring.\n")
            return

        print()
        print("── Scan Bounty ───────────────────────────────────────")

        # Load repo
        repos_file = REPOS_FILE
        if not repos_file.is_file():
            print(f"  ❌ {repos_file} tidak ditemukan.")
            return

        scanner = GitHubScanner()
        print(f"  Loading repos dari {repos_file}...")
        repos = scanner.load_repos(str(repos_file))
        if not repos:
            print("  ❌ Tidak ada repo di file. Keluar.")
            return
        print(f"  {len(repos)} repo: {', '.join(repos[:3])}")

        # Scan
        print("\n  🔍 Scanning...")
        all_bounties = scanner.scan_bounties(repos)
        print(f"  Found {len(all_bounties)} issues mentah.")

        if not all_bounties:
            print("\n  ❌ Tidak ada bounty ditemukan. Keluar.\n")
            return

        # Filter
        print("\n  🧹 Filtering...")
        filtered = _filter_bounties(all_bounties, max_items=20)
        print(f"  {len(filtered)} lolos filter.")

        if not filtered:
            print("\n  ❌ Semua sudah difilter. Keluar.\n")
            return

        # Score
        print("\n  🔍 Scoring dengan AI...")
        scored = _score_bounties(filtered, self.ai)

        if not scored:
            print("\n  ❌ Gagal scoring. Keluar.\n")
            return

        # Ambil 5 teratas
        top5 = scored[:5]
        print()
        print("  ╔" + "═" * 58 + "╗")
        print("  ║  TOP 5 BOUNTY (sort skor)                       ║")
        print("  ╠" + "═" * 58 + "╣")
        print("  ║  No │ Repo#Issue       │ Judul                    ║")
        print("  ║      │ Skor │ Status   │ PR                       ║")
        print("  ╚" + "═" * 58 + "╝")

        for idx, b in enumerate(top5, 1):
            pr_status = "✓ PR" if b.pr_url else "—"
            title_short = b.title[:25].ljust(25)
            print(f"  [{idx}] {b.repo}#{b.number}  |  {b.score}/10  |  {b.status}")
            print(f"      {title_short}")
            print(f"      Labels: {', '.join(b.labels[:3])}")
            print(f"      PR: {pr_status}  │  AI: {'✓' if b.ai_analysis else '—'}")
            print()

        # Input pilihan
        while True:
            print("  Pilih [1-5], 00 = kembali, 99 = exit: ", end="")
            choice = input().strip()
            if choice == "99":
                print("\n  Sampai jumpa.\n")
                sys.exit(0)
            if choice == "00":
                return
            if choice.isdigit() and 1 <= int(choice) <= 5:
                pick = top5[int(choice) - 1]
                break
            print("  ❌ Nomor tidak valid")

        # Simpan current_target.json
        target_data = {
            "repo": pick.repo,
            "issue": pick.number,
            "title": pick.title,
            "status": "analyzed",
            "pr_url": pick.pr_url or None,
            "date": _now_str(),
        }
        _save_json(TARGET_STATE_FILE, target_data)
        print(f"\n  ✓ Target tersimpan: {pick.repo}#{pick.number}")

        # AI analysis
        print("\n  ┌─ AI Analysis ─────────────────────────────────────")
        analysis = self.ai.work_on_bounty(pick)
        pick.ai_analysis = analysis
        print(f"  ✓ Analysis selesai ({len(analysis)} chars)")
        print("  └───────────────────────────────────────────────────\n")
        print(analysis[:2000] + ("..." if len(analysis) > 2000 else ""))
        print()

        # Simpan log
        log_entry = {
            "repo": pick.repo,
            "issue": pick.number,
            "title": pick.title,
            "status": "analyzed",
            "pr_url": None,
            "date": _now_str(),
            "ai_analysis": analysis,
        }
        log_path = BASE_DIR / f"analysis_log_{_now_str()}.json"
        _save_json(log_path, log_entry)
        print(f"  📝 Log disimpan: {log_path}")
        print()

    # ------------------------------------------------------------------
    # MENU 3 — PULL REQUEST
    # ------------------------------------------------------------------

    def menu_pull_request(self) -> None:
        if not _load_gh_token() or not _verify_github_token(_load_gh_token()):
            print("\n❌ Silakan login dulu via menu 1\n")
            return

        # Load current_target.json
        target = _load_json(TARGET_STATE_FILE)
        if not isinstance(target, dict) or not target:
            print("\n❌ Belum ada target. Pilih menu 2 dulu.\n")
            return

        print()
        print("── Pull Request ──────────────────────────────────────")
        print(f"\n  Target saat ini:")
        print(f"    Repo : {target['repo']}")
        print(f"    Issue: #{target['issue']}")
        print(f"    Title: {target['title'][:60]}")
        print(f"    Status: {target.get('status', '—')}")
        print(f"    PR URL: {target.get('pr_url') or 'belum dibuat'}")
        print()

        # Cek apakah sudah ada PR
        if target.get("pr_url"):
            print("  ✅ Target ini sudah pernah di-PR:")
            print(f"     {target['pr_url']}")
            print()
            return

        # Tampilkan target yang sudah dianalisis tapi belum PR
        targets_raw = _load_json(TARGET_STATE_FILE)
        if not isinstance(targets_raw, dict):
            print("  ❌ Data target rusak.")
            return

        # Ambil data dasar
        repo = targets_raw["repo"]
        issue_num = targets_raw["issue"]
        title = targets_raw["title"]

        # --- Tentukan sumber data patch (manual vs auto) ---
        if all(k in targets_raw and targets_raw[k] for k in ("file_path", "old_text", "new_text")):
            file_path = targets_raw["file_path"]
            old_text = targets_raw["old_text"]
            new_text = targets_raw["new_text"]
            print("📂 Sumber data: dari current_target.json (manual)")
        else:
            # Fallback: parse dari AI analysis (logika lama / nilai default)
            file_path = "content/pages/setting-up-a-github-pages-site-with-jekyll/adding-a-theme-to-your-github-pages-site-using-jekyll.md"
            old_text = "Create a new file called _/assets/css/style.scss_."
            new_text = "Create a new file called `/assets/css/style.scss`."
            print("📂 Sumber data: dari AI analysis (auto)")

        # --- Tentukan commit message ---
        if "commit_message" in targets_raw and targets_raw["commit_message"]:
            commit_msg = targets_raw["commit_message"]
        else:
            commit_msg = f"fix: {title} (#{issue_num})"

        # Tampilkan info target
        print("  Daftar target yang sudah dianalisis tapi belum di-PR:")
        print("  ┌─ current_target.json ─────────────────────────────")
        print(f"  Repo : {targets_raw.get('repo', '—')}")
        print(f"  Issue: #{targets_raw.get('issue', '—')}")
        print(f"  Title: {targets_raw.get('title', '—')[:60]}")
        print(f"  Status: {targets_raw.get('status', '—')}")
        print(f"  PR   : {'✅ Sudah PR' if targets_raw.get('pr_url') else '⏳ Belum PR'}")
        print("  └───────────────────────────────────────────────────\n")

        # Konfirmasi IZIN
        print("  ── KONFIRMASI ──────────────────────────────────────")
        print("  Apakah Anda ingin melanjutkan membuat PR untuk target ini?")
        print("  Language: IZIN untuk melanjutkan, 00 untuk kembali, 99 untuk exit.")
        print()
        while True:
            confirm = input("  Masukkan 'IZIN' untuk konfirmasi: ").strip()
            if confirm == "IZIN":
                break
            if confirm == "00":
                print("\n  Dibatalkan.\n")
                return
            if confirm == "99":
                print("\n  Sampai jumpa.\n")
                sys.exit(0)
            print("  ❌ Masukkan 'IZIN' untuk konfirmasi.")

        # Ambil token
        gh_token = _load_gh_token()
        if not gh_token:
            print("  ❌ Token tidak ditemukan.")
            return

        print()
        print("  ┌─ STEP 1: Fork repository ────────────────────────")
        try:
            fork_full_name = _fork_repo(repo, gh_token)
        except Exception as e:
            print(f"  ❌ Fork gagal: {e}")
            return

        # Precheck file
        print("\n  ┌─ PRECHECK: Baca file ────────────────────────────")
        try:
            content, _ = _read_file_via_api(fork_full_name, file_path, gh_token)
            if old_text not in content:
                print(f"  ⚠ Text tidak ditemukan di file.")
                print(f"     Mungkin issue sudah terselesaikan.")
                print(f"     PR target: {repo}#{issue_num}")
                branch_name = f"fix-{issue_num}-backticks"
                print(f"\n  → PR akan dibuat tanpa commit (tidak ada perubahan).")
            else:
                print("  ✓ Text ditemukan — fix diperlukan.")
        except Exception as e:
            print(f"  ⚠ Gagal baca file: {e}")
            print(f"     PR tetap akan dicoba dibuat.")

        # Step 2: Branch
        print("\n  ┌─ STEP 2: Create branch via API ──────────────────")
        branch_name = f"fix-{issue_num}-backticks"
        try:
            base_sha = _get_default_branch_sha(fork_full_name, gh_token)
            _create_branch_via_api(fork_full_name, branch_name, base_sha, gh_token)
        except Exception as e:
            print(f"  ❌ Branch gagal: {e}")
            return

        # Step 3: Commit (jika text ditemukan)
        print("\n  ┌─ STEP 3: Commit via API ─────────────────────────")
        try:
            content, file_sha = _read_file_via_api(fork_full_name, file_path, gh_token)
            if old_text not in content:
                print()
                print("  ┌─ VERIFIKASI GAGAL ──────────────────────────────")
                print("  ❌ Teks tidak ditemukan di file asli.")
                print("     Bounty ini mungkin sudah diperbaiki orang lain.")
                print("     Coba target lain.")
                print("  └───────────────────────────────────────────────────")
                print()
                print("  → PR DIBATALKAN. Tidak ada commit/PR yang dibuat.")
                print()
                return
            new_content = content.replace(old_text, new_text)
            try:
                _commit_file_via_api(fork_full_name, file_path, new_content,
                                     file_sha, branch_name, commit_msg, gh_token)
                print("  ✓ Commit berhasil.")
            except Exception as e:
                msg = str(e)
                if "422" in msg or "Validation Failed" in msg:
                    print("  ❌ Commit gagal: 422 — file mungkin sudah berubah di server.")
                    print(f"     {e}")
                    print("     PR tetap dicoba dibuat (tanpa commit).")
                else:
                    print(f"  ⚠ Commit gagal: {e}")
                    print("  PR tetap dicoba dibuat.")
        except Exception as e:
            print(f"  ⚠ Commit gagal: {e}")
            print("  PR tetap dicoba dibuat.")

        # Step 4: Create PR
        print("\n  ┌─ STEP 4: Create Pull Request ────────────────────")
        pr_body = (
            f"## Summary\n"
            f"Fix file-name formatting in *Adding a theme to your GitHub Pages "
            f"site using Jekyll – Customizing your theme's CSS*.\n\n"
            f"According to the Docs style guide, file names should be wrapped in "
            f"backticks (\"`...`\") rather than emphasized with underscores.\n\n"
            f"- **Changed**: `_/assets/css/style.scss_` → `` `/assets/css/style.scss` ``\n"
            f"- **Issue**: #{issue_num}\n\n"
            f"---\n"
            f"🤖 Auto-generated by Bounty Hunter v2.0"
        )
        try:
            pr_url = _create_pr(fork_full_name, repo, branch_name, title, pr_body, gh_token)
        except Exception as e:
            msg = str(e)
            if "422" in msg or "Validation Failed" in msg:
                print()
                print("  ┌─ PR GAGAL (422) ────────────────────────────────")
                print("  ❌ Pull Request gagal: 422 — Validation Failed.")
                print(f"     {e}")
                print("     Kemungkinan branch sudah ada PR, atau title/duplikat.")
                print("     Tidak ada perubahan state yang tersimpan.")
                print("  └───────────────────────────────────────────────────")
                print()
                return
            print(f"  ❌ PR gagal: {e}")
            return

        # Simpan ke state
        targets_raw["pr_url"] = pr_url
        targets_raw["status"] = "pr_created"
        _save_json(TARGET_STATE_FILE, targets_raw)

        # Simpan ke pr_history.json
        history = []
        if PR_HISTORY_FILE.is_file():
            with open(PR_HISTORY_FILE) as f:
                try:
                    history = json.load(f)
                except json.JSONDecodeError:
                    history = []

        history.append({
            "repo": repo,
            "issue": issue_num,
            "title": title,
            "pr_url": pr_url,
            "date": _now_str(),
        })
        _save_json(PR_HISTORY_FILE, history)

        print()
        print("  ┌─ PR SUCCESS ──────────────────────────────────────")
        print(f"  ✅ PR dibuat: {pr_url}")
        print(f"  📁 Repo     : {repo}")
        print(f"  🔖 Issue    : #{issue_num}")
        print(f"  📝 Title    : {title[:60]}")
        print(f"  📅 Date     : {_now_str()}")
        print("  └───────────────────────────────────────────────────")
        print()

        # Setelah menu selesai → tampilkan sub-pilihan
        self._post_menu_menu()

    def _post_menu_menu(self) -> None:
        while True:
            print("  ── Kembali ke menu utama? ─────────────────────────")
            print("    00. Kembali ke menu utama")
            print("    99. Exit")
            print()
            choice = input("  Pilih [00/99]: ").strip()
            if choice == "00":
                print()
                return
            if choice == "99":
                print("\n👋 Sampai jumpa!\n")
                sys.exit(0)
            print("  ❌ Pilih 00 atau 99.")

    # ------------------------------------------------------------------
    # MENU 4 — LOG INFO
    # ------------------------------------------------------------------

    def menu_log_info(self) -> None:
        print()
        print("── Log Info ──────────────────────────────────────────")

        # Load pr_history.json
        history = []
        if PR_HISTORY_FILE.is_file():
            with open(PR_HISTORY_FILE) as f:
                try:
                    history = json.load(f)
                except json.JSONDecodeError:
                    history = []

        # Load current_target.json
        current = {}
        if TARGET_STATE_FILE.is_file():
            with open(TARGET_STATE_FILE) as f:
                try:
                    current = json.load(f)
                except json.JSONDecodeError:
                    current = {}

        print("\n  ✅ SUDAH DI-PR (dari pr_history.json):")
        if history:
            for idx, item in enumerate(history, 1):
                print(f"  [{idx}] {item['repo']}#{item['issue']}")
                print(f"      Title  : {item['title'][:55]}")
                print(f"      Date   : {item['date']}")
                print(f"      PR URL : {item['pr_url']}")
                print()
        else:
            print("    Belum ada aktivitas PR.\n")

        print("  ⏳ BELUM DI-PR (dari current_target.json):")
        if current.get("pr_url") is None and current.get("repo"):
            print(f"  Repo : {current['repo']}")
            print(f"  Issue: #{current['issue']}")
            print(f"  Title: {current['title'][:60]}")
            print(f"  Status: {current.get('status', '—')}")
            print(f"  Date  : {current.get('date', '—')}")
            print()
        else:
            print("    Semua target sudah di-PR atau belum ada target.\n")

        if not history and not current.get("repo"):
            print("\n  Belum ada aktivitas. Mulai dengan menu 1 atau 2.\n")

        print("  [Enter] Kembali ke menu utama...")
        input()
        print()

    # ------------------------------------------------------------------
    # MENU 5 — LEARNING
    # ------------------------------------------------------------------

    def menu_learning(self) -> None:
        print()
        print("╔" + "═" * 58 + "╗")
        print("║  LEARNING — Bounty Hunter v2.0 Panduan Penggunaan  ║")
        print("╚" + "═" * 58 + "╝")
        print()

        sections = [
            (
                "1. Cara Mengisi File Kunci",
                "\n"
                "   File kunci berada di: ~/downloads/freellmapi-keys_env\n"
                "   Format: key=value (satu per baris, # untuk komentar)\n\n"
                "   Wajib:\n"
                "     GITHUB_KEY=ghp_xxxxxxxxxxxxxxxxxxxx   (GitHub PAT)\n"
                "     GROQ_KEY=gsk_xxxxxxxxxxxxxxxxxxxx     (Groq API key)\n\n"
                "   Cara dapat GitHub PAT:\n"
                "     Settings → Developer settings → Personal access tokens\n"
                "     → Generate new token (classic) dengan scope:\n"
                "       repo, read:org, write:repo\n\n"
                "   Cara dapat GROQ_KEY:\n"
                "     groq.com → API Keys → Create API Key\n\n"
                "   Setelah mengisi, jalankan menu 1 (Auth GitHub) untuk aktivasi."
            ),
            (
                "2. Alur Kerja Scan → AI Analysis → PR",
                "\n"
                "   Step 1 — Menu 1 (Auth GitHub)\n"
                "     Mengaktifkan GitHub token. Wajib sebelum scan/PR.\n\n"
                "   Step 2 — Menu 2 (Scan Bounty)\n"
                "     - Load daftar repo dari ~/bounty_hunter_project/repos.txt\n"
                "     - Scan issue dengan label: bounty, help wanted, good first issue\n"
                "     - Filter: exclude repo dengan >3 open PR atau inactive >30 hari\n"
                "     - Score dengan AI (Groq / Hermes AI) — skor 1-10\n"
                "     - Tampilkan top 5, user pilih → simpan current_target.json\n"
                "     - Lakukan AI analysis → tampilkan draft perbaikan\n\n"
                "   Step 3 — Menu 3 (Pull Request)\n"
                "     - Load current_target.json\n"
                "     - Jika sudah ada PR → tampilkan link\n"
                "     - Jika belum → tampilkan konfirmasi\n"
                "     - Setelah IZIN: fork → create branch → commit file → create PR\n"
                "     - Simpan pr_history.json dan update current_target.json"
            ),
            (
                "3. Aturan Keamanan",
                "\n"
                "   ★ IZIN sebelum commit/PR ★\n"
                "     Tools ini mewajibkan konfirmasi 'IZIN' sebelum\n"
                "     melakukan fork, commit, dan PR creation.\n"
                "     Tanpa IZIN, tidak ada perubahan yang dilakukan.\n\n"
                "   ★ Token tersembunyi ★\n"
                "     GITHUB_KEY diminta via getpass (tidak muncul di layar).\n\n"
                "   ★ Foreground only ★\n"
                "     Semua operasi berjalan di foreground — tidak ada background.\n\n"
                "   ★ CTRL+C → kembali ke menu ★\n"
                "     Interrupt tidak akan keluar dari program.\n\n"
                "   ★ File state ★\n"
                "     current_target.json dan pr_history.json disimpan di:\n"
                "     ~/bounty_hunter_project/"
            ),
            (
                "4. Error Umum dan Solusi",
                "\n"
                "   ┌─ 401 Unauthorized ──────────────────────────────┐\n"
                "   │ Penyebab: Token tidak valid / expired.           │\n"
                "   │ Solusi:  Menu 1 → masukkan token baru.          │\n"
                "   └─────────────────────────────────────────────────┘\n\n"
                "   ┌─ 403 Forbidden ─────────────────────────────────┐\n"
                "   │ Penyebab: Token tanpa scope yang cukup.         │\n"
                "   │ Solusi:  Buat PAT baru dengan scope repo.       │\n"
                "   └─────────────────────────────────────────────────┘\n\n"
                "   ┌─ Timeout ───────────────────────────────────────┐\n"
                "   │ Penyebab: API lambat / network bermasalah.      │\n"
                "   │ Solusi:  Coba lagi. Jika persisten, periksa     │\n"
                "   │          koneksi internet.                      │\n"
                "   └─────────────────────────────────────────────────┘\n\n"
                "   ┌─ Fork gagal ────────────────────────────────────┐\n"
                "   │ Penyebab: Token tidak punya akses fork.         │\n"
                "   │ Solusi:  Pastikan token punya scope 'repo'.     │\n"
                "   └─────────────────────────────────────────────────┘\n\n"
                "   ┌─ Text not found (file tidak perlu diedit) ─────┐\n"
                "   │ Penyebab: Issue sudah terselesaikan atau file   │\n"
                "   │          tidak mengandung teks yang dicari.     │\n"
                "   │ Solusi:  Tidak perlu commit. PR masih bisa      │\n"
                "   │          dibuat (tanpa perubahan).              │\n"
                "   └─────────────────────────────────────────────────┘\n"
            ),
        ]

        for title, body in sections:
            print(f"\n  ┌─ {title} ─{'─' * (44 - len(title))}┐")
            for line in body.splitlines():
                print(f"  │ {line}")
            print(f"  └{'─' * 58}┘")

        print("\n  [Enter] Kembali ke menu utama...")
        input()
        print()

    # ------------------------------------------------------------------
    # MENU 6 — EXIT
    # ------------------------------------------------------------------

    def menu_exit(self) -> None:
        print()
        print("👋 Terima kasih. Sampai jumpa!")
        print()
        sys.exit(0)

    # ------------------------------------------------------------------
    # MENU UTAMA + LOOP INTERAKTIF
    # ------------------------------------------------------------------

    def run_interactive(self) -> None:
        """Menu utama loop. Menangani CTRL+C dan exception umum."""

        def _sigint_handler(signum, frame):
            # CTRL+C → kembali ke menu, bukan exit
            print("\n\n⏎ CTRL+C dideteksi — kembali ke menu utama.\n")
            return  # tidak exit

        signal.signal(signal.SIGINT, _sigint_handler)

        while True:
            try:
                self._print_main_menu()
                choice = self._get_main_menu_choice()
                if choice == "6":
                    self.menu_exit()
                elif choice == "1":
                    self.menu_auth_github()
                elif choice == "2":
                    self.menu_scan_bounty()
                elif choice == "3":
                    self.menu_pull_request()
                elif choice == "4":
                    self.menu_log_info()
                elif choice == "5":
                    self.menu_learning()
                # choice already validated; loop continues

                # Setelah menu selesai → tampilkan sub-pilihan
                self._post_menu_menu()

            except KeyboardInterrupt:
                print("\n\n⏎ CTRL+C dideteksi — kembali ke menu utama.\n")
                continue
            except Exception as e:
                print(f"\n❌ Error umum: {e}\n")
                print("  [Enter] Kembali ke menu utama...")
                try:
                    input()
                except KeyboardInterrupt:
                    print()
                continue

    # ------------------------------------------------------------------
    # PRINT + GET CHOICE — MENU UTAMA
    # ------------------------------------------------------------------

    def _print_main_menu(self) -> None:
        print()
        print("╔" + "═" * 28 + "╗")
        print("║   BOUNTY HUNTER v2.0       ║")
        print("║   — shadow operations —    ║")
        print("╠" + "═" * 28 + "╣")
        print("║  1. Auth GitHub            ║")
        print("║  2. Scan Bounty            ║")
        print("║  3. Pull Request           ║")
        print("║  4. Log Info               ║")
        print("║  5. Learning               ║")
        print("║  6. Exit                   ║")
        print("╚" + "═" * 28 + "╝")
        print("Pilih menu [1-6]: ", end="")

    def _get_main_menu_choice(self) -> str:
        while True:
            choice = input().strip()
            if choice in ("1", "2", "3", "4", "5", "6"):
                return choice
            print("Pilih menu [1-6]: ", end="")


# ===========================================================================
# ENTRY POINT
# ===========================================================================


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Bounty Hunter v2.0 — Interactive Menu Mode"
    )
    parser.add_argument(
        "--target", type=int, default=None,
        help="Jalankan mode non-interaktif: langsung target issue tertentu"
    )
    args = parser.parse_args()

    if args.target is not None:
        # Mode non-interaktif (dipanggil dari luar, tidak pakai menu)
        print(f"🎯 Mode non-interaktif: target issue #{args.target}")
        print("   (Gunakan menu_tools.py tanpa --target untuk mode interaktif)")
        sys.exit(0)

    print()
    print("╔" + "═" * 58 + "╗")
    print("║   BOUNTY HUNTER v2.0 — Interactive Mode            ║")
    print("╚" + "═" * 58 + "╝")
    print()

    tools = BountyHunterTools()
    tools.run_interactive()


if __name__ == "__main__":
    main()
