# Active workflow queue (authoritative)

```json
{
  "schema": "active-workflows.v1",
  "routing_schema_version": "workflow-routing-index.v1",
  "generated_by": "control-plane bootstrap",
  "generated_at": "2026-08-16T04:10:00Z",
  "workflows": [
    {
      "workflow_id": "WF-1000",
      "display_name": "Workflow A - Product Research",
      "aliases": [
        "workflow-a",
        "product-research"
      ],
      "tier": "P1",
      "priority": "high",
      "lifecycle": "active",
      "readiness": "active",
      "effective_status": "active",
      "state_description": "Workflow A processes supplier catalogs into scored product_candidate entities with business-key dedupe, dry-run-first preflight, and phase-3 handoff packets.",
      "next_action": "Run product_research_workflow.py against approved source catalogs and review top-opportunity packets in derived/research before any connector activation.",
      "authoritative_next_action": "Run product_research_workflow.py against approved source catalog and review candidates before any external connector action.",
      "implementation_script": "scripts/product_research_workflow.py",
      "commands": {
        "dry_run": "python scripts/product_research_workflow.py <catalog.csv> --dry-run --top-n 5",
        "write": "python scripts/product_research_workflow.py <catalog.csv> --database canonical/efficiens.db --top-n 5 --lane-id WF-1000::product-research --lane-owner agent-main"
      },
      "helper_safe": true,
      "owner_action_required": false,
      "authority_boundary": "canary",
      "authority_class": "review_ready",
      "primary_owner_lane": "agent-main",
      "secondary_consumers": [
        "review",
        "operations"
      ],
      "human_approval_owner": "operator",
      "proof_artifact": "continuity/WF-1000-Product-Research.md",
      "freshness_sla": "daily",
      "default_resume_command": "python scripts/workflow_router.py WF-1000 --answer next --validate",
      "control_override": null,
      "primary_route_artifact": "state/workflows/WF-1000.json",
      "validator_commands": [
        "python -m unittest discover -s tests -v",
        "python scripts/run_checks.py --skip-smoke",
        "python scripts/workflow_router.py WF-1000 --answer summary --validate"
      ]
    },
    {
      "workflow_id": "WF-1001",
      "display_name": "Workflow B - Listing Drafts",
      "aliases": [
        "workflow-b",
        "listing-drafts",
        "listings"
      ],
      "tier": "P2",
      "priority": "medium",
      "lifecycle": "route_only",
      "readiness": "route_only",
      "effective_status": "route_only",
      "state_description": "Generates storefront-ready listing drafts from WF-1000 top opportunities. Internal drafts only until a storefront connector is approved.",
      "next_action": "Await operator approval and storefront connector configuration before activation.",
      "authoritative_next_action": "Do not activate until an approved storefront connector exists and operator approval is recorded.",
      "implementation_script": null,
      "commands": {
        "dry_run": null,
        "write": null
      },
      "helper_safe": false,
      "owner_action_required": true,
      "authority_boundary": "review_only",
      "authority_class": "route_only",
      "primary_owner_lane": "agent-main",
      "secondary_consumers": [
        "review",
        "operations"
      ],
      "human_approval_owner": "operator",
      "proof_artifact": "continuity/WF-1001-Listing-Drafts.md",
      "freshness_sla": "weekly",
      "default_resume_command": "python scripts/workflow_router.py WF-1001 --answer next --validate",
      "control_override": null,
      "depends_on": [
        "WF-1000"
      ],
      "blockers": [
        "No approved external connector configuration yet for phase 3.",
        "No approved storefront connector for listing publication."
      ],
      "stop_lines": [
        "Override removal before external writes",
        "No listing publication without operator approval"
      ],
      "primary_route_artifact": "state/workflows/WF-1001.json",
      "validator_commands": [
        "python -m unittest discover -s tests -v",
        "python scripts/workflow_router.py WF-1001 --answer summary --validate"
      ]
    },
    {
      "workflow_id": "WF-1002",
      "display_name": "Workflow C - Creative Generation",
      "aliases": [
        "workflow-c",
        "creative-generation",
        "creatives"
      ],
      "tier": "P2",
      "priority": "medium",
      "lifecycle": "route_only",
      "readiness": "route_only",
      "effective_status": "route_only",
      "state_description": "Generates ad creative and copy drafts from WF-1000 top opportunities. Internal drafts only until an ad-channel connector is approved.",
      "next_action": "Await operator approval and ad-channel connector configuration before activation.",
      "authoritative_next_action": "Do not activate until an approved ad-channel connector exists and operator approval is recorded.",
      "implementation_script": null,
      "commands": {
        "dry_run": null,
        "write": null
      },
      "helper_safe": false,
      "owner_action_required": true,
      "authority_boundary": "review_only",
      "authority_class": "route_only",
      "primary_owner_lane": "agent-main",
      "secondary_consumers": [
        "review",
        "operations"
      ],
      "human_approval_owner": "operator",
      "proof_artifact": "continuity/WF-1002-Creative-Generation.md",
      "freshness_sla": "weekly",
      "default_resume_command": "python scripts/workflow_router.py WF-1002 --answer next --validate",
      "control_override": null,
      "depends_on": [
        "WF-1000"
      ],
      "blockers": [
        "No approved external connector configuration yet for phase 3.",
        "No approved ad-channel connector for creative delivery."
      ],
      "stop_lines": [
        "Override removal before external writes",
        "No creative delivery without operator approval"
      ],
      "primary_route_artifact": "state/workflows/WF-1002.json",
      "validator_commands": [
        "python -m unittest discover -s tests -v",
        "python scripts/workflow_router.py WF-1002 --answer summary --validate"
      ]
    },
    {
      "workflow_id": "WF-1003",
      "display_name": "Workflow D - Campaign Execution",
      "aliases": [
        "workflow-d",
        "campaign-execution",
        "campaigns"
      ],
      "tier": "P2",
      "priority": "medium",
      "lifecycle": "route_only",
      "readiness": "route_only",
      "effective_status": "route_only",
      "state_description": "Plans and executes ad campaigns with spend controls from approved WF-1000 opportunities, WF-1001 listings, and WF-1002 creative. Fully gated until connectors and spend approval exist.",
      "next_action": "Await operator approval, connector configuration, and spend-cap policy before activation.",
      "authoritative_next_action": "Do not activate until connectors are approved, spend caps are set, and operator approval is recorded.",
      "implementation_script": null,
      "commands": {
        "dry_run": null,
        "write": null
      },
      "helper_safe": false,
      "owner_action_required": true,
      "authority_boundary": "review_only",
      "authority_class": "route_only",
      "primary_owner_lane": "agent-main",
      "secondary_consumers": [
        "review",
        "operations"
      ],
      "human_approval_owner": "operator",
      "proof_artifact": "continuity/WF-1003-Campaign-Execution.md",
      "freshness_sla": "weekly",
      "default_resume_command": "python scripts/workflow_router.py WF-1003 --answer next --validate",
      "control_override": null,
      "depends_on": [
        "WF-1000",
        "WF-1001",
        "WF-1002"
      ],
      "blockers": [
        "No approved external connector configuration yet for phase 3.",
        "No approved ad-channel connector for campaign execution.",
        "No spend-cap policy approved for campaign budgets."
      ],
      "stop_lines": [
        "Override removal before external writes",
        "No campaign spend without operator approval"
      ],
      "primary_route_artifact": "state/workflows/WF-1003.json",
      "validator_commands": [
        "python -m unittest discover -s tests -v",
        "python scripts/workflow_router.py WF-1003 --answer summary --validate"
      ]
    }
  ]
}
```
