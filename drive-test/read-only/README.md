# drive-test: read-only suite

Non-destructive checks for drives that hold data: is it healthy, is it still readable, did testing make it worse? **Nothing in this suite writes to a drive.** It reads SMART, asks the drive firmware to run its own self-tests, reads sectors with `badblocks` in its default read-only mode, runs filesystem checkers in report-only mode, and copies files out of filesystems that are already mounted read-only.

For new or wiped drives where you want a destructive burn-in, see [`../destructive/`](../destructive/README.md).

**Requirements:** `smartmontools`, `badblocks` (`e2fsprogs`), `rsync`, `screen` (optional, for long runs), passwordless `sudo` (or run as root with `SUDO=`). Optional, used when present: `ntfs-3g` (`ntfsfix`), `dosfstools` (`fsck.vfat`), `xfsprogs`, `btrfs-progs`, `gddrescue` (`ddrescue`), `curl` (notifier).

## Quick start

```bash
# 1. See what you have (seconds, harmless): serials are the third column of by-id names
ls -l /dev/disk/by-id | grep -v -- -part
./ro-baseline.sh <serial>

# 2. Mount the filesystems you care about READ-ONLY yourself, e.g.
#    sudo mount -o ro,noexec,nodev /dev/sdX1 /mnt/old1

# 3. Run everything, copying the data to a backup location as the main readability test
screen -dmS ro-<serial> ./ro-run.sh <serial> --dest /mnt/backup --long
tail -f ~/drive-test-logs/<serial>-ro.log     # last line is the verdict
```

## What it checks, and what each outcome means

| Step | Script | Reads | Needs | If it can't run |
|------|--------|-------|-------|-----------------|
| SMART baseline | `ro-baseline.sh` | SMART data only | SMART (native or via USB bridge `-d sat`) | Skipped; later steps still run |
| Self-test | `ro-selftest.sh` | drive reads itself (short: minutes, long: hours) | ATA SMART | Skipped (NVMe, no SMART) |
| Filesystem check | `ro-fscheck.sh` | metadata | a checker for that filesystem | Warning: "no read-only checker" |
| Copy + verify | `ro-copy-verify.sh` | every file (or whole partition) | filesystem **already mounted read-only**, and `--dest` | Skipped / warning |
| Surface scan | `ro-surface-scan.sh` | every sector | nothing | n/a |
| Compare | `ro-compare.sh` | SMART + kernel log | a baseline | Skipped |
| Orchestrator | `ro-run.sh` | all of the above | | |
| Notifier | `ro-check-complete.sh` | logs only | `curl`, a webhook URL | n/a |

**Verdict** (last line of `~/drive-test-logs/<serial>-ro.log`): `RO-CHECK PASSED <serial>`, `RO-CHECK WARN <serial>` (nothing failed, but something was skipped or flagged, e.g. a filesystem with no checker, a pre-existing error log), or `RO-CHECK FAILED <serial>` (listed above it). Every script exits 0 passed, 1 failed, 2 warn, 3 skipped.

### Why the order is what it is

1. Cheap, low-stress checks first (SMART, short self-test, filesystem check).
2. **A suspect drive** (failed health, reallocated/pending/uncorrectable sectors, or entries in the ATA error log at baseline) is copied **before** the long self-test and surface scan, so the data is rescued with as little extra wear as possible. With `ddrescue` installed it is imaged partition by partition (with a mapfile) instead of copied file by file.
3. Heavy full-surface reads last. If you copied the data (`--dest`), the surface scan is skipped by default since the used area was already read; a scan adds the unused remainder.
4. The compare step diffs SMART against the baseline: rising reallocated/pending/uncorrectable counters, error-log growth, or kernel I/O errors mean the testing itself exposed a failing drive.

### Conditional behaviour

| Situation | What happens |
|-----------|--------------|
| USB bridge hides SMART | `smartctl -d sat` is tried automatically |
| No SMART at all | SMART-based steps skipped (warn); copy, fscheck and surface scan still run |
| NVMe | Health log and media-error counters are recorded and compared; self-test polling is skipped |
| SSD | Wear/reserve attributes (177, 231, 232, 233) are recorded in the snapshot |
| Drive formatted with 4096-byte sectors but presenting 512 (kernel sees no partitions) | `--loop` attaches a read-only loop device with `--sector-size 4096` for the run (or a loop device you created is picked up automatically) |
| Encrypted or unsupported filesystem (LUKS, APFS, ...) | No checker, so a warning; if **you** unlock and mount it read-only, it is included in the copy. The suite never handles passwords or mounts anything |
| Filesystem mounted read-write | Skipped for fscheck and copy (remount read-only first) |
| xfs / btrfs mounted | Their checkers need an unmounted filesystem: reported as skipped |
| No `--dest` | Copy step skipped, the surface scan covers readability instead |
| Destination on the drive under test, or too small | Copy refuses before reading anything |

