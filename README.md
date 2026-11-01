# 🏆 Bounty Hunter — README

Tool otomatis untuk mencari bounty GitHub, menilai kelayakannya dengan AI, dan menyiapkan draft perbaikan.

## Struktur File

```
bounty_hunter/
├── bounty_hunter.py    # Main script (Python)
├── repos.txt           # Daftar target repo (owner/repo format)
└── bounty_hunter.log   # Hasil log (otomatis dibuat)
```

## Cara Kerja

1. **Load Config** → Membaca model AI aktif dari `~/.hermes/config.yaml`
2. **Scan Bounties** → Mencari issue dengan label `bounty`, `help wanted`, `good first issue`
3. **Filter** → Buang yang sudah punya >3 PR atau repo tidak aktif >30 hari
4. **Score** → AI menilai setiap bounty 1-10 berdasarkan difficulty, clarity, acceptance chance
5. **Pick Best** → Memilih bounty dengan skor tertinggi
6. **Work** → AI menganalisis issue dan membuat draft perbaikan
7. **Notify** → Menampilkan ringkasan dan menyimpan ke log

## Prasyarat

- Python 3.10+
- `gh` CLI terinstal dan sudah login (`gh auth status`)
- Hermes Agent terinstal dan sudah login (`hermes status`)
- Repositori target sudah ada di `repos.txt`

## Cara Menjalankan

### 1. Edit repos.txt

Buka file `repos.txt` dan tambahkan repo target:

```
Elqudsy/memind
Elqudsy/cipher-line
```

### 2. Jalankan (Full Workflow)

```bash
python bounty_hunter.py
```

### 3. Jalankan (Dry Run — Scan + Score Only)

```bash
python bounty_hunter.py --dry-run
```

### 4. Jalankan (Custom Repos File + Output Log)

```bash
python bounty_hunter.py --repos my_repos.txt --output my_log.txt
```

### 5. Jalankan (Tanpa Filter)

```bash
python bounty_hunter.py --no-filter
```

### 6. Gabungkan Semua Opsi

```bash
python bounty_hunter.py --repos repos.txt --output results.log --no-filter --dry-run
```

## Argumen CLI

| Flag | Deskripsi | Default |
|------|-----------|---------|
| `--repos FILE` | Path ke file repos | `./repos.txt` |
| `--dry-run` | Skip work_on_bounty | False |
| `--output FILE` | Nama file log | `bounty_hunter.log` |
| `--no-filter` | Skip filter step | False |

## Fitur Keamanan

- ❌ **Tidak auto-submit PR** ke GitHub
- ❌ **Tidak auto-transfer uang/kripto**
- ✅ Semua tindakan GitHub harus dikonfirmasi manual
- ✅ Draft perbaikan ditampilkan, bukan dikirim

## Config Model

Tool ini otomatis membaca model dari `~/.hermes/config.yaml`. Ganti model di Hermes (via `hermes model`), dan tool ini langsung mengikuti tanpa perlu edit kode.

## Output Contoh

```
🏆 BOUNTY HUNTER REPORT
Model: inclusionai/ling-3.0-flash-fin:free (nous)

🏆 SELECTED: #42 (Elqudsy/memind)
Score: 8/10
Title: Fix file upload on Android

AI ANALYSIS:
[Analysis from AI...]

RECOMMENDED NEXT STEPS:
1. Review the analysis above
2. Implement the fix on your own branch
3. Test thoroughly
4. Open a PR manually
```

## File Log

Semua hasil disimpan ke `bounty_hunter.log` untuk review nanti. Format:
- Timestamp
- Model yang digunakan
- Detail setiap bounty
- AI analysis
- Recommended next steps

## Troubleshooting

### "No repos found"
- Pastikan `repos.txt` ada dan berisi repo yang valid
- Gunakan `--repos path/to/file.txt` untuk menunjuk file lain

### "gh: command not found"
- Install `gh` CLI: `sudo apt install gh` (Ubuntu/Debian)
- Login: `gh auth login`

### "hermes: command not found"
- Pastikan Hermes terinstal di PATH
- Cek: `which hermes`

### Model tidak berubah
- Tool membaca `~/.hermes/config.yaml`
- Ganti model: `hermes model` atau edit config.yaml secara manual
- Restart tool setelah mengganti model

## Lisensi

MIT — Gratis digunakan untuk tujuan apapun.
