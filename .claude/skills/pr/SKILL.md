---
name: pr
description: Open a PR for the current gamba_pick branch — classifies the diff against the project's tier rules, proposes a pyproject.toml version bump as a final commit, and drafts title + body using actual conversation context for the "why."
---

# /pr — gamba_pick PR composer

Project-aware PR creation for the **public gamba_pick repo**. Reads
the current branch's diff, classifies the highest semver tier across
the changes per the rules in `project_versioning_policy.md`, proposes
a version bump as a final pre-merge commit, and drafts the PR body
using context from the current conversation rather than just diff
descriptions.

**Scope:** This skill is for the `gamba_pick` public repo only. The
private `casino-buddy-internal` configs repo has different rules
(no semver pressure, no shared API to break) — don't run this there.

## When to invoke

- User types `/pr` (or `/gamba_pick:pr`)
- User says "open the PR" / "draft the PR" / "we're ready to merge"
- A working branch has commits ready to land on master and the user
  signals they're done iterating

## When NOT to invoke

- Branch is `master` (nothing to PR)
- No commits ahead of master (nothing to land)
- User asked for a *plan* of what would go in a PR — that's
  conversational, don't actually call `gh pr create`
- We're inside `casino-buddy-internal` — out of scope

## Steps

Run steps 1–3 in parallel — they're independent reads:

1. `git status` — confirm clean working tree (or check for
   uncommitted changes that the user might want included).
2. `git rev-parse --abbrev-ref HEAD` — current branch.
3. `git log master..HEAD --oneline` and
   `git diff master..HEAD --name-only` — what's in the PR.

Then sequentially:

4. **Read the versioning memory** — load
   `~/.claude/projects/-home-lothrop-src-gamba-pick/memory/project_versioning_policy.md`
   so the tier rules are authoritative and current. (The rules
   below are a summary — the memory is the source of truth.)