## Scripts

| Script | Usage |
|--------|-------|
| `ro-baseline.sh` | `./ro-baseline.sh <serial> [label]`: SMART snapshot to `<serial>-ro-snapshots/<label>.{txt,attrs}`; default label `baseline` is the reference for `ro-compare.sh` |
| `ro-selftest.sh` | `./ro-selftest.sh <serial> [short\|long]`: runs the drive's self-test and polls to completion (`MAX_WAIT_HOURS`, default 24) |
| `ro-surface-scan.sh` | `./ro-surface-scan.sh <serial>`: one full read pass with `badblocks` (read-only mode), bad blocks listed in `<serial>-ro-snapshots/surface-badblocks.txt` |
| `ro-fscheck.sh` | `./ro-fscheck.sh <serial> [--loop]`: `ntfsfix --no-action`, `e2fsck -fn`, `fsck.vfat -n`, `xfs_repair -n`, `btrfs check --readonly` |
| `ro-copy-verify.sh` | `./ro-copy-verify.sh <serial> <dest-dir> [--checksum] [--loop]`: rsync (or ddrescue for suspect drives) to `<dest>/<serial>/`, then verify; `--checksum` re-reads both sides in full |
| `ro-compare.sh` | `./ro-compare.sh <serial>`: takes an `after` snapshot, diffs against `baseline`, scans the kernel log since the baseline |
| `ro-run.sh` | `./ro-run.sh <serial> [--dest DIR] [--long] [--checksum] [--loop] [--no-surface]` |
| `ro-check-complete.sh` | `SERIALS="<serial> <serial>" ./ro-check-complete.sh <webhook-url>`: webhook once every listed drive has a verdict |
| `lib.sh` | Shared helpers (sourced, not run) |

Environment overrides: `LOGDIR` (default `~/drive-test-logs`), `SUDO` (default `sudo`; empty when already root), `POLL_SECONDS` (default 60), `MAX_WAIT_HOURS`, `BYID_DIR` (default `/dev/disk/by-id`).

## Reading the results

- A single drive can score `WARN` forever if it simply has old error-log entries; compare the numbers in `<serial>-ro-snapshots/baseline.attrs` before and after, since only **changes** during the run are treated as failures.
- A rising `attr_199` (interface CRC errors) usually means a bad cable or port rather than a bad disk: reseat, move the port, and re-run before condemning the drive.
- A clean run proves the drive was readable now, not that it will last. Keep the copy.

## Tests

```bash
./tests/run-tests.sh
```

Runs every script against stub `smartctl`, `lsblk`, `badblocks`, etc. in `tests/stubs/` and a temporary directory (real `rsync` between two temp directories). No real drive, no root. Also greps the suite for write-capable commands.

```bash
sudo ./tests/prove-readonly.sh
```

Opt-in proof that the suite writes nothing, with the real tools: builds a 2 GiB sparse image (NTFS + ext4), attaches it as a loop device, runs the suite, and checks the image SHA-256 and the kernel's sectors-written counter are unchanged. It then repeats the run with the device locked by `blockdev --setro`, where any write would be refused. A control write first proves both detectors can see a change. Needs root, `ntfs-3g`, `sfdisk`, `rsync`, `e2fsprogs`; touches no real drive. The overall verdict there is `WARN` because a loop device has no SMART.

To check your own drives the same way, lock them first (`sudo blockdev --setro /dev/sdX`) and compare `/sys/block/sdX/stat` (field 7, sectors written) before and after a run.

## Runtime files

| File | Description |
|------|-------------|
| `~/drive-test-logs/<serial>-ro.log` | Combined log of every step; the last line is the verdict |
| `~/drive-test-logs/<serial>-ro-snapshots/baseline.{txt,attrs}`, `after.{txt,attrs}` | SMART snapshots (full `smartctl -x` and the key=value summary), `started` (epoch of the baseline) |
| `~/drive-test-logs/<serial>-ro-snapshots/surface-badblocks.txt` | Unreadable block numbers from the surface scan (empty when clean) |
| `<dest>/<serial>/...` | Copied files, or `*.img` + `*.img.map` ddrescue images, when `--dest` is used |
| `~/drive-test-logs/ro-check-complete.log`, `ro-notified.sentinel` | Notifier log and its fire-once marker |
