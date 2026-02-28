# Scripts

@README.md

Script-specific architecture details are in `.claude/rules/`, loaded automatically when editing matching files:

| Rule File | Glob | Content |
|-----------|------|---------|
| `check-connectivity.md` | `check_connectivity*` | Code structure, connectivity checks, notifications, WHOIS/ISP, CSV resilience, config design, runtime artifacts, constraints |

## Commit Workflow Overrides

These fill the placeholder slots in the root commit workflow.

**Step 2 — Build and verify:** No build step. For Python scripts, verify with `python -m py_compile <script>.py` to catch syntax errors.

**Step 10 — Deploy:** No automated deploy. Scripts are manually deployed to their target servers.
