import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from canonical.db import CanonicalDB
from scripts.product_research_workflow import run_product_research
from scripts.top_opportunities_report import generate as generate_top_opportunities
from scripts.workflow_runner import WorkflowPreflightError


POLICY = {
    "schema": "commerce-launch-policy.v1",
    "market": "US",
    "currency": "USD",
    "retail_price_usd": {"min": 30.0, "max": 80.0},
    "retail_price_basis": "offer_total",
    "bundle_rules": {
        "single_supplier_required": True,
        "minimum_component_count": 2,
    },
    "margin_basis_values": [
        "landed_cost",
        "product_cost_only",
        "supplier_claim_unknown",
    ],
    "comparable_margin_basis": "landed_cost",
    "max_shipping_days": 7,
    "max_moq": 1,
    "min_gross_margin_percent": 55.0,
    "min_pre_ad_contribution_percent": 35.0,
    "max_margin_reconciliation_delta_percent": 5.0,
    "evidence_max_age_days": 14,
    "require_tracking": True,
    "required_inventory_statuses": ["in_stock"],
    "sample_budget_usd": 250.0,
    "sample_shortlist_size": 3,
    "product_character_requirements": [
        "small",
        "light",
        "durable",
        "visually_demonstrable",
        "solves_obvious_problem",
    ],
    "qualification_modes": {
        "research": {
            "max_shortlist_size": 10,
            "min_product_only_margin_percent": 40.0,
            "quality_status": "research_candidate",
        },
        "organic_sample": {
            "max_shortlist_size": 3,
            "min_gross_margin_percent": 45.0,
            "min_pre_ad_contribution_percent": 35.0,
            "min_pre_ad_contribution_usd": 20.0,
            "sample_pending_status": "organic_sample_candidate",
            "sample_passed_status": "organic_pilot_ready",
        },
        "paid_launch": {
            "max_shortlist_size": 1,
            "min_gross_margin_percent": 55.0,
            "min_pre_ad_contribution_percent": 35.0,
            "min_post_ad_contribution_percent": 10.0,
            "quality_status": "paid_launch_ready",
        },
    },
    "excluded_risk_tags": [
        "supplement",
        "medical_claim",
        "cosmetic",
        "childrens_product",
        "weapon",
        "hazardous_material",
        "fragile",
        "counterfeit",
        "unauthorized_branded",
        "high_return_sizing",
        "safety_sensitive_electronics",
    ],
}

PROFILE = {
    "schema": "commerce-interest-profile.v1",
    "profile_id": "RANDALL-AI-REVENUE-SYSTEMS-2026-08-29",
    "mission": "Build and publicly demonstrate practical AI revenue systems.",
    "primary_audience_ids": [
        "small_shopify_operators",
        "solo_ai_builders",
        "practical_creators",
    ],
    "approved_problem_spaces": [
        "creator_workspace_organization",
        "product_content_capture",
        "merchant_operating_station_efficiency",
    ],
    "minimum_strategic_fit_score": 70.0,
    "minimum_content_angles": 5,
    "operator_fit_statuses": ["approved"],
    "strategic_fit_weights": {
        "audience_fit_score": 0.25,
        "operator_fit_score": 0.20,
        "content_demo_score": 0.20,
        "brand_extension_score": 0.15,
        "bundle_potential_score": 0.10,
        "case_study_value_score": 0.10,
    },
    "approved_theses": [
        {
            "thesis_id": "creator-desk-reset",
            "audience_ids": ["small_shopify_operators", "solo_ai_builders", "practical_creators"],
            "problem_spaces": ["creator_workspace_organization"],
            "interest_tags": ["ai_operators", "creator_workspaces", "ecommerce_operations"],
        },
        {
            "thesis_id": "shopify-content-capture",
            "audience_ids": ["small_shopify_operators", "practical_creators"],
            "problem_spaces": ["product_content_capture"],
            "interest_tags": ["creator_workflows", "ecommerce_operations"],
        },
        {
            "thesis_id": "seller-operations-station",
            "audience_ids": ["small_shopify_operators"],
            "problem_spaces": ["merchant_operating_station_efficiency"],
            "interest_tags": ["ecommerce_operations", "merchant_productivity"],
        },
    ],
    "default_excluded_categories": ["pet", "automotive", "kitchen", "garden", "general_home"],
}

