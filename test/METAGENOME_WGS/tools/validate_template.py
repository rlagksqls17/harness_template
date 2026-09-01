#!/usr/bin/env python3
"""Read-only validator for the minimal METAGENOME_WGS Agent harness."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ACTIVE = {
    "active_agent_communication",
    "active_agent_development",
    "active_agent_analysis",
    "active_agent_inspection",
    "active_agent_document",
    "active_agent_schedule",
}
EXPECTED_NON_ACTIVE = {"prompt_agent", "passive_agent", "improvement_agent", "supervisor_agent"}
EXPECTED_STAGES = ["preparation", "execution", "verification", "completion"]


class Validator:
    def __init__(self) -> None:
        self.passes: list[str] = []
        self.failures: list[str] = []

    def check(self, condition: bool, label: str, detail: str = "") -> None:
        if condition:
            self.passes.append(label)
        else:
            self.failures.append(f"{label}: {detail}" if detail else label)

    def json(self, relative: str) -> dict[str, Any]:
        path = ROOT / relative
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.check(isinstance(value, dict), f"json.object.{relative}")
            return value
        except Exception as exc:
            self.failures.append(f"json.parse.{relative}: {exc}")
            return {}


def load_harness():
    spec = importlib.util.spec_from_file_location("validated_metagenome_harness", ROOT / "src" / "harness.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import src/harness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(strict_runtime: bool) -> Validator:
    v = Validator()
    governance = v.json("system/immutable_governance.json")
    registry = v.json("system/role_registry.json")
    workflow = v.json("system/workflow_contract.json")
    router = v.json("system/function_router.json")
    state_example = v.json("system/system_state.example.json")
    evidence_example = v.json("system/execution_evidence.example.json")

    agents = registry.get("agents", [])
    ids = {entry.get("id") for entry in agents}
    active = {entry.get("id") for entry in agents if entry.get("kind") == "active"}
    v.check(ids == EXPECTED_ACTIVE | EXPECTED_NON_ACTIVE, "registry.exact_10_roles", f"got={sorted(ids)}")
    v.check(active == EXPECTED_ACTIVE, "registry.exact_6_active", f"got={sorted(active)}")
    v.check(registry.get("active_agent_count") == 6, "registry.active_count_6")
    v.check(registry.get("active_group_is_agent") is False, "registry.group_not_agent")
    v.check("active_agent_research" not in ids, "registry.research_not_registered")
    v.check(registry.get("mutation_policy", {}).get("fresh_context_validation_required") is True, "registry.fresh_supervisor_gate")

    for entry in agents:
        path = ROOT / str(entry.get("path", ""))
        v.check(path.exists(), f"role.exists.{entry.get('id')}")
        if not path.exists():
            continue
        role = v.json(str(path.relative_to(ROOT)).replace("\\", "/"))
        v.check(set(role) == {"id", "purpose", "roles", "contract", "paperthin_skills", "status"}, f"role.fields.{entry.get('id')}")
        v.check(role.get("id") == entry.get("id"), f"role.id.{entry.get('id')}")
        if entry.get("kind") == "active":
            stage_contract = role.get("contract", {}).get("stage_contract", {})
            v.check(set(stage_contract) == set(EXPECTED_STAGES), f"role.stages.{entry.get('id')}")

    legacy = v.json("Agent/2_Active_Agent/active_agent_research/role/role.json")
    v.check(legacy.get("status", {}).get("default_enabled") is False, "legacy.research_disabled")
    v.check(legacy.get("status", {}).get("implementation") == "legacy_unregistered_contract", "legacy.research_labeled")

    window = governance.get("operation_window_kst", {})
    v.check(window == {"start": "08:00", "end_exclusive": "22:00"}, "governance.operation_window")
    v.check(governance.get("workflow", {}).get("active_agent_count") == 6, "governance.active_count_6")
    v.check(governance.get("system_gate", {}).get("direct_dissatisfaction_precedes_agents_passive_and_recording") is True, "governance.dissatisfaction_first")
    v.check(governance.get("evidence", {}).get("routing_is_not_agent_execution") is True, "governance.route_not_execution")
    v.check(governance.get("evidence", {}).get("data_analysis_and_analysis_process_status_are_separate") is True, "governance.analysis_status_separate")
    v.check(governance.get("report", {}).get("explanation_order") == ["identity", "execution_actor", "actual_structure"], "governance.explanation_order")

    v.check(workflow.get("stage_model", {}).get("stages") == EXPECTED_STAGES, "workflow.four_internal_stages")
    v.check(workflow.get("record_policy", {}).get("explanation_only") == "no_file_without_explicit_request", "workflow.explanation_no_file")
    v.check(workflow.get("analysis_modes", {}).get("must_not_substitute_for_each_other") is True, "workflow.analysis_modes_distinct")

    routed_agents = {item.get("agent") for item in router.get("routes", [])}
    v.check(routed_agents == EXPECTED_ACTIVE, "router.exact_6_agents", f"got={sorted(routed_agents)}")
    v.check(router.get("rules", {}).get("selection_is_not_execution_evidence") is True, "router.selection_not_execution")

    v.check(state_example.get("status") == "CANDIDATE", "state.example_candidate")
    v.check(state_example.get("activation") == "fresh_supervisor_and_explicit_user_operation_only", "state.no_automatic_activation")
    v.check(evidence_example.get("kind") == "actual_execution", "evidence.example_actual_execution")
    v.check(evidence_example.get("ledger_entry_id") is not None, "evidence.example_ledger_binding")
    v.check(bool(evidence_example.get("worker_actor")), "evidence.example_worker_actor")
    v.check(bool(evidence_example.get("worker_signature_path")), "evidence.example_worker_signature")
    v.check(bool(evidence_example.get("task_digest")), "evidence.example_task_digest")
    v.check(bool(evidence_example.get("artifacts")), "evidence.example_artifact_binding")
    v.check(bool(evidence_example.get("artifact_manifest_digest")), "evidence.example_manifest_digest")
    v.check(bool(evidence_example.get("runtime_verifier_receipt")), "evidence.example_runtime_receipt")
    v.check((ROOT / "system" / "trusted_signers.allowed_signers").exists(), "evidence.trusted_signer_registry_exists")
    if strict_runtime:
        state = v.json("system/system_state.json")
        v.check(state.get("status") in {"INVALID", "CANDIDATE", "ACTIVE"}, "runtime.state_valid")
        for relative in ("system/current_task.yaml", "records/ledger.jsonl", "records/evidence_index.jsonl"):
            v.check((ROOT / relative).exists(), f"runtime.preserved.{relative}")

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "system/system_state.json",
        "system/current_task.yaml",
        "Agent/**/memory/current.json",
        "Agent/**/record/history.jsonl",
        "records/*.jsonl",
        "reviews/*.json",
        "supervisor_evidence/",
    ):
        v.check(pattern in gitignore, f"gitignore.{pattern}")

    try:
        harness = load_harness()
        v.check(harness.ACTIVE_AGENTS == EXPECTED_ACTIVE, "runtime.exact_6_active")
        with tempfile.TemporaryDirectory() as name:
            state_path = Path(name) / "state.json"
            state_path.write_text(json.dumps({"status": "ACTIVE", "epoch": 3}), encoding="utf-8")
            routed = harness.route_text("메타지놈 WGS 결과를 분석해줘", state_path, "2026-09-01T21:00:00+09:00")
            v.check(routed.get("allowed") is True, "runtime.bare_natural_language")
            v.check(routed.get("task_spec", {}).get("route", {}).get("analysis_mode") == "data_analysis", "runtime.data_analysis_route")
            dispatched = harness.dispatch_text("메타지놈 WGS 분석 프로세스 상태를 확인해줘", state_path, "2026-09-01T21:00:00+09:00")
            v.check(dispatched.get("agent_execution_status") == "not_started", "runtime.dispatch_not_execution")
            invalid = harness.route_text("왜 Agent가 또 실행하지 않은 걸 완료라고 해?", state_path, "2026-09-01T21:00:00+09:00")
            v.check(invalid.get("reason") == "direct_user_dissatisfaction", "runtime.direct_dissatisfaction")
    except Exception as exc:
        v.failures.append(f"runtime.import_or_smoke: {exc}")

    v.check((ROOT / "tests" / "test_harness.py").exists(), "tests.regression_suite_exists")
    return v


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-runtime", action="store_true")
    args = parser.parse_args()
    result = validate(args.strict_runtime)
    for item in result.failures:
        print(f"FAIL {item}")
    print(f"checks_passed={len(result.passes)} failures={len(result.failures)}")
    print("runtime_engine=INCLUDED")
    print("actual_agent_work=NOT_VERIFIED")
    print("scientific_pass=NOT_VERIFIED")
    if result.failures:
        print("FINAL_TEMPLATE_FAIL")
        return 1
    print("FINAL_TEMPLATE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
