# drive-test: destructive suite

**These scripts overwrite the drive. Never point them at a drive that holds data you want to keep** (use [`../read-only/`](../read-only/README.md) for that).

Five-script pipeline for validating new or wiped hard drives: SMART pre-check and badblocks destructive write test (phase 1), ZFS + f3 + SMART long test with a pass/fail verdict (phase 2), an unattended badblocks queue (full four-pattern run, or single-pass via `BB_PATTERN`), a waiter that starts phase 2 only after badblocks passes, and a completion webhook notifier.

## Scripts

### drive-test-phase1.sh

Launches a screen session to run a single drive test action against one drive.

**Requirements:** `screen`, `badblocks` (`apt install e2fsprogs`), `smartmontools` (`apt install smartmontools`)

**Usage:**

```bash
./drive-test-phase1.sh <precheck|badblocks|smart-status|smart-long> <drive>
```

| Argument | Description |
|----------|-------------|
| `precheck` | Print drive identity (model, serial, WWN) and SMART attributes, flag non-zero 5/197/198 raw values; logs to `/tmp/<drive>-precheck.log` |
| `badblocks` | Destructive read-write test (`badblocks -wsv`), logs to `/tmp/<drive>-badblocks.log` |
| `smart-status` | Print SMART attributes and last selftest result |
| `smart-long` | Trigger a SMART extended self-test |
| `<drive>` | Full drive name, e.g. `sdg` |

**Examples:**

```bash
./drive-test-phase1.sh precheck sdg       # screen session: precheck-sdg
./drive-test-phase1.sh badblocks sdg      # screen session: drivetest-sdg
./drive-test-phase1.sh smart-status sdg   # screen session: smart-status-sdg
./drive-test-phase1.sh smart-long sdg     # screen session: smart-long-sdg
```

Attach to the session: `screen -r <session-name>`. Detach: `Ctrl-A d`.

---

### drive-test-phase2.sh

Runs the full phase 2 validation sequence on one drive: ZFS pool creation, f3 write/read, scrub, and SMART long test. All output is logged. A failing step does not abort the run; it is recorded and the script always ends with a verdict line: `PHASE 2 COMPLETE` (all clean) or `PHASE 2 FAILED` (with the reasons listed above it).

**Requirements:** `zfsutils-linux`, `f3` (`apt install f3`), `smartmontools`

**Usage:**

```bash
./drive-test-phase2.sh <serial|drive_letter>
```

`<drive_letter>` is a single letter, e.g. `g` for `/dev/sdg`.

**Example:**

```bash
./drive-test-phase2.sh ABC123XYZ   # by serial (preferred)
./drive-test-phase2.sh g                 # by letter
```

**Steps:**

0. Record drive identity (model, serial, WWN) and pre-test SMART attributes
1. Create ZFS pool `TEST{LETTER}` on the drive (ashift=12, lz4, no dedup, no atime)
2. Export and re-import pool by disk ID
3. `f3write` — fill the mount with test data
4. `f3read` — verify integrity
5. `zpool scrub` — poll every 60s until complete
6. `smartctl -t long` — poll every 5 minutes until complete, then print attributes
7. Judge: fail on SMART 5/197/198 raw value ≠ 0 (before or after), f3read lost/corrupted/changed/overwritten sectors, any ZFS READ/WRITE/CKSUM count or data error, or a SMART long test that didn't finish clean. Checksum-only errors with a clean badblocks/f3 can be a bad cable or HBA — re-seat and retest before binning the drive.

**Log:** `/tmp/<serial>-phase2.log` (by serial) or `/tmp/sd<letter>-phase2.log` (by letter). With a serial the script finds the drive through `/dev/disk/by-id/ata-*_<serial>` and builds the pool on that stable path; it refuses a drive with a mounted filesystem.

---

### queue-badblocks.sh

Waits for one drive's SMART long self-test to finish and pass, then runs the full destructive badblocks run (default four patterns 0xaa, 0x55, 0xff, 0x00, each a write pass plus a read-back pass; days on a multi-TB drive) on it. Set `BB_PATTERN=0xaa` for a single-pattern pass instead. Meant for unattended overnight runs: start one detached screen session per drive and they all start themselves as each drive's self-test completes. **Destroys all data on the drive once the gate passes.**

