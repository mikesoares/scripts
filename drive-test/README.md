# drive-test

Three-script pipeline for validating used/new hard drives: badblocks destructive write test (phase 1), ZFS + f3 + SMART long test (phase 2), and a completion webhook notifier.

## Scripts

### drive-test-phase1.sh

Launches a screen session to run a single drive test action against one drive.

**Requirements:** `screen`, `badblocks` (`apt install e2fsprogs`), `smartmontools` (`apt install smartmontools`)

**Usage:**

```bash
./drive-test-phase1.sh <badblocks|smart-status|smart-long> <drive>
```

| Argument | Description |
|----------|-------------|
| `badblocks` | Destructive read-write test (`badblocks -wsv`), logs to `/tmp/<drive>-badblocks.log` |
| `smart-status` | Print SMART attributes and last selftest result |
| `smart-long` | Trigger a SMART extended self-test |
| `<drive>` | Full drive name, e.g. `sdg` |

**Examples:**

```bash
./drive-test-phase1.sh badblocks sdg      # screen session: drivetest-sdg
./drive-test-phase1.sh smart-status sdg   # screen session: smart-status-sdg
./drive-test-phase1.sh smart-long sdg     # screen session: smart-long-sdg
```

Attach to the session: `screen -r <session-name>`. Detach: `Ctrl-A d`.

---

### drive-test-phase2.sh

Runs the full phase 2 validation sequence on one drive: ZFS pool creation, f3 write/read, scrub, and SMART long test. All output is logged. Uses `set -e` — aborts on any failure.

**Requirements:** `zfsutils-linux`, `f3` (`apt install f3`), `smartmontools`

**Usage:**

```bash
./drive-test-phase2.sh <drive_letter>
```

`<drive_letter>` is a single letter, e.g. `g` for `/dev/sdg`.

**Example:**

```bash
./drive-test-phase2.sh g
```

**Steps:**

1. Create ZFS pool `TEST{LETTER}` on the drive (ashift=12, lz4, no dedup, no atime)
2. Export and re-import pool by disk ID
3. `f3write` — fill the mount with test data
4. `f3read` — verify integrity
5. `zpool scrub` — poll every 60s until complete
6. `smartctl -t long` — poll every 5 minutes until complete, then print attributes

**Log:** `/tmp/sd<letter>-phase2.log`

---

### check-complete.sh

Polls phase 2 logs for a set of drives and hits a webhook URL when all are done. Safe to run repeatedly — exits immediately after the first successful webhook call (sentinel file).

**Usage:**

```bash
./check-complete.sh <webhook-url>
```

**Example:**

```bash
./check-complete.sh https://example.com/notify
```

Reads `/tmp/sd{a,b,c,d}-phase2.log` for the `PHASE 2 COMPLETE` marker. When all 4 drives are done, POSTs to the webhook and writes `/tmp/phase2-notified.sentinel` to prevent duplicate notifications.

**Logs:** `/tmp/check-complete.log`

## Runtime Files

| File | Description |
|------|-------------|
| `/tmp/<drive>-badblocks.log` | Badblocks output for a phase 1 run |
| `/tmp/sd<letter>-phase2.log` | Full phase 2 output for one drive |
| `/tmp/check-complete.log` | Completion check log |
| `/tmp/phase2-notified.sentinel` | Written after webhook fires; prevents re-notification |
