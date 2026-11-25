# drive-test

Two independent suites for checking hard drives and SSDs, kept apart on purpose so the wrong one is never run on a drive that holds data.

| Suite | Writes to the drive? | Use it when |
|-------|----------------------|-------------|
| [`destructive/`](destructive/README.md) | **Yes. Overwrites everything.** | The drive is new, or empty/wiped, and you want a full burn-in (badblocks write patterns, ZFS + f3, SMART). |
| [`read-only/`](read-only/README.md) | **Never.** | The drive holds data you want to keep: old or long-unpowered drives, drives pulled from another machine, "does this still work?" checks. |

If in doubt, use `read-only/`. Both suites identify drives by **serial number** through `/dev/disk/by-id` (device letters like `sdb` can change between boots or after a cable swap) and write their logs under `~/drive-test-logs/`.

## Layout

```
drive-test/
├── README.md        # This file
├── destructive/     # Burn-in pipeline (5 scripts), destroys data
└── read-only/       # Non-destructive health checks (8 scripts + lib.sh + stub tests)
```
