# PR governance for tapestry_client

All changes use an issue in this repository and a reviewed PR. Validate the
candidate locally before opening it; drafts may track a validated slice. Link
cross-repository dependencies and merge order. GitHub's native `Closes #...`
handles owning-repository issues; the retired workspace board is not a dependency.

The maintainer reviews the candidate diff and green CI before authorizing a
manual merge. Automated reviewers remain advisory. General plan approval and
green checks are not approval to merge a particular implementation. Record
out-of-band approval against the exact PR/commit. A merge does not authorize
production deployment. Changed code needs affected checks and renewed review.

## Reviewed rollout proposal

September 16, 2026 audit: Main is currently unprotected.
The following proposal is **not applied** by this PR. Review the accompanying
[REST request](main-protection.proposed.json) before changing GitHub settings.
It requires a current base, enforces rules for administrators, requires resolved
conversations, and retains the prohibition on force pushes and branch deletion.

The current author and sole maintainer use the same account, which cannot approve
its own PR. The formal approval count is zero; owner review and manual merge are
procedural requirements. Introduce one required independent approval when a
separate author or second maintainer is available, through another reviewed change.

Required GitHub Actions contexts (app ID `15368`):

- `test (3.13)`

These names come from the repository's current workflows and successful runs;
they are not copied from another repository. The workflows run for every PR to
main without top-level path filters. Advisory scans remain advisory. Preserve
these stable context names or update the protection in the same reviewed rollout.

After this policy is reviewed and merged, re-read current protection and reconcile
intervening administrator changes. Require successful main runs for CI/security
contexts and a successful current PR for PR-only title checks. Save sanitized
before/after snapshots and run links, apply the reviewed request, read it back,
and verify the requirements on an ordinary PR. Do not test enforcement by making
a direct main push or attempting to merge failing code.

Policy background: [Tapestry PR governance](https://github.com/tapestry-ecosystem/tapestry/blob/main/docs/maintenance/pr-governance.md).

`test (3.13)` runs frozen installation, formatting, lint, types, unit/integration lanes, combined coverage and disjoint case ownership. No nonexistent title or security workflow is made mandatory.
