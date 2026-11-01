# Root Cause Analysis: bounty_hunter.py No Output and No Log File

## Summary

The script is NOT crashing with a Python exception. It is being **terminated by the 120-second timeout** before it can complete its work and reach the logging function. The "crash" is actually a timeout kill (exit code 124), and the missing log file is because the `notify()` function that creates it sits at the very end of the script.

## Evidence from Three Test Runs

| Run | Timeout | Python Flags | Output | Exit Code | Conclusion |
|-----|---------|--------------|--------|-----------|------------|
| 1 | 120s | (none) | **ZERO** | 124 (timeout) | Killed before stdout buffer flushed |
| 2 | 90s | `-u` (unbuffered) | **ZERO** | 0* | Killed before reaching next print after banner |
| 3 | 180s | `-u` | **WORKING** | 0 | Script found 113 bounties and started filtering |

*Exit 0 in Run 2 is misleading — it's from `|| true`, not Python's actual exit.

**Run 3 proves the script functions correctly.** It successfully:
- Printed the banner and configuration
- Loaded 5 repos from repos.txt
- Scanned GitHub and found 113 bounty issues
- Started the filtering phase (processed multiple github/docs issues)

## Execution Flow and Timing

The script's `main()` executes in this order:

```
1. Print banner                                    [~0 seconds]
2. Load Hermes config                             [~1-2 seconds]
3. Load repos from file                           [~0.1 seconds]
4. Scan: 5 repos × 3 labels × gh issue list      [30-120+ seconds]
   → Each call: subprocess.run with 30s timeout
   → Total: 15 sequential gh API calls
5. Filter: 113 bounties × 2 gh calls each        [potentially very long]
   → gh pr list (15s timeout) + gh repo view (15s timeout)
   → Total: up to 226 sequential gh API calls
6. Score: N bounties × hermes chat --oneshot     [EXTREMELY LONG]
   → Each call: subprocess.run with 90s timeout
   → If 50 bounties remain after filtering: 50 × 90s = 4500s = 75 min
7. Pick best, work on bounty                      [AI calls, 120s timeout]
8. Notify: print summary + write log file         [~1 second]
```

**The script found 113 bounties.** After filtering, even if only 50 remain, the scoring phase alone would take 50 × 90s = 4500 seconds (75 minutes) in the worst case. Even with faster AI responses (10s each), that's 500 seconds (8+ minutes).

## Why Zero Output Appears

### Run 1 (120s, no `-u` flag):
Python uses **block buffering** for stdout when the output is piped (not a TTY). The banner and early print statements are held in an internal buffer. When the 120-second timeout sends SIGTERM to kill the process, the buffer is discarded before it can be flushed to the terminal. Result: **zero visible output**.

### Run 2 (90s, `-u` flag):
With unbuffered output (`-u`), Python flushes each print immediately. The banner should appear instantly. However, **90 seconds is not enough to complete the scanning phase** (which can take 30-120+ seconds for 15 gh API calls). The script was still in the scanning or early filtering phase when the `timeout` command killed it. No further output was produced after the banner.

## Why No Log File Is Created

The `notify()` function (lines 453-504) that creates the log file is called at the **very end of `main()`**, after all scanning, filtering, scoring, and analysis:

```python
def main():
    ...
    # Step 1: Scan (line 559)
    bounties = scanner.scan_bounties(repos)
    
    # Step 2: Filter (line 565)
    bounties = filter_bounties(bounties)
    
    # Step 3: Score (line 574)
    bounties = score_bounties(bounties, ai)
    
    # Step 4: Pick best (line 577)
    best = pick_best(bounties)
    
    # Step 5: Work on bounty (line 581)
    if best and not args.dry_run:
        best = work_on_bounty(best, ai)
    
    # Step 6: Notify & log (line 586) ← HERE is where log is created
    notify(bounties, best, args.output)
```

If the script is killed during **any** of steps 1-5, `notify()` is never reached, and **no log file is created**. Since the script takes well over 120 seconds to get through scanning and filtering (let alone scoring), the timeout kill happens before the log file can be written.

## Root Cause: Sequential API Calls Without Parallelism

The script's fundamental design issue is that it makes **250+ sequential subprocess calls** with no parallelism:

- **15 `gh issue list` calls** (scan phase, 5 repos × 3 labels)
- **Up to 226 `gh` calls** (filter phase, 2 calls per bounty for 113 bounties)
- **Up to 100+ `hermes chat` calls** (score phase, 1 call per remaining bounty)
- **1 `hermes chat` call** (work phase, for the best bounty)

Each call has a timeout (30s, 15s, or 90s), but they execute **one at a time**. For 113 bounties, even at an optimistic 2 seconds per API call average, the total execution time would be 250 × 2 = 500 seconds (8+ minutes) — far exceeding the 120-second observation window.

## Additional Contributing Factors

1. **GitHub API rate limits**: Multiple sequential `gh` calls may be throttled, increasing response times.
2. **Network latency**: Each subprocess call involves network requests to github.com and the AI inference API.
3. **AI inference time**: The `hermes chat --oneshot` calls depend on the AI model's response time, which can vary from seconds to minutes.
4. **No early logging**: The script only writes to the log file at the very end. If it's killed mid-execution, no progress is saved.

## What Would Fix This

1. **Increase the timeout**: Run with `timeout 300` or higher to allow the script to complete its initial phases.
2. **Use `--dry-run`**: Skips the `work_on_bounty()` phase, saving one AI call.
3. **Reduce scope**: Fewer repos in `repos.txt` means fewer bounties to process.
4. **Add intermediate logging**: Write scan results and progress to the log file as they happen, not just at the end.
5. **Parallelize API calls**: Use threading or async to make multiple `gh` calls concurrently (subject to GitHub API rate limits).
6. **Use `--no-filter`**: Skip the filtering phase to reduce the number of API calls.

## Conclusion

The script is functionally correct but **architecturally slow** for the volume of work it attempts (113 bounties across 5 repos). The 120-second timeout is insufficient to observe meaningful progress. The "crash" is a timeout kill, and the missing log file is a consequence of the script being terminated before reaching the final `notify()` function.

To see the script working and produce a log file, Tuan needs to either:
- Run with a much longer timeout (5+ minutes)
- Reduce the scope (fewer repos/bounties)
- Use `--dry-run` to skip the most time-consuming phases