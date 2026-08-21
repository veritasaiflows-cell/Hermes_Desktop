# CORE BOOTSTRAP AND SKILLS GOVERNANCE

Maintain a lean, clean separation between initial bootstrap instructions, detailed skills, procedures, memory, and generated artifacts.

## Architectural rule

The initial bootstrap answers only:

- Who the agent is
- Its mission and operating principles
- Non-negotiable safety and authority boundaries
- How it should communicate
- How it should orient at startup
- How it should route work
- Where detailed instructions are located

Detailed procedures belong in skills. Do not place long workflows, troubleshooting guides, examples, schemas, tool recipes, or domain-specific operating instructions in core bootstrap files.

## Core bootstrap files

Keep bootstrap files short, stable, and broadly applicable.

Core files may contain:

- Identity
- Mission
- Values
- Safety boundaries
- Authority model
- Response contract
- Startup sequence
- Tool-routing principles
- Skill-discovery rules
- Pointers to authoritative files

Core files must not contain:

- Full procedures
- Repeated instructions from skills
- Long examples
- Historical logs
- Temporary project details
- Generated reports
- Detailed API or command references
- Duplicated policy text
- Step-by-step troubleshooting
- Information needed only for one workflow

If a rule is not needed during nearly every session, it probably belongs in a skill, reference document, memory record, or artifact.

## Single-owner rule

Every important instruction must have one authoritative owner.

Use this ownership model:

- Identity and mission: identity file
- Global safety and authority: core operating rules
- User preferences: user profile
- Runtime and tool map: tools/configuration file
- Startup sequence: bootstrap operating rules
- Detailed procedures: skills
- Durable facts and decisions: memory
- Current task state: workflow or task records
- Generated results: artifacts
- Historical evidence: source records and logs

Other files may link to or summarize the owner, but must not create competing versions of the same rule.

When duplicate or conflicting instructions are found:

1. Identify the authoritative owner.
2. Remove duplicated detail from weaker locations.
3. Replace it with a short pointer.
4. Preserve useful information in the correct skill or reference file.
5. Validate that no behavior was lost.

## Startup loading policy

At startup, load only:

1. Identity and mission
2. Core operating rules
3. User preferences
4. Runtime/tool map
5. Compact skill index
6. Current status and active task pointers

Do not load every skill in full during bootstrap.

When a request matches a skill:

1. Identify the matching skill.
2. Read that skill completely before taking action.
3. Load only the references required by that skill.
4. Follow the skill’s procedure.
5. Return to the core response contract when reporting the result.

Bootstrap files should route work; skills should explain how to perform it.

## Skill design standard

Each skill should be detailed enough to execute independently without making the core bootstrap large.

Every skill should include:

- Name and version
- Purpose
- Trigger conditions
- Scope
- Explicit non-scope
- Required inputs
- Preconditions
- Procedure
- Decision points
- Tool usage
- Expected outputs
- Validation requirements
- Failure handling
- Rollback or recovery guidance
- Security and authority considerations
- Examples where useful
- Related references
- Change history

Skills must not redefine identity, global safety rules, or authority boundaries. They should reference the core policy instead.

A skill should explain the actual work. The bootstrap should explain when to find and use the skill.

## Keep skills modular

Prefer several focused skills over one enormous skill.

Split a skill when it contains:

- Multiple unrelated workflows
- Different triggers
- Separate toolchains
- Different validation requirements
- Different risk levels
- More than one independent owner
- Procedures that are rarely used together

Use a small routing skill or index to direct the agent to the detailed skill.

Avoid excessive fragmentation when splitting would make a procedure difficult to follow.

## Context and size budgets

Maintain explicit size budgets for core files.

Recommended starting limits:

- Identity file: brief and stable
- Core operating rules: compact and procedural only at the routing level
- User profile: preferences and constraints only
- Tool map: routing information, not full tool manuals
- Startup status: current state only
- Skill index: names, triggers, and one-line descriptions

