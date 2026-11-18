# Fresh recordings: the first pair was not used (2026-11-18)

Written 2026-11-18, before any model scored a fresh recording. Protocol: `protocol_run13.md`,
step 4.

## What happened

The Lead Architect made the two step-4 recordings on PC A, from an administrator PowerShell
window, as the instructions in `score_fresh.py` said. The files were written at 14:57 and 15:00.
`capture_browsing.py` printed 48 and 47 page loads, none timed out.

Both files were far smaller than every earlier recording made with the same command. So they were
checked before scoring, with `check_recordings.py`, which prints counts only.

| Recording | Packets | HTTPS connections | QUIC packets | MB on port 443 | TLS hellos naming a test site |
|---|---|---|---|---|---|
| `run13/fresh_generator.pcap` (administrator window) | 1,329 | 6 | 342 | 0.64 | 0 |
| `run13/fresh_unseen.pcap` (administrator window) | 1,961 | 25 | 319 | 1.48 | 0 |
| `def19/browser.pcap` (same command) | 47,557 | 206 | 35,944 | 53.26 | 22 |
| `def19/final_browser.pcap` (same command) | 53,217 | 185 | 40,425 | 58.44 | 19 |
| `def19/final_unseen.pcap` (same command, `--sites unseen`) | 41,536 | 343 | 23,024 | 51.27 | 46 |
| 15-second trial, normal window, same day (deleted) | 19,099 | 70 | 14,848 | 30.50 | 6 |

## Cause

- **Chrome did not browse.**
  - No TLS hello in either file names a test site.
  - Their traffic on port 443 is 0.6 and 1.5 MB. That is far less than 47 or 48 page loads need.
  - 48 runs in 120 seconds is what the script's pauses of 1–4 seconds give on their own. So each
    Chrome run ended at once.
- **The script counted every Chrome run as a page load.** It threw away Chrome's output.
- **A normal window works.**
  - The same Chrome command, from a normal window on the same day, returned each page: 3 of 3,
    taking 3–14 s each.
  - The 15-second trial above recorded real page loads.
  - Npcap on PC A lets any user capture (`AdminOnly` = 0), so no administrator window is needed.
- **Not investigated further:** why Chrome ends at once when started from an administrator
  window. A Chrome update was waiting to be applied: 157.0.8103.51 was running and 157.0.8103.79
  was staged.

## Decision

- **The pair is recorded again.** It is not the recording step 4 describes: Chrome loading the
  sites while all of the PC's traffic is recorded. Step 4 does the same with a recording that
  has too few flows.
- **Neither file was scored by any model.** Both are kept, outside git, in
  `research/dataset/processed/run13/not_used_admin_window/`.
- **`capture_browsing.py` now counts real page loads.**
  - A page load counts only when Chrome returned the page.
  - The script reports the runs that returned no page.
  - It is run from a normal window.
- **When a new recording is used:**
  - only if the script reports no run that returned no page;
  - `check_recordings.py` is run on it before scoring, and its counts are added below.

## Second pair

The Lead Architect recorded the second pair from a normal PowerShell window. The files were
written at 15:40 and 15:42.

| Recording | Script's last line | Packets | HTTPS connections | QUIC packets | MB on port 443 | TLS hellos naming a test site |
|---|---|---|---|---|---|---|
| `run13/fresh_generator.pcap` | 16 page loads (2 returned no page, 0 timed out) | 32,373 | 211 | 19,299 | 39.91 | 17 |
| `run13/fresh_unseen.pcap` | 19 page loads (0 returned no page, 0 timed out) | 33,494 | 359 | 18,542 | 55.67 | 53 |

**The rule under "Decision" was too strict. It is corrected here, before either file was scored.**

- **The two runs that returned no page are the two visits to `www.gnu.org`.**
  - The site order is fixed (seed 19), and its 18 runs visit gnu.org at runs 10 and 16.
  - That order was replayed from a normal window without recording. Chrome reported
    `net::ERR_CONNECTION_TIMED_OUT` for gnu.org, and every other site loaded.
- **www.gnu.org does not answer from PC A.**
  - `check_recordings.py` reports it as tried but never answered in this file.
  - It reports the same for `def19/generator.pcap`, `def19/browser.pcap` and
    `def19/final_browser.pcap`. Chrome's attempts come in two unanswered bursts at the same points
    of the order: 44–59 s and 90–106 s here; 33–53 s and 85–100 s in `browser.pcap`.
  - On the same day, curl also timed out on it twice.
- **So every generator recording on PC A has two runs that return no page.**
  - The script before this change counted them as page loads.
  - Under the rule above, no generator recording could be used. That includes the selection
    recordings that the protocol depends on.
- **Corrected rule:**
  - A recording is not used when Chrome did not browse, that is, its runs end at once without a
    page, as in the first pair.
  - A site that does not answer is reported, as timeouts always were.
- **Both files are used.** Their traffic matches the earlier recordings made with the same
  command (first table).