5. **Classify the diff** against the tier rules (see "Tier
   classification" below).
6. **Read the current version** from `pyproject.toml`.
7. **Compute the proposed version** per tier.
8. **Check for an existing version bump on the branch** — `git log
   master..HEAD --oneline | grep -i "bump version"`. If one exists
   and matches the computed version, skip the bump step. If it
   exists but the tier classification has *escalated* since (e.g.,
   started as patch, branch later picked up a `casino.py` change),
   tell the user and ask how to proceed.
9. **Show the user the plan** before making any commit:
   - branch name
   - file list (with classification annotations)
   - proposed tier + version
   - proposed PR title + body draft
   Wait for explicit confirmation. If they want changes to the
   draft, iterate before proceeding.
10. **Bump pyproject.toml** (if needed): edit the `version = "..."`
    line, commit with message `Bump version to X.Y.Z`.
11. **Push the branch** with `git push -u origin <branch>` if
    upstream isn't set, otherwise `git push`.
12. **Open the PR** with `gh pr create --title "..." --body "$(cat <<'EOF' ... EOF)"`.
13. **Return the PR URL** to the user.

## Tier classification

The memory file is authoritative. Quick reference:

| Tier | Triggers |
|------|---------|
| **patch** (0.X.Y → 0.X.Y+1) | New site config file added; selector fix in a single existing site config; doc/comment/log-line cosmetic; scoped bugfix inside one site config |
| **minor** (0.X.0 → 0.X+1.0) | Any change to `casino.py`, `runner.py`, `scrapling_ext.py`; field added to `CasinoConfig` / `LoginConfig` / `MTBClaimConfig` / `SimpleClaimConfig` / `GenericClaimConfig` / `CurrencyDisplayConfig` / `Currency`; runner outcome-parser regex tweaks; new framework helper that site configs are expected to use |
| **major** (0.X.Y → 1.0.0) | Reserved — only when a frozen public API exists for external library consumers. Don't bump major because something feels significant |

**Highest tier wins** in mixed PRs. A branch that adds a new site
*and* a small `casino.py` tweak is **minor**, not patch.

When the diff is ambiguous, lean toward over-bumping rather than
under-bumping. Configurators reading the diff need accurate signal.

### File-pattern hints (not authoritative — use judgment):

- `pyproject.toml` only → ignore (it's the bump itself)
- `*.py` under repo root that's a site config (e.g.
  `shuffle_us.py`, `stake_us.py`) → patch
- `casino.py`, `runner.py`, `scrapling_ext.py`,
  `selectors_generic.py` → minor
- `README.md`, `*.md` only → patch (or no-bump if pure typo fix)
- `tests/` only → patch
- `sites_seed.toml` → patch
- New `*.py` at repo root → patch (new site config)

If the diff includes a `*Config` field addition (grep the diff for
`@dataclass` regions or `: Optional` / `= None` additions in
`casino.py`), that's **minor** even if everything else looks like
a patch — public surface change.

## PR title format

Match recent commit style (verify via
`git log master --oneline | head -5` if unsure):

- `0.X.Y: <one-line summary>` for tagged-version PRs
- Keep title under 70 characters
- Imperative voice in the summary, no trailing period

Examples (from the actual project):

- `0.6.0: shuffle.us reference config + runner --config-dir fallback + --stream`
- `0.5.0: open-core split — public repo retains framework + 2 reference configs`
- `0.4.0: add Pulsz, PulszBingo, Modo (3 new working sites)`

## PR body format

```markdown
## Summary

<2–4 bullets describing the changes. Group by area: framework,
configs, runner, docs. Lead with the *why* where it's not obvious
from the diff — pull this from the conversation context, not just
the diff stats.>

## Test plan

<Bulleted markdown checklist. Include both what was already verified
in-conversation and what the reviewer should verify themselves.
Reference specific sweep runs, log evidence, or behaviors observed.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**Critical:** the body's "why" should reflect what was actually
discussed in the conversation that produced the work. Diff stats
alone produce shallow PR bodies that read like changelogs. If the
PR fixed an orphan-promise crash, *say that* — and explain the
mechanism enough that a reviewer can sanity-check the fix.

## Confirmation gate

Before committing the version bump or running `gh pr create`, show
the user:

```
Branch: <name>  →  master
Commits: <N> ahead

Files changed:
  casino.py                         [minor: framework field added]
  americanluck.py                   [patch: settle wait wired]
  ...

Tier: minor
Version: 0.10.0 → 0.11.0

Proposed PR title:
  0.11.0: pre_claim_settle_ms + orphan-promise hardening across MTB clicks

Proposed PR body:
  ## Summary
  ...

Looks good? (Want to tweak anything?)
```

The user is in control of every step. Do not auto-commit or
auto-open the PR without affirmative confirmation.

## Edge cases

- **Branch tracks an open PR already**: detect via `gh pr view --json
  state,url` (or the absence). If a PR is open, this is an *update*
  flow — push commits, optionally edit title/body via `gh pr edit`,
  don't try to `gh pr create` again.
- **Conflicts with master**: if `git merge-base --is-ancestor
  master HEAD` is false (we're behind), warn the user and stop —
  don't push or open a PR until they rebase / merge.
- **Bump already present**: if a "Bump version to X.Y.Z" commit
  exists matching the computed tier, skip the bump and proceed.
  If it exists but is the *wrong* tier (under-bump), ask the
  user: amend the prior commit, add a new bump on top, or
  override classification.
- **Pure typo / doc-only PR**: skip the bump entirely. Not every
  PR needs a version. Confirm with the user that no-bump is
  intended.

## Don't

- Don't bump pyproject.toml *during* iteration on a feature
  branch — only at PR-finalization time. The tier rule applies
  to the totality of the work being merged, not to each commit.
  (User clarified 2026-05-02 after I'd been bumping per-edit.)
- Don't `git push --force` without explicit user request.
- Don't merge the PR. Open it, then stop. Merge is the user's
  call after review.
- Don't fabricate test-plan items. If you don't know whether
  something was tested, say so explicitly in the body.
- Don't run this in `casino-buddy-internal` or any other repo —
  the rules here are gamba_pick-specific.
