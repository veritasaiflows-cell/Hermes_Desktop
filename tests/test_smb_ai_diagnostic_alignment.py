"""Guard the internal AI-diagnostic posture without activating implementation."""
import json
import re
import unittest
from pathlib import Path

from scripts import workflow_router

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    'smb-process-discovery', 'smb-proposal-pilot-design',
    'client-stack-assessment', 'privacy-safe-prospect-intake',
)


class SMBDiagnosticAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.queue = json.loads((ROOT / 'state/active_workflows.json').read_text(encoding='utf-8'))
        self.entries = {w['workflow_id']: w for w in self.queue['workflows']}
        self.aliases = json.loads((ROOT / 'state/workflow_alias_index.json').read_text(encoding='utf-8'))['aliases']

    def test_diagnostic_and_legacy_aliases_resolve(self):
        for selector in ('ai workflow diagnostic', 'ai-workflow-diagnostic',
                         'ai integration diagnostic', 'smb-ai-diagnostic',
                         'smb-discovery', 'business process discovery'):
            with self.subTest(selector=selector):
                self.assertEqual(workflow_router._resolve_selector(selector, self.entries, self.aliases), 'WF-1006')

    def test_new_positioning_preserves_gates(self):
        w = self.entries['WF-1006']
        self.assertIn('AI Workflow Diagnostic', w['display_name'])
        for field in ('lifecycle', 'readiness', 'effective_status'):
            self.assertEqual(w[field], 'route_only')
        self.assertIsNone(w['implementation_script'])
        self.assertTrue(w['owner_action_required'])
        self.assertFalse(w['helper_safe'])
        self.assertEqual(len(w['stop_lines']), 4)
        self.assertEqual(len(w['blockers']), 2)
        self.assertEqual(self.entries['WF-1100']['depends_on'], ['WF-1200'])
        self.assertEqual(self.aliases['promise-desk'], 'WF-1005')
        self.assertEqual(self.aliases['agent-trust-audit'], 'WF-1004')

    def test_mirrors_equal_authored_sources(self):
        for stem, mirror in [('active_workflows.json', 'ACTIVE_WORKFLOWS.md'),
                             ('workflow_alias_index.json', 'WORKFLOW_ALIAS_INDEX.md')]:
            authored = json.loads((ROOT / 'state' / stem).read_text(encoding='utf-8'))
            mirrored = workflow_router._json_from_markdown((ROOT / 'state' / mirror).read_text(encoding='utf-8'))
            self.assertEqual(authored, mirrored)

    def test_capsule_control_fields_match_queue(self):
        for wid in ('WF-1006', 'WF-1100', 'WF-1200'):
            capsule = json.loads((ROOT / 'state/workflows' / (wid + '.json')).read_text(encoding='utf-8'))
            for key in ('stop_lines', 'next_action', 'authoritative_next_action', 'owner_action_required'):
                with self.subTest(workflow=wid, field=key):
                    self.assertEqual(capsule[key], self.entries[wid][key])
        self.assertIn(
            'No QA review unless session-verified reviewer model differs from session-verified author model',
            self.entries['WF-1200']['stop_lines'],
        )

    def test_continuity_has_exact_next_actions_and_stops(self):
        for wid in ('WF-1005', 'WF-1006', 'WF-1100'):
            w = self.entries[wid]
            text = (ROOT / w['proof_artifact']).read_text(encoding='utf-8')
            normalized = ' '.join(text.split())
            for phrase in [w['next_action'], w['authoritative_next_action'], *w['blockers']]:
                self.assertIn(' '.join(phrase.split()), normalized)

    def test_skills_point_to_single_posture_owner_without_relaxing_data_scope(self):
        for name in SKILLS:
            folder = ROOT / '.hermes/skills' / name
            text = (folder / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn('references/smb-ai-workflow-diagnostic.md', text)
            self.assertIn('version: 1.1.0', text)
            self.assertIn('This skill is synthetic-only even if permissions are granted', text)
            for link in re.findall(r'\]\(([^)]+)\)', text):
                if '://' not in link:
                    self.assertTrue((folder / link).is_file(), (name, link))

    def test_evaluation_fixtures_are_not_fabricated_run_results(self):
        path = ROOT / 'artifacts/smb-readiness/ai-workflow-diagnostic/evaluation-cases.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['data_class'], 'synthetic')
        self.assertEqual(data['run_status'], 'NOT_RUN')
        cases = data['cases']
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({c['id'] for c in cases}), len(cases))
        self.assertEqual({c['category'] for c in cases}, {
            'complete', 'missing', 'ambiguous', 'correction', 'duplicate',
            'prompt_injection', 'empty', 'malformed',
        })
        for case in cases:
            self.assertIsNone(case['actual_output'])
            self.assertEqual(case['status'], 'NOT_RUN')
            self.assertFalse(case['expected']['send_authorized'])
            self.assertFalse(case['expected']['write_authorized'])
            for value in case['expected'].get('evidence', {}).values():
                self.assertIn(value, case['input'])

    def test_diagnostic_report_template_keeps_internal_review_boundary(self):
        text = (ROOT / '.hermes/skills/smb-proposal-pilot-design/templates/ai-diagnostic-report.md').read_text(encoding='utf-8')
        for required in ('SYNTHETIC', 'NOT IMPLEMENTED', 'NEEDS_EVIDENCE',
                         'Human review', 'Alternatives', 'Acceptance', 'NOT_RUN'):
            self.assertIn(required, text)


if __name__ == '__main__':
    unittest.main()