FIELDNAMES = [
    "product_name",
    "supplier_name",
    "margin_percent",
    "margin_basis",
    "shipping_days",
    "demand_signal",
    "saturation_signal",
    "cost_per_unit_usd",
    "retail_price_usd",
    "source_uri",
    "supplier_url",
    "evidence_status",
    "evidence_checked_at",
    "supplier_identity_status",
    "return_policy_url",
    "compliance_ip_status",
    "quality_evidence_status",
    "estimated_cac_usd",
    "warehouse_country",
    "inventory_status",
    "tracking_available",
    "moq",
    "shipping_cost_usd",
    "packaging_cost_usd",
    "duties_usd",
    "payment_fee_percent",
    "payment_fee_fixed_usd",
    "expected_return_cost_usd",
    "risk_tags",
    "sample_status",
    "thesis_id",
    "audience_id",
    "problem_space",
    "problem_statement",
    "why_operator_cares",
    "why_customer_cares",
    "category",
    "interest_tags",
    "content_angles",
    "audience_fit_score",
    "operator_fit_score",
    "content_demo_score",
    "brand_extension_score",
    "bundle_potential_score",
    "case_study_value_score",
    "operator_fit_status",
    "product_character_tags",
]

RESEARCH_FIELDNAMES = [
    "product_name",
    "supplier_name",
    "margin_percent",
    "shipping_days",
    "demand_signal",
    "saturation_signal",
    "source_uri",
    "thesis_id",
    "audience_id",
    "problem_space",
    "problem_statement",
    "why_operator_cares",
    "why_customer_cares",
    "category",
    "interest_tags",
    "content_angles",
    "audience_fit_score",
    "operator_fit_score",
    "content_demo_score",
    "brand_extension_score",
    "bundle_potential_score",
    "case_study_value_score",
    "operator_fit_status",
    "product_character_tags",
]


def write_policy(path: Path, **overrides: object) -> Path:
    policy = dict(POLICY)
    policy.update(overrides)
    path.write_text(json.dumps(policy), encoding="utf-8")
    write_profile(path.parent / "operator-interest-profile.json")
    return path


def write_profile(path: Path, **overrides: object) -> Path:
    profile = dict(PROFILE)
    profile.update(overrides)
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path


def write_catalog(
    path: Path,
    rows: list[dict[str, object]],
    *,
    fieldnames: list[str] = FIELDNAMES,
) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def viable_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "product_name": "Compact Desk Organizer",
        "supplier_name": "Supplier A",
        "margin_percent": 60,
        "margin_basis": "landed_cost",
        "shipping_days": 5,
        "demand_signal": 75,
        "saturation_signal": 30,
        "cost_per_unit_usd": 15,
        "retail_price_usd": 50,
        "source_uri": "https://supplier-a.com/products/desk-organizer",
        "supplier_url": "https://supplier-a.com",
        "evidence_status": "verified",
        "evidence_checked_at": datetime.now(timezone.utc).isoformat(),
        "supplier_identity_status": "verified",
        "return_policy_url": "https://supplier-a.com/returns",
        "compliance_ip_status": "cleared",
        "quality_evidence_status": "sample_pending",
        "estimated_cac_usd": "unknown",
        "warehouse_country": "US",
        "inventory_status": "in_stock",
        "tracking_available": "true",
        "moq": 1,
        "shipping_cost_usd": 4,
        "packaging_cost_usd": 1,
        "duties_usd": 0,
        "payment_fee_percent": 2.9,
        "payment_fee_fixed_usd": 0.30,
        "expected_return_cost_usd": 2,
        "risk_tags": "none",
        "sample_status": "pending",
        "thesis_id": "creator-desk-reset",
        "audience_id": "small_shopify_operators",
        "problem_space": "creator_workspace_organization",
        "problem_statement": "AI operators need a clean, demonstrable workspace.",
        "why_operator_cares": "The operator can use and test the offer in the live AI-commerce workflow.",
        "why_customer_cares": "The offer removes visible cable clutter from a creator workspace.",
        "category": "workspace_organization",
        "interest_tags": "ai_operators|creator_workspaces|ecommerce_operations",
        "content_angles": "before_after|setup_tutorial|fit_guide|ai_desk_build|shopify_operator_setup",
        "audience_fit_score": 85,
        "operator_fit_score": 90,
        "content_demo_score": 85,
        "brand_extension_score": 80,
        "bundle_potential_score": 90,
        "case_study_value_score": 95,
        "operator_fit_status": "approved",
        "product_character_tags": "small|light|durable|visually_demonstrable|solves_obvious_problem",
    }
    row.update(overrides)
    return row