If a core file repeatedly grows beyond its budget:

1. Identify detailed or rarely used content.
2. Move that content into the appropriate skill or reference.
3. Replace it with a concise pointer.
4. Re-run duplicate and contradiction checks.
5. Confirm that startup still provides enough information to route work correctly.

Do not solve context growth by adding more summaries to the bootstrap. Move detail to the correct layer.

## Maintenance workflow

Before editing a core file:

1. Search for duplicate instructions elsewhere.
2. Identify the authoritative owner.
3. Check whether the change belongs in a skill instead.
4. Review links and references.
5. Make the smallest complete change.
6. Validate file size, formatting, links, and conflicts.
7. Test startup routing.
8. Record the change.

Before editing a skill:

1. Confirm its trigger and scope.
2. Check whether another skill owns the same procedure.
3. Verify all referenced tools and files.
4. Test the main path and failure path.
5. Confirm that the skill does not contradict core policy.
6. Update its version or change history when appropriate.

## Quality checks

Regularly run checks for:

- Core files exceeding their size budgets
- Duplicate rules
- Contradictory instructions
- Broken skill links
- Missing skill metadata
- Procedures without validation steps
- Procedures without failure handling
- Stale references
- Skills that silently change authority boundaries
- Detailed instructions embedded in bootstrap files
- Bootstrap references to files that no longer exist

A clean bootstrap should make the agent easy to orient and route. It should not attempt to contain the entire operating system.

## Change classification

Classify every new instruction before storing it:

- Core policy: needed broadly and continuously
- Skill procedure: detailed task execution method
- Reference: supporting technical or domain information
- Memory: durable fact, preference, decision, or lesson
- Workflow state: current task status or queue state
- Artifact: generated output or proof

Store the instruction in the narrowest correct layer.

## Workspace placement map

This section is the authoritative owner for persistent workspace placement and
retention. Layer READMEs may add local detail but must not contradict this map.

| Surface | Purpose and authority | Retention rule |
|---|---|---|
| `source/` | Preserved original evidence and approved inputs | Preserve originals; do not replace them with summaries |
| `canonical/` | Authoritative structured state and schema | Durable; migrations and writes require validation and provenance |
| `graph/` | Relationship-layer contract and adapters; durable edges remain canonical records | Rebuild derived views, preserve asserted edge provenance |
| `vector/indexes/` | Rebuildable semantic and exact full-text retrieval indexes | Durable local cache, ignored by git, refresh when source hashes drift |
| `derived/` | Generated reports, rankings, packets, dashboards, and caches | Rebuildable unless explicitly promoted with provenance |
| `telemetry/` | Run measurements, verification outcomes, corrections, and errors | Retain according to the telemetry contract; metadata only by default |
| `state/` | Authoritative workflow control plane plus generated routing surfaces | Preserve authoritative queue/override/lane state; refresh generated surfaces only through owner scripts |
| `continuity/` | Human-readable resume context for major workflows | Durable while the workflow is active or historically relevant |
| `references/` | Authoritative detailed contracts and technical guidance | Version-controlled; one authoritative owner per contract |
| `wiki/` | Generated navigational and synthesis views | Rebuild from declared sources; manifest freshness is mandatory |
| `scripts/` and `tests/` | Executable behavior and its verification | Version-controlled; behavior changes require tests |
| `tmp/` | Disposable per-run scratch and query packets | Delete after use or promote to the correct governed layer; never store live indexes or authoritative state |

Every persistent artifact must have an identifiable authority owner, governing
README or contract, retention rule, and verification path. A file being ignored
by git does not make it temporary; authority and rebuildability determine its
layer.

## Final principle

Keep the bootstrap small enough to load quickly and understand immediately.

Keep skills detailed enough to execute reliably.

Keep memory factual and durable.

Keep artifacts traceable and disposable.

Keep one authoritative source for every important rule.

The agent should begin with orientation, route to the correct skill, execute the detailed procedure, verify the result, and return a concise evidence-backed response.
