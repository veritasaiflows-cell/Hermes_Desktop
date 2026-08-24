# Efficiens project skills

This directory is the authoritative, Git-backed source for skills whose procedures are specific to `HermesWorkspace`.

Current project skills:

- `mcp-integration-governance`
- `mcp-pilot-evaluation`

Hermes discovers these skills only for sessions rooted in this repository. The repository must be trusted once with `hermes skills trust`; project skills then take precedence over same-named profile-local skills.

## Ownership and durability

- Edit these files as governed workspace source, review their diffs, run the skill audit and repository gates, and include them in a scope-limited local checkpoint commit.
- Do not maintain a second editable profile-local copy with the same name. Profile copies may exist only as Curator backups or archives, not as competing authority.
- Never push merely because a local checkpoint was created.
- General-purpose skills that are not workspace-specific remain profile-local and use Curator backup plus mutation-ledger evidence instead of pretending they are covered by workspace Git.
