---
name: build-gpcat
description: Build an encrypted .gpcat catalog bundle from a configs source repo (default casino-buddy-internal). Stages a clean tree, runs tools/build_gpcat, prints the {license, bundle_id, sha256} tuple, and optionally installs it locally to the catalog/ directory for end-to-end verification.
---

# /build-gpcat — operator-side catalog packager

Wraps the manual recipe documented in `tools/build_gpcat.py` into a
single repeatable skill: stage a clean catalog tree, build the encrypted
`.gpcat`, surface the sales credentials, and (optionally) install
locally for verification.

**Scope:** This skill is for the gamba_pick operator (you), not for
customers. It assumes you have access to a configs source repo
(default: `~/src/casino-buddy-internal`) with a `configs/` subdir of
Python site files plus a `sites_seed.toml` at the source root.

## When to invoke

- User types `/build-gpcat` (or `/gamba_pick:build-gpcat`)
- User says "build a gpcat" / "make a fresh bundle" / "package the
  catalog" / "ship a build for <customer>"
- A new customer needs a bundle, or an existing customer needs an
  updated bundle (bug fixes / new sites / revoked old key)

## When NOT to invoke

- Inside `casino-buddy-internal` (the source repo) — wrong cwd; this
  skill runs from the gamba_pick repo because that's where the
  builder + the install target live
- User asked for a *plan* of what a build would produce — answer
  conversationally; do not actually run the build
- User wants to add a NEW site to the bundle — that's a casino-buddy
  edit, not a build. Build comes after.

## Inputs (skill arguments)

All optional, sensible defaults documented inline:

- `--source-dir <path>` — configs source repo. Default:
  `/home/lothrop/src/casino-buddy-internal`. Must contain `configs/`
  and `sites_seed.toml` at the top level.
- `--output <path>` — where to write the `.gpcat`. Default:
  `./build/casino-buddy-<YYYYMMDD-HHMMSS>.gpcat` (relative to gamba_pick
  repo root).
- `--license <STRING>` — explicit license string. Default: auto-generate
  via `tools.build_gpcat`'s Crockford-base32 generator.
- `--bundle-id-hex <32hex>` — explicit bundle_id for idempotent rebuilds.
  Default: auto-generate.
- `--customer <name>` — customer name for the sales record line. Default:
  `unspecified`. Skill prompts for this if it's a real customer build.
- `--install-local` — after build, copy the `.gpcat` to `./catalog/`,
  write the license to `.env`, and run `./run.sh --show` to verify
  end-to-end decryption. Off by default. Use for first-customer setup
  testing or for sanity-checking a fresh build before shipping.

If invoked without arguments, ask the user about `--customer` (and,
if they're shipping to anyone real, confirm whether they want
`--install-local` for verification).

## Steps

### 1. Pre-flight checks

Run in parallel:

- `pwd` — must be `/home/lothrop/src/gamba_pick` (or the gamba_pick repo
  root); if not, instruct the user to `cd` there first.
- `test -d <source-dir>/configs && test -f <source-dir>/sites_seed.toml` —
  the source has the expected layout.
- `test -f tools/build_gpcat.py` — the builder is here.

If any pre-flight fails, surface a clear error and stop. Don't try to
recover by guessing at paths.

### 2. Stage a clean catalog tree

```sh
STAGE=/tmp/gpcat-stage-$(date +%Y%m%d-%H%M%S)
mkdir -p "$STAGE"
cp -r <source-dir>/configs "$STAGE/"
cp <source-dir>/sites_seed.toml "$STAGE/"
rm -rf "$STAGE/configs/__pycache__"
cat > "$STAGE/manifest.toml" <<EOF
[meta]
version = "1"
build_date = "$(date -I)"
source = "$(basename <source-dir>)"
customer = "<customer-or-unspecified>"
EOF
```

Why a tempdir: the staging tree gets the `manifest.toml` *we* generate
plus the source repo's `configs/` and `sites_seed.toml`. Building
in-place would pollute the source repo with a `manifest.toml` that
shouldn't be tracked.

Always strip `__pycache__` from configs/. The source repo doesn't
gitignore it well, and a 28 KB bundle balloons to 100+ KB if pyc files
sneak in.

### 3. Build

```sh
mkdir -p ./build
uv run python -m tools.build_gpcat \
    --catalog-dir "$STAGE" \
    --output "<output-path>" \
    [--license "<license>"] \
    [--bundle-id-hex "<bundle-id>"]
```

Capture stdout — it contains the credentials and sha256.

### 4. Surface credentials + sales record

Print clearly (these are the values to store):