class CommerceLaunchPolicyTests(unittest.TestCase):
    def test_repository_policy_matches_operator_approved_defaults(self) -> None:
        policy_path = Path(__file__).resolve().parents[1] / "state" / "commerce-launch-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))

        self.assertEqual(policy["schema"], "commerce-launch-policy.v1")
        self.assertEqual(policy["market"], "US")
        self.assertEqual(policy["retail_price_usd"], {"min": 30.0, "max": 80.0})
        self.assertEqual(policy["max_shipping_days"], 7)
        self.assertEqual(policy["max_moq"], 1)
        self.assertEqual(policy["min_gross_margin_percent"], 55.0)
        self.assertEqual(policy["min_pre_ad_contribution_percent"], 35.0)
        self.assertEqual(policy["sample_budget_usd"], 250.0)
        self.assertEqual(policy["sample_shortlist_size"], 3)
        self.assertEqual(
            policy["qualification_modes"]["organic_sample"]["min_gross_margin_percent"],
            45.0,
        )
        self.assertEqual(
            policy["qualification_modes"]["paid_launch"]["min_gross_margin_percent"],
            55.0,
        )
        self.assertEqual(policy["retail_price_basis"], "offer_total")
        self.assertTrue(policy["bundle_rules"]["single_supplier_required"])
        self.assertIn("supplement", policy["excluded_risk_tags"])
        self.assertIn("safety_sensitive_electronics", policy["excluded_risk_tags"])
        self.assertNotIn("authorized_branded", policy["excluded_risk_tags"])

    def test_repository_profile_encodes_ai_revenue_systems_thesis(self) -> None:
        profile_path = (
            Path(__file__).resolve().parents[1] / "state" / "operator-interest-profile.json"
        )
        profile = json.loads(profile_path.read_text(encoding="utf-8"))

        self.assertEqual(profile["schema"], "commerce-interest-profile.v1")
        self.assertIn("small_shopify_operators", profile["primary_audience_ids"])
        self.assertEqual(profile["minimum_strategic_fit_score"], 70.0)
        self.assertIn("bundle_potential_score", profile["strategic_fit_weights"])
        self.assertIn("case_study_value_score", profile["strategic_fit_weights"])
        self.assertEqual(profile["approved_theses"][0]["thesis_id"], "creator-desk-reset")

    def test_interest_profile_rejects_off_thesis_product_despite_strong_commercial_score(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        product_name="Unrelated Viral Pet Gadget",
                        thesis_id="random-viral-product",
                        category="pet",
                        margin_percent=75,
                        demand_signal=100,
                        shipping_days=1,
                    )
                ],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 0)
            rejected = summary["strategic_review"]["rejected"][0]
            self.assertIn("outside_operator_thesis", rejected["reasons"])
            self.assertIn("category_excluded_by_default", rejected["reasons"])
            gate_telemetry = summary["telemetry"]["qualification_gates"]
            self.assertEqual(gate_telemetry["strategic_eligible_count"], 0)
            self.assertEqual(gate_telemetry["rejections_by_thesis"]["random-viral-product"], 1)
            self.assertEqual(
                gate_telemetry["strategic_rejection_reason_counts"]["outside_operator_thesis"],
                1,
            )

    def test_interest_profile_exposes_separate_strategic_and_commercial_scores(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            selected = summary["selected"][0]
            self.assertEqual(selected["strategic_fit"]["thesis_id"], "creator-desk-reset")
            self.assertEqual(selected["strategic_fit"]["score"], 86.75)
            self.assertEqual(
                selected["strategic_fit"]["why_operator_cares"],
                "The operator can use and test the offer in the live AI-commerce workflow.",
            )
            self.assertEqual(selected["commercial_viability_score"], selected["viability_score"])

    def test_research_mode_admits_aligned_row_without_production_columns(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(margin_percent=42)],
                fieldnames=RESEARCH_FIELDNAMES,
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="research",
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 1)
            self.assertEqual(summary["selected"][0]["quality_status"], "research_candidate")
            self.assertEqual(summary["qualification_mode"], "research")

    def test_organic_sample_mode_accepts_lower_margin_only_with_contribution_dollars(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            passing = viable_row(
                product_name="Creator Desk Reset Kit",
                margin_percent=47,
                retail_price_usd=60,
                cost_per_unit_usd=27,
                shipping_cost_usd=4,
                packaging_cost_usd=1,
            )
            failing = viable_row(
                product_name="Low Contribution Desk Kit",
                source_uri="https://supplier-a.com/products/low-contribution-kit",
                margin_percent=45,
                retail_price_usd=40,
                cost_per_unit_usd=17,
                shipping_cost_usd=4,
                packaging_cost_usd=1,
            )
            catalog = write_catalog(root / "catalog.csv", [passing, failing])

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="organic_sample",
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 1)
            selected = summary["selected"][0]
            self.assertEqual(selected["name"], "Creator Desk Reset Kit")
            self.assertEqual(selected["quality_status"], "organic_sample_candidate")
            reasons = summary["quality_review"]["rejected"][0]["reasons"]
            self.assertIn("pre_ad_contribution_usd_below_mode", reasons)
            self.assertEqual(
                summary["telemetry"]["qualification_gates"]["quality_rejection_reason_counts"][
                    "pre_ad_contribution_usd_below_mode"
                ],
                1,
            )

    def test_organic_passed_sample_cannot_be_labeled_paid_launch_ready(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        sample_status="passed",
                        quality_evidence_status="sample_passed",
                        estimated_cac_usd="unknown",
                    )
                ],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="organic_sample",
                dry_run=True,
            )

            self.assertEqual(summary["selected"][0]["quality_status"], "organic_pilot_ready")

    def test_paid_launch_requires_sample_and_profitable_numeric_cac(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(sample_status="passed", quality_evidence_status="sample_passed", estimated_cac_usd=20)],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="paid_launch",
                top_n=1,
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 1)
            self.assertEqual(summary["selected"][0]["quality_status"], "paid_launch_ready")
            self.assertGreater(summary["selected"][0]["economics"]["post_ad_contribution_usd"], 0)

    def test_paid_launch_rejects_break_even_cac_as_unprofitable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(sample_status="passed", quality_evidence_status="sample_passed", estimated_cac_usd=31.25)],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="paid_launch",
                top_n=1,
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 0)
            self.assertIn(
                "post_ad_contribution_percent_below_mode",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_product_character_requirements_are_enforced(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        product_character_tags="small|light|visually_demonstrable|solves_obvious_problem"
                    )
                ],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="organic_sample",
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 0)
            self.assertIn(
                "product_character_missing:durable",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_authorized_branded_is_reviewable_but_unauthorized_branded_is_excluded(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(product_name="Authorized Product", risk_tags="authorized_branded"),
                    viable_row(
                        product_name="Unauthorized Product",
                        source_uri="https://supplier-a.com/products/unauthorized",
                        risk_tags="unauthorized_branded",
                    ),
                ],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertEqual([item["name"] for item in summary["selected"]], ["Authorized Product"])
            self.assertIn(
                "excluded_risk_tag:unauthorized_branded",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_quality_gate_rejects_unverified_catalog_rows(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(evidence_status="unknown")],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                top_n=3,
                min_viability_score=0.0,
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 0)
            self.assertEqual(summary["quality_review"]["rejected_count"], 1)
            self.assertIn(
                "evidence_status_not_verified",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_quality_gate_reconciles_economics_for_sample_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                top_n=3,
                min_viability_score=0.0,
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 1)
            selected = summary["selected"][0]
            self.assertEqual(selected["quality_status"], "organic_sample_candidate")
            self.assertAlmostEqual(selected["economics"]["landed_cost_usd"], 20.0)
            self.assertAlmostEqual(selected["economics"]["gross_margin_percent"], 60.0)
            self.assertAlmostEqual(selected["economics"]["pre_ad_contribution_usd"], 26.25)
            self.assertAlmostEqual(selected["economics"]["pre_ad_contribution_percent"], 52.5)

    def test_unknown_estimated_cac_is_explicit_sample_warning(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])
            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            selected = summary["selected"][0]
            self.assertIn("estimated_cac_unknown", selected["quality_warnings"])
            self.assertAlmostEqual(selected["economics"]["break_even_cac_usd"], 26.25)

    def test_quality_gate_requires_supplier_returns_compliance_and_risk_reviews(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        supplier_identity_status="unknown",
                        return_policy_url="",
                        compliance_ip_status="unknown",
                        quality_evidence_status="unknown",
                        risk_tags="",
                    )
                ],
            )
            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            reasons = set(summary["quality_review"]["rejected"][0]["reasons"])
            self.assertTrue(
                {
                    "supplier_identity_not_verified",
                    "return_policy_url_invalid",
                    "compliance_ip_not_cleared",
                    "quality_evidence_status_unknown",
                    "risk_review_not_explicit",
                }.issubset(reasons)
            )

    def test_quality_gate_rejects_negative_cost_inputs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(shipping_cost_usd=-4)],
            )
            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertIn(
                "negative_shipping_cost_usd",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_quality_gate_rejects_placeholder_supplier_urls(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        source_uri="https://supplier.example.com/products/desk-organizer",
                        supplier_url="https://supplier.example.com",
                    )
                ],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            reasons = summary["quality_review"]["rejected"][0]["reasons"]
            self.assertIn("placeholder_product_url", reasons)
            self.assertIn("placeholder_supplier_url", reasons)

    def test_quality_gate_enforces_fulfillment_price_and_risk_policy(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        retail_price_usd=90,
                        shipping_days=10,
                        warehouse_country="CN",
                        inventory_status="unknown",
                        tracking_available="false",
                        moq=5,
                        risk_tags="supplement|fragile",
                    )
                ],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            reasons = set(summary["quality_review"]["rejected"][0]["reasons"])
            self.assertTrue(
                {
                    "retail_price_outside_policy",
                    "shipping_days_above_policy",
                    "warehouse_country_outside_market",
                    "inventory_status_not_approved",
                    "tracking_required",
                    "moq_above_policy",
                    "excluded_risk_tag:supplement",
                    "excluded_risk_tag:fragile",
                }.issubset(reasons)
            )

    def test_quality_gate_rejects_stale_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(evidence_checked_at="2000-01-01T00:00:00Z")],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertIn(
                "evidence_stale",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_quality_gate_rejects_unreconciled_margin_claim(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(margin_percent=80)],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertIn(
                "margin_claim_not_reconciled",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_noncomparable_supplier_margin_claim_is_advisory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(margin_percent=80, margin_basis="product_cost_only")],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 1)
            self.assertIn(
                "reported_margin_not_comparable:product_cost_only",
                summary["selected"][0]["quality_warnings"],
            )

    def test_quality_gate_rejects_failed_sample(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(sample_status="failed")],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertIn(
                "sample_failed",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_quality_gate_marks_passed_sample_organic_pilot_ready(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        sample_status="passed",
                        quality_evidence_status="sample_passed",
                        estimated_cac_usd=15,
                    )
                ],
            )

            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertEqual(summary["selected_count"], 1)
            self.assertEqual(summary["selected"][0]["quality_status"], "organic_pilot_ready")

    def test_paid_launch_requires_estimated_cac(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(sample_status="passed", quality_evidence_status="sample_passed")],
            )
            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="paid_launch",
                top_n=1,
                dry_run=True,
            )

            self.assertIn(
                "estimated_cac_required_for_paid_launch",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_paid_launch_rejects_estimated_cac_above_profitable_threshold(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [
                    viable_row(
                        sample_status="passed",
                        quality_evidence_status="sample_passed",
                        estimated_cac_usd=30,
                    )
                ],
            )
            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                qualification_mode="paid_launch",
                top_n=1,
                dry_run=True,
            )

            self.assertIn(
                "post_ad_contribution_percent_below_mode",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_quality_gate_rejects_sample_evidence_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row(sample_status="passed", quality_evidence_status="sample_pending")],
            )
            summary = run_product_research(
                catalog,
                database_path=root / "efficiens.db",
                launch_policy_path=policy,
                dry_run=True,
            )

            self.assertIn(
                "sample_quality_evidence_mismatch",
                summary["quality_review"]["rejected"][0]["reasons"],
            )

    def test_launch_policy_requires_production_catalog_columns(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = root / "catalog.csv"
            catalog.write_text(
                "product_name,supplier_name,margin_percent,shipping_days,demand_signal,saturation_signal\n"
                "Desk Organizer,Supplier A,60,5,75,30\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "production columns"):
                run_product_research(
                    catalog,
                    database_path=root / "efficiens.db",
                    launch_policy_path=policy,
                    dry_run=True,
                )

    def test_launch_policy_caps_sample_shortlist_size(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])

            with self.assertRaisesRegex(ValueError, "organic_sample max_shortlist_size 3"):
                run_product_research(
                    catalog,
                    database_path=root / "efficiens.db",
                    top_n=4,
                    launch_policy_path=policy,
                    dry_run=True,
                )

    def test_quality_status_and_economics_survive_canonical_write(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])
            database = root / "efficiens.db"

            run_product_research(
                catalog,
                database_path=database,
                launch_policy_path=policy,
                dry_run=True,
            )
            summary = run_product_research(
                catalog,
                database_path=database,
                launch_policy_path=policy,
            )

            self.assertEqual(summary["written"][0]["quality_status"], "organic_sample_candidate")
            self.assertAlmostEqual(
                summary["written"][0]["economics"]["pre_ad_contribution_percent"],
                52.5,
            )
            with CanonicalDB(database) as db:
                payload = json.loads(
                    db.connection.execute(
                        "SELECT payload_json FROM events WHERE event_type = ?",
                        ("workflow.product_research.candidate_selected",),
                    ).fetchone()[0]
                )
                metric_names = {
                    row[0]
                    for row in db.connection.execute(
                        "SELECT metric_name FROM metrics"
                    ).fetchall()
                }
                metric_dimensions = json.loads(
                    db.connection.execute(
                        "SELECT dimensions_json FROM metrics WHERE metric_name = ? LIMIT 1",
                        ("commerce.strategic_fit_score",),
                    ).fetchone()[0]
                )
            self.assertEqual(payload["quality_status"], "organic_sample_candidate")
            self.assertEqual(payload["qualification_mode"], "organic_sample")
            self.assertEqual(payload["margin_basis"], "landed_cost")
            self.assertEqual(payload["strategic_fit"]["thesis_id"], "creator-desk-reset")
            self.assertAlmostEqual(payload["economics"]["gross_margin_percent"], 60.0)
            self.assertIn("commerce.break_even_cac_usd", metric_names)
            self.assertIn("commerce.strategic_fit_score", metric_names)
            self.assertEqual(metric_dimensions["margin_basis"], "landed_cost")

    def test_production_evidence_refreshes_a_reused_legacy_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "efficiens.db"
            catalog = write_catalog(
                root / "catalog.csv",
                [viable_row()],
                fieldnames=RESEARCH_FIELDNAMES,
            )

            run_product_research(catalog, database_path=database, top_n=1, dry_run=True)
            legacy = run_product_research(catalog, database_path=database, top_n=1)
            self.assertEqual(legacy["written"][0]["action"], "inserted")
            self.assertEqual(generate_top_opportunities(database, 10, 0.0), [])

            policy = write_policy(root / "policy.json")
            write_catalog(root / "catalog.csv", [viable_row()])
            run_product_research(
                catalog,
                database_path=database,
                launch_policy_path=policy,
                top_n=1,
                dry_run=True,
            )
            promoted = run_product_research(
                catalog,
                database_path=database,
                launch_policy_path=policy,
                top_n=1,
            )

            self.assertEqual(promoted["written"][0]["action"], "reused")
            rows = generate_top_opportunities(database, 10, 0.0)
            self.assertEqual([row["name"] for row in rows], ["Compact Desk Organizer"])
            self.assertEqual(rows[0]["quality_status"], "organic_sample_candidate")
            self.assertEqual(rows[0]["thesis_id"], "creator-desk-reset")

    def test_write_requires_dry_run_with_same_launch_policy(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])
            database = root / "efficiens.db"

            run_product_research(
                catalog,
                database_path=database,
                launch_policy_path=policy,
                dry_run=True,
            )
            write_policy(policy, sample_budget_usd=200.0)

            with self.assertRaisesRegex(WorkflowPreflightError, "policy changed"):
                run_product_research(
                    catalog,
                    database_path=database,
                    launch_policy_path=policy,
                )

    def test_write_requires_dry_run_with_same_interest_profile(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            profile = root / "operator-interest-profile.json"
            catalog = write_catalog(root / "catalog.csv", [viable_row()])
            database = root / "efficiens.db"

            run_product_research(
                catalog,
                database_path=database,
                launch_policy_path=policy,
                dry_run=True,
            )
            write_profile(profile, minimum_strategic_fit_score=90.0)

            with self.assertRaisesRegex(WorkflowPreflightError, "Interest profile changed"):
                run_product_research(
                    catalog,
                    database_path=database,
                    launch_policy_path=policy,
                )

    def test_write_requires_dry_run_with_same_qualification_mode(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])
            database = root / "efficiens.db"

            run_product_research(
                catalog,
                database_path=database,
                launch_policy_path=policy,
                qualification_mode="organic_sample",
                top_n=1,
                dry_run=True,
            )

            with self.assertRaisesRegex(WorkflowPreflightError, "Qualification mode changed"):
                run_product_research(
                    catalog,
                    database_path=database,
                    launch_policy_path=policy,
                    qualification_mode="paid_launch",
                    top_n=1,
                )

    def test_cli_accepts_launch_policy_for_production_dry_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = write_policy(root / "policy.json")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])
            database = root / "efficiens.db"

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/product_research_workflow.py",
                    str(catalog),
                    "--database",
                    str(database),
                    "--launch-policy",
                    str(policy),
                    "--dry-run",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["selected"][0]["quality_status"], "organic_sample_candidate")

    def test_launch_policy_missing_required_fields_fails_clearly(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            incomplete = dict(POLICY)
            incomplete.pop("max_moq")
            policy = root / "policy.json"
            policy.write_text(json.dumps(incomplete), encoding="utf-8")
            catalog = write_catalog(root / "catalog.csv", [viable_row()])

            with self.assertRaisesRegex(ValueError, "missing required fields: max_moq"):
                run_product_research(
                    catalog,
                    database_path=root / "efficiens.db",
                    launch_policy_path=policy,
                    dry_run=True,
                )


if __name__ == "__main__":
    unittest.main()
