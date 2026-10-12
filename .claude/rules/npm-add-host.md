# npm-add-host Architecture

## Code Structure

`npm-add-host/npm_add_host.py` is stdlib-only (urllib, argparse), top-to-bottom:

| Section | Contents |
|---------|----------|
| Configuration | `load_dotenv()`, `env_bool()`, `parse_aliases()`, `load_config()`, `validate_config()` |
| NPM API | `NpmClient` (JWT login, JSON requests, error unwrapping), `NpmError` |
| Workflow | `normalize_fqdn()`, `find_access_list()`, `find_existing_cert()`, `add_host()`, `find_deletable_host()` |
| CLI | `build_parser()` (subcommands `add`, `delete`, `show-config`), `run_add()`, `run_delete()`, `main()` |

## Design Decisions

- **Per-host HTTP-01 certificates, not a wildcard.** NPM's DNS-challenge path pins `certbot-dns-ispconfig~=0.2.0` (checked through NPM 2.16.0), which sends an empty `stamp` that ISPConfig 3.3.1p1's strict MySQL rejects; plugin 0.3.1 fixes it but NPM reinstalls 0.2.x on every request. HTTP-01 needs only a wildcard DNS record already pointing at NPM.
- **Cert first, then host.** `add_host()` requests the certificate, then creates the proxy host with its `certificate_id`. If host creation fails, a certificate created in this run is deleted (a reused one never is) so retries don't burn Let's Encrypt rate limits.
- **`meta.managed_by = npm-add-host` marks created hosts.** `delete` refuses hosts without it unless `--force`, because the same NPM holds many hand-made hosts. NPM preserves unknown `meta` keys (verified live on 2.12.3).
- **Fail closed on access.** With no `NPM_ACCESS_LIST` and no `--access-list`, `add` refuses unless `--public`. Aliases (`NPM_ACCESS_LIST_ALIASES`, `name=id`) exist because NPM does not enforce unique access-list names; an alias is resolved before the NPM name lookup, and an ambiguous plain name is an error.
- **`delete` drops the certificate only when** it is a Let's Encrypt cert for exactly that hostname and no other proxy host references it. Hosts serving several names are refused.
- **`add` and `delete` take a full FQDN** (at least two valid DNS labels); there is no base-domain setting, so any hostname that already resolves to NPM works.
- **Env search order:** `--env-file`, else `/etc/npm-add-host/env`, then `.env` next to the script; real environment variables always win (`load_dotenv()` never overrides).

## Gotchas

- Certificate issuance runs certbot inside the NPM request and can take close to a minute; `CERT_TIMEOUT` is 180 s, other calls 30 s.
- The NPM API is not formally versioned; the request bodies were taken from the instance's own `/api/schema` and tested live on 2.12.3. Re-test after an NPM upgrade.
- The script has no automated tests; verification is a live add, HTTPS/redirect/auth check, and delete against a throwaway hostname.