```
=== BUILD COMPLETE ===

  output         : <output-path>
  size           : <bytes>
  license_string : XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
  bundle_id_hex  : <32-hex>
  sha256         : <64-hex>
  customer       : <customer-name-or-unspecified>
  build_date     : <YYYY-MM-DD>

→ Email the customer the .gpcat file + license_string.
→ Add this line to your sales record:
    <customer>,<build_date>,<license>,<bundle_id>,<sha256>
```

The sales record is the operator's responsibility to maintain — the
skill doesn't auto-write because the format/location is the user's
preference. If the user wants a default, suggest a CSV at
`~/gamba-pick-sales.csv` and offer to append.

### 5. Optional: install locally for verification

Only when `--install-local` is set. Steps:

```sh
# Copy bundle into catalog/ (create if missing).
mkdir -p catalog
# If catalog/ already has a .gpcat, ask the user before overwriting —
# multi-bundle catalogs are an error path the framework rejects.
cp "<output-path>" ./catalog/
# Write the license via the framework's own helper to keep .env
# clean (preserves any existing credentials in there).
uv run python -c "
from pathlib import Path
from gamba_pick.license_store import write_license
write_license(Path('.env'), '<license>')
"
# Verify decryption + site discovery without running anything live.
./run.sh --show
./run.sh --list
```

Confirm the `--show` output shows `Catalog: present (decrypted to
/tmp/gamba-pick-cat-<random>/configs)` (note the `/configs` suffix —
the cli pivots to that subdir for module lookup).

### 6. Cleanup notes

The stage dir at `/tmp/gpcat-stage-*` is throwaway — the skill can
delete it after a successful build, or leave it for the user to clean
up. Default: leave it (cheap, easy to grep for build context later).

The decrypted tempdir at `/tmp/gamba-pick-cat-*` from `--install-local`
gets cleaned by the cli's atexit handler when `./run.sh --show` exits.

## Confirmation gate

Before running step 3 (the actual build), show the user:

```
Source : <source-dir>
Output : <output-path>
Customer : <customer>
License : <auto-generated | provided>

Sites in bundle (from configs/):
  - americanluck.py
  - fortunewins.py
  - ...

Looks good? (y/n, or override anything)
```

The build itself is reversible (just delete the file), but the
license_string in the output is one-shot — once printed, the operator
needs to track it before re-running. Confirmation prevents accidentally
generating a build with the wrong customer attribution baked into the
manifest.

## Don't

- Don't reuse a license across customers. Crypto derives the AES key
  from `(license, bundle_id)` via HKDF — sharing a license = sharing
  the bundle key. Each new build for a new customer auto-generates
  a fresh license.
- Don't commit the `build/*.gpcat` outputs. They contain encrypted
  paid configs. `.gitignore` should already cover `build/`; if it
  doesn't, add it before the first build runs.
- Don't run with `--install-local` if `.env` already has a
  different `GAMBA_PICK_LICENSE`. The framework's `write_license`
  *does* preserve other env-var lines but it overwrites the existing
  license line. Warn the user first if a different license is already
  set; they may want to keep both bundles installable.
- Don't bundle `.gitignore`, `.git/`, `__pycache__/`, `*.pyc`, or
  `README.md` from the source repo. Only `configs/` (cleaned of
  pyc) and `sites_seed.toml`. The bundle is a runtime payload, not
  a repo snapshot.
- Don't run the build from inside `casino-buddy-internal`. The
  builder lives in gamba_pick (`tools/build_gpcat.py`); cwd matters
  for the `uv run` and the relative `./build/` output path.

## Edge cases

- **No `--customer` provided** and the build looks like a real
  shipment (e.g. `--install-local` not set): prompt for the customer
  name before building. Bundles with `customer = "unspecified"` in
  the manifest are easy to lose track of.
- **Existing `.gpcat` in `./catalog/`** when `--install-local` is set:
  the framework rejects multi-`.gpcat` catalog dirs (`CatalogState.MULTI`
  → user-facing error). Ask whether to (a) replace the existing one,
  (b) abort and let the user clean up first.
- **Build fails partway through** (uv complaint, missing dependency,
  bad source layout): leave the staging dir in place for inspection,
  do NOT delete partial output. Surface the underlying error verbatim.
- **Source repo has uncommitted changes**: warn the user — they may
  not want a build that pulls in WIP. Don't refuse to build, but make
  the warning visible.
- **`__pycache__` survived the strip**: shouldn't happen with
  `rm -rf "$STAGE/configs/__pycache__"`, but if `du -sh "$STAGE"`
  comes back >200 KB for a 12-site bundle, something else snuck in.
  List the stage tree and figure out what before building.