**Requirements:** `screen`, `badblocks` (`e2fsprogs`), `smartmontools`, passwordless `sudo`

**Usage:**

```bash
./queue-badblocks.sh <serial>
screen -dmS bb-<serial> ./queue-badblocks.sh <serial>
BB_PATTERN=0xaa screen -dmS bb-<serial> ./queue-badblocks.sh <serial>   # single pattern, overnight
```

`<serial>` is the drive's serial number as shown by `smartctl -i`. The script finds the drive through `/dev/disk/by-id/ata-*_<serial>`, so `/dev/sdX` letters changing (controller or cable swap) cannot point it at the wrong disk. It refuses any drive with a mounted filesystem.

**Gate (all must hold, else it logs `SKIPPED` and exits 2):** newest self-test is an Extended one that completed without error, SMART health is PASSED, and SMART attributes 5/197/198 are all 0.

**Result:** the log ends with `BADBLOCKS PASSED` (exit code 0, no bad blocks listed, SMART 5/197/198 still 0) or `BADBLOCKS FAILED`. A single pattern (`BB_PATTERN=0xaa`) keeps a 3-4 TB drive within roughly 10-16 hours; the full run is about four times that.

**Log:** `~/drive-test-logs/<serial>-badblocks.log`

---

### queue-phase2.sh

Waits for one drive's `queue-badblocks.sh` run to reach a verdict, then runs `drive-test-phase2.sh` on it only if badblocks **PASSED**. A `BADBLOCKS FAILED`, `SKIPPED` or `ABORT` line makes it log `SKIPPED phase 2` and exit 2. Start one detached screen session per drive next to its badblocks session. **Destroys all data on the drive once phase 2 starts.**

**Usage:**

```bash
./queue-phase2.sh <serial>
screen -dmS p2-<serial> ./queue-phase2.sh <serial>
```

It polls the badblocks log every 5 minutes, so a stopped or restarted badblocks run just keeps it waiting. When phase 2 ends it copies `/tmp/<serial>-phase2.log` to `~/drive-test-logs/<serial>-phase2.log` and logs the `PHASE 2 COMPLETE`/`FAILED` verdict. Environment overrides for testing: `LOGDIR`, `PHASE2_SCRIPT`, `POLL_SECONDS`.

**Log:** `~/drive-test-logs/<serial>-pipeline.log`

---

### check-complete.sh

Polls phase 2 logs for a set of drives and hits a webhook URL when every drive has a verdict (`COMPLETE` or `FAILED`). Safe to run repeatedly — exits immediately after the first successful webhook call (sentinel file).

**Usage:**

```bash
./check-complete.sh <webhook-url>
```

**Example:**

```bash
./check-complete.sh https://example.com/notify
```

Reads the phase 2 log for each drive in `DRIVES` (default letters `a b c d`; override with `DRIVES="e f g"` or with serials, `DRIVES="<serial> <serial>"`, then `./check-complete.sh <url>`) and looks for `PHASE 2 COMPLETE` or `PHASE 2 FAILED`. The webhook fires on completion regardless of pass/fail; the pass/fail counts are in the check log. When all drives are done, it hits the webhook and writes `/tmp/phase2-notified.sentinel` to prevent duplicate notifications.

**Logs:** `/tmp/check-complete.log`

## Runtime Files

| File | Description |
|------|-------------|
| `/tmp/<drive>-precheck.log` | Identity + SMART attributes from a `precheck` run |
| `~/drive-test-logs/<serial>-pipeline.log`, `~/drive-test-logs/<serial>-phase2.log` | Waiter status and the copied phase 2 log from `queue-phase2.sh` |
| `~/drive-test-logs/<serial>-badblocks.log` | Queue and badblocks output from `queue-badblocks.sh`; ends in `BADBLOCKS PASSED`/`FAILED`/`SKIPPED` |
| `/tmp/<drive>-badblocks.log` | Badblocks output for a phase 1 run |
| `/tmp/sd<letter>-phase2.log`, `/tmp/<serial>-phase2.log` | Full phase 2 output for one drive |
| `/tmp/check-complete.log` | Completion check log |
| `/tmp/phase2-notified.sentinel` | Written after webhook fires; prevents re-notification |
