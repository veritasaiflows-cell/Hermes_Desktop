# Active workflow queue (authoritative)

```json
{
  "schema": "active-workflows.v1",
  "generated_by": "control-plane bootstrap",
  "generated_at": "2026-08-15T00:00:00Z",
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
      "state_description": "Workflow A processes supplier catalogs into scored product_candidate entities.",
      "next_action": "Run product_research_workflow.py against approved source catalog and review candidates before phase-3.",
      "authoritative_next_action": "Run product_research_workflow.py against approved source catalog and review candidates before any external connector action.",
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
      "blockers": [
        "No approved external connector configuration yet for phase 3."
      ],
      "stop_lines": [
        "Override removal before external writes"
      ],
      "primary_route_artifact": "state/workflows/WF-1000.json",
      "validator_commands": [
        "python -m unittest discover -s tests -v",
        "python scripts/run_checks.py --skip-smoke",
        "python scripts/workflow_router.py WF-1000 --answer summary --validate"
      ]
    }
  ]
}
```
