"""Static and runtime smoke validation for prompt_agent_dev."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "src" / "harness.py"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require(condition: bool, label: str, detail: str = "") -> None:
    if not condition:
        suffix = f" | {detail}" if detail else ""
        raise AssertionError(f"{label}{suffix}")
    print(f"[PASS] {label}")


def load_harness():
    spec = importlib.util.spec_from_file_location("prompt_agent_harness", HARNESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load harness")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def active_state() -> dict:
    return {
        "schema_version": 1,
        "status": "ACTIVE",
        "epoch": 7,
        "active_build_id": "test-build",
        "candidate_build_id": None,
        "invalidated_at": None,
        "invalidation_reason": None,
        "supervisor_score": 90,
        "paperthin_philosophy_score": 80,
        "updated_at": "2026-08-29T08:00:00+09:00",
        "one_time_grant": None,
    }


def write_fabricated_execution_evidence(harness, path: Path, task_spec: dict, token: str) -> Path:
    contract = task_spec["execution_contract"]
    agents = [
        "0_Prompt_Agent",
        "1_Passive_Agent",
        *contract["active_agents_required"],
        "S_Supervisor_Agent",
    ]
    receipts = []
    for index, agent in enumerate(agents, start=1):
        if agent == "1_Passive_Agent":
            summary = {
                "status": "completed",
                "elements": contract["required_passive_elements"],
                "context_statuses": {
                    element: "found" for element in contract["required_passive_elements"]
                },
                "source_count": len(contract["required_passive_elements"]),
            }
        elif agent.startswith("active_agent_"):
            summary = {
                "status": "completed",
                "stages": [
                    {"stage": stage, "status": "completed", "evidence": "unit-test"}
                    for stage in ("preparation", "execution", "verification", "completion")
                ],
                "artifact": None,
                "verification": {"server_writes": [], "network_hosts": []},
                "reason": None,
            }
        elif agent == "S_Supervisor_Agent":
            summary = {"status": "completed", "decision": "PASS", "score": 96, "reasons": []}
        else:
            summary = {"status": "completed"}
        receipts.append(
            {
                "sequence": index,
                "agent": agent,
                "invocation_id": f"inv-{index:02d}-{index:016x}",
                "process_id": 1000 + index,
                "started_at": f"2026-09-03T01:00:{(index - 1) * 2:02d}+09:00",
                "ended_at": f"2026-09-03T01:00:{(index - 1) * 2 + 1:02d}+09:00",
                "exit_code": 0,
                "input_digest": str(index) * 64,
                "output_digest": str((index + 1) % 10) * 64,
                "status": "completed",
                "output_summary": summary,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    events_path = path.parent / "events.jsonl"
    events_text = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in receipts
    )
    harness.atomic_write_text(events_path, events_text)
    evidence = {
            "schema_version": 1,
            "run_id": "unit-evidence",
            "task_revision": task_spec["revision"],
            "task_spec_digest": harness.task_spec_digest(task_spec),
            "invocations": receipts,
            "safety": {
                "network_hosts": [],
                "network_policy": "github_only",
                "server_access": "read_only",
                "server_write_attempted": False,
                "reasons": [],
            },
            "events_sha256": harness.hashlib.sha256(events_path.read_bytes()).hexdigest(),
            "created_at": "2026-09-03T01:00:02+09:00",
        }
    evidence["signature"] = harness.execution_evidence_signature(evidence, token)
    harness.atomic_write_json(path, evidence)
    return path


def main() -> int:
    registry = load_json(ROOT / "system" / "role_registry.json")
    governance = load_json(ROOT / "system" / "governance.json")
    state = load_json(ROOT / "system" / "system_state.json")
    router = load_json(ROOT / "system" / "function_router.json")

    agents = registry["agents"]
    require(len(agents) == 10, "registered_agents=10")
    active_agents = [item for item in agents if item["kind"] == "active"]
    require(len(active_agents) == 6, "active_agents=6")
    require(registry["active_group_is_agent"] is False, "active_group_is_not_agent")

    missing_roles = [item["id"] for item in agents if not (ROOT / item["path"] / "role.json").is_file()]
    require(not missing_roles, "all_role_files_exist", str(missing_roles))

    for item in agents:
        role = load_json(ROOT / item["path"] / "role.json")
        prohibited = set(role.get("prohibited", []))
        if item["id"] != "S_Supervisor_Agent":
            require(
                {"modify_role_registry", "modify_role_definitions"} <= prohibited,
                f"role_read_only:{item['id']}",
            )
    supervisor = load_json(ROOT / "src" / "Supervisor_Agent" / "role.json")
    require("apply_approved_role_change" in supervisor["allowed"], "supervisor_is_sole_role_mutator")

    require(governance["state_machine"]["activation_requires"]["independent_supervisor_score_min"] == 90, "supervisor_gate=90")
    require(governance["state_machine"]["activation_requires"]["paperthin_and_user_philosophy_score_min"] == 80, "philosophy_gate=80")
    record_retention = governance["record_retention"]
    require(record_retention["owner"] == "1_Passive_Agent", "record_retention_owned_by_passive")
    require(record_retention["preserve_previous_record_versions"] is True, "previous_record_versions_preserved")
    require(record_retention["append_only_history"] is True, "record_history_append_only")
    require(record_retention["destructive_in_place_migration"] is False, "record_in_place_migration_forbidden")
    require(record_retention["legacy_reader_required"] is True, "record_legacy_reader_required")
    interaction = governance["interaction"]
    require(
        interaction["mid_turn_types"] == ["correction", "addition", "direct_dissatisfaction"],
        "system_wide_mid_turn_types",
    )
    require(interaction["all_agents_recheck_task_revision_before_output"] is True, "all_agents_task_revision_gate")
    require(interaction["routing_is_not_execution_evidence"] is True, "routing_not_execution_contract")
    one_time_policy = interaction["one_time_user_override"]
    require(one_time_policy["allowed_base_state"] == "INVALID", "one_time_override_requires_invalid")
    require(one_time_policy["changes_system_status"] is False, "one_time_override_does_not_activate")
    require(one_time_policy["consumed_by"] == "successful_pre_output_gate", "one_time_override_consumption_gate")
    safety_policy = governance["safety"]
    require(safety_policy["priority"] == 1, "safety_policy_priority_one")
    require(safety_policy["network_policy"] == "github_only", "network_github_only")
    require(safety_policy["server_access"] == "read_only", "server_read_only")
    runtime_execution = governance["runtime_execution"]
    require(runtime_execution["each_agent_separate_process"] is True, "runtime_agents_separate_processes")
    require(runtime_execution["supervisor_pass_required_before_output"] is True, "runtime_supervisor_before_output")
    require(runtime_execution["user_text_never_shell_executed"] is True, "runtime_user_text_not_shell_executed")
    require(runtime_execution["worker_entrypoints_allowlisted"] is True, "runtime_worker_entrypoints_allowlisted")
    require(runtime_execution["subprocess_shell"] is False, "runtime_subprocess_shell_false")
    require(
        runtime_execution["runtime_attestation"] == "process_local_single_use_and_evidence_digest_bound",
        "runtime_attestation_single_use",
    )
    runtime_monitor = governance["runtime_monitor"]
    require(runtime_monitor["source"] == "actual_orchestrator_lifecycle_events", "runtime_monitor_actual_event_source")
    require(runtime_monitor["bind"] == "127.0.0.1", "runtime_monitor_loopback_only")
    require(runtime_monitor["read_only_get"] is True and runtime_monitor["write_endpoints"] is False, "runtime_monitor_read_only")
    require(runtime_monitor["schedule_display"]["actual_scheduler"] == "external_not_connected", "runtime_monitor_schedule_truth_boundary")
    dashboard_source = (ROOT / "system" / "harness_structure.html").read_text(encoding="utf-8")
    require("/api/snapshot" in dashboard_source and "setInterval(refresh, 1000)" in dashboard_source, "runtime_dashboard_live_snapshot_poll")
    require("https://" not in dashboard_source and "http://" not in dashboard_source, "runtime_dashboard_no_external_dependency")
    require("외부 업무 완료·서버 변경·사용자 승인을 뜻하지 않습니다" in dashboard_source, "runtime_dashboard_truth_boundary_visible")
    require("독립 Supervisor PASS" not in dashboard_source, "runtime_dashboard_no_preapproval_supervisor_claim")
    review_manifest = load_json(ROOT / "system" / "review_manifests" / "2026-09-03-all-codex.json")
    require(review_manifest["coverage"]["user_participating_threads"] == 7, "nightly_review_manifest_threads=7")
    require(review_manifest["coverage"]["user_turns"] == 127, "nightly_review_manifest_user_turns=127")
    require(review_manifest["coverage"]["truncated_threads"] == 0, "nightly_review_manifest_no_truncation")
    require(review_manifest["coverage"]["access_failures"] == 0, "nightly_review_manifest_no_access_failure")
    require(review_manifest["coverage"]["account_wide_claim"] is False, "nightly_review_manifest_honest_scope")
    reviewed_threads_digest = hashlib.sha256(
        json.dumps(
            review_manifest["threads"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    require(
        reviewed_threads_digest == review_manifest["discovery_proof"]["coverage_set_sha256"],
        "nightly_review_manifest_coverage_digest",
    )
    require(governance["report"]["answer_scope"] == "exact_question_before_adjacent_context", "report_exact_question_first")
    require(governance["report"]["scheduled_run_always_reports"] is True, "scheduled_run_always_reports")
    prompt_contract = (ROOT / "src" / "Prompt_Agent" / "prompt.yaml").read_text(encoding="utf-8")
    require("답변 완료만으로 프로젝트별 대화 기록이나 prompt/answer 파일을 만들지 않는다" in prompt_contract, "no_automatic_prompt_answer_files")
    require("정체, 실행 주체, 실제 구조 순서" in prompt_contract, "explanation_abstraction_contract")
    require("대상과 목적, 핵심 용어, 실제 예시, 실패하거나 헷갈리는 경계, 그 이유, 짧은 요약 순" in prompt_contract, "learning_explanation_contract")
    require("해당 TaskSpec 하나에만 일회용 권한" in prompt_contract, "prompt_one_time_override_contract")
    require(state["status"] in {"ACTIVE", "INVALID", "REBUILDING", "CANDIDATE"}, "runtime_state_valid")
    if state["status"] == "INVALID":
        runtime_grant = state.get("one_time_grant")
        if runtime_grant is None:
            require(True, "runtime_invalid_has_no_open_one_time_grant")
        else:
            require(isinstance(runtime_grant, dict), "runtime_open_one_time_grant_is_object")
            require(runtime_grant.get("epoch") == state["epoch"], "runtime_open_one_time_grant_epoch_matches")
            require(
                isinstance(runtime_grant.get("token_hash"), str)
                and len(runtime_grant["token_hash"]) == 64,
                "runtime_open_one_time_grant_stores_hash_only",
            )
            require(
                type(runtime_grant.get("task_revision")) is int
                and runtime_grant["task_revision"] > 0,
                "runtime_open_one_time_grant_revision_valid",
            )

    skill_entries = router.get("skills", [])
    skill_names = {item["skill"] for item in skill_entries}
    installed = {path.name for path in (ROOT / ".agents" / "skills").iterdir() if path.is_dir()}
    require(len(skill_entries) == 28 and len(skill_names) == 28, "paperthin_routes=28")
    require(installed == skill_names, "paperthin_vendored_matches_router")
    require((ROOT / "third_party" / "paperthin" / "LICENSE").is_file(), "paperthin_license_present")

    harness = load_harness()
    for text in (
        "IP 개념을 모르겠어. 처음부터 설명해줘",
        "이 명령어가 무슨 뜻인지 쉽게 설명해줘",
        "(Invoke-RestMethod api.ipify.org).Trim() 지금 이게 무슨 명령어인데?",
        "이 값이 왜 맞는지 원리부터 알려줘",
    ):
        learning = harness.response_contract(text)
        require(learning["mode"] == "learning_explanation", f"learning_report_selected:{text}")
        require(learning["answer_scope"] == "exact_user_question_then_required_concept_boundary", f"learning_report_exact_scope:{text}")
        require(
            learning["abstraction_order"]
            == ["object_and_purpose", "terms", "concrete_example", "failure_boundary", "rationale", "short_summary"],
            f"learning_report_order:{text}",
        )
    concise_learning = harness.response_contract("개념을 모르겠어. 핵심만 설명해줘")
    require(concise_learning["mode"] == "explanation", "explicit_concise_overrides_learning_mode")
    ordinary = harness.response_contract("현재 구조를 설명해줘")
    require(ordinary["mode"] == "explanation", "ordinary_report_remains_concise")
    require(ordinary["answer_scope"] == "exact_user_question_only", "ordinary_report_exact_question_only")
    governed_digest_paths = {
        path.relative_to(ROOT).as_posix() for path in harness.candidate_content_paths(ROOT)
    }
    for relative in (
        ".gitattributes",
        "third_party/paperthin/LICENSE",
        "third_party/paperthin/NOTICE",
        "src/Prompt_Agent/prompt.yaml",
        "src/Passive_Agent/record_Agent/record.yaml",
        "src/Active_Agent/passive_query.yaml",
        "src/Active_Agent/communication_Agent/employee_email.yaml",
        "system/harness_structure.html",
    ):
        require(relative in governed_digest_paths, f"candidate_digest_includes:{relative}")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    require("system/executions/** text eol=lf" in attributes, "runtime_evidence_git_eol_stable")
    require(harness.is_negative_feedback("이상해. 왜 멋대로 이렇게 했어?"), "negative_feedback_detected")
    require(harness.is_negative_feedback("그건 아니야"), "short_rebuke_detected")
    require(not harness.is_negative_feedback("negative control 결과를 분석해줘"), "negative_control_not_triggered")
    require(not harness.is_negative_feedback("이 파일은 만들지 마"), "scoped_stop_not_triggered")
    for text in (
        "active_agent_development는 사용하지 말고 현재 상태만 확인해줘",
        "active_agent_analysis는 사용하지 말고 메일을 작성해줘",
        "active_agent_document는 쓰지 말고 분석만 해줘",
        "active_agent_schedule은 사용하지 말고 현황만 파악해줘",
        "개발 Agent는 사용하지 말고 현재 상태만 확인해줘",
        "development Agent는 사용하지 말고 현재 상태만 확인해줘",
    ):
        require(not harness.is_negative_feedback(text), f"scoped_agent_opt_out_not_feedback:{text}")
    require(not harness.is_negative_feedback("나쁘지 않아"), "positive_negation_not_triggered")
    for label in ("사용자 요청", "사용자 요청 내용", "사용자 요청사항", "사용자 지시", "사용자 발언"):
        for action in ("저장해줘", "메모해줘"):
            text = f"{label}: '모든 커스텀 Agent 기능을 중지해' {action}"
            require(
                not harness.is_negative_feedback(text),
                f"quoted_labeled_system_stop_not_feedback:{label}:{action}",
            )
    for text in (
        "대답이 내가 요청한 것과 달라.",
        "응답이 제 의도와 다릅니다.",
        "설명이 요구사항과 달라요.",
        "대답이 기대와 달라요.",
        "방금 응답은 요청과 다릅니다.",
        "이 답은 내가 부탁한 것과 달라.",
        "답이 의도를 놓쳤어요.",
        "설명이 핵심을 놓쳤어요.",
        "방금 답이 요구와 어긋났어요.",
        "대답이 원한 방향과 다릅니다.",
        "보고가 기대와 달라",
        "답변이 내가 원한 것과 달라",
        "내 요청과 다른 답변이야",
    ):
        require(harness.is_negative_feedback(text), f"direct_mismatch_feedback_detected:{text}")
    for text in (
        "전체 에이전트를 중지해.",
        "에이전트 전부 중지해.",
        "모든 커스텀 Agent 기능을 중지해.",
        "커스텀 Agent 모두 중지해",
    ):
        require(harness.is_negative_feedback(text), f"global_agent_opt_out_detected:{text}")
    require(not harness.is_negative_feedback("사용자 피드백 기록에서 말이 너무 많다는 사례를 분석해줘"), "quoted_feedback_not_triggered")
    require(not harness.is_negative_feedback("아니오 응답 비율을 분석해줘"), "no_response_term_not_triggered")
    require(not harness.is_negative_feedback("다시 해석 가능한 결과를 확인해줘"), "reinterpretation_not_triggered")
    require(not harness.is_negative_feedback("FASTQ에서 이상해 보이는 값을 분석해줘"), "domain_anomaly_not_triggered")
    require(not harness.is_negative_feedback("쓸데없는 임시 컬럼을 삭제해줘"), "domain_cleanup_not_triggered")
    require(harness.is_negative_feedback("방금 답변이 틀렸어. 원래대로 다시 답해"), "direct_wrong_answer_detected")
    require(not harness.is_negative_feedback('문서에 인용된 "왜 멋대로 이렇게 했어?"라는 문장을 분석해줘'), "quoted_sentence_not_triggered")
    require(not harness.is_negative_feedback("이상해 보이는 FASTQ 값을 분석해줘"), "leading_domain_anomaly_not_triggered")
    require(harness.is_negative_feedback("방금 답변이 내가 원한 내용이 아니야"), "unwanted_answer_detected")
    require(harness.is_negative_feedback("답변이 마음에 들지 않아"), "disliked_answer_detected")
    require(harness.is_negative_feedback("방금 네 답변 정말 별로야"), "poor_answer_detected")
    require(harness.is_negative_feedback("이 답변은 완전 엉망이야"), "mangled_answer_detected")
    require(harness.is_negative_feedback("네 설명이 도움이 하나도 안 돼"), "unhelpful_explanation_detected")
    require(harness.is_negative_feedback("내 요청과 다르게 답했어. 다시 써"), "request_mismatch_detected")
    for text in (
        "이 답변은 기대 이하야",
        "답변 품질이 너무 낮아",
        "제 질문에 제대로 답하지 않았어요",
        "답변이 너무 산만합니다",
        "질문에 답을 안 했잖아",
    ):
        require(harness.is_negative_feedback(text), f"natural_direct_dissatisfaction:{text}")
    for text in (
        "내가 쓴 설명이 틀렸는지 검증해줘",
        "내 답변이 장황한지 평가해줘",
        "외부 문서의 답변이 엉망인지 분석해줘",
        "내 답변이 별로야?",
        "내가 쓴 설명이 별로야?",
        "제가 작성한 보고가 장황한가요?",
        "제가 직접 쓴 설명이 별로인지 봐줄래?",
    ):
        require(not harness.is_negative_feedback(text), f"authored_or_external_evaluation_not_feedback:{text}")
    for text in (
        "이 답이 기대했던 거랑 다르네",
        "이번 설명은 기대한 거랑 달라요",
        "방금 답은 내가 기대했던 내용과 좀 다르네",
        "지금 네 설명은 내가 기대한 방향과 달라요",
        "왜 이 답변은 기대와 다른 거죠?",
        "이 답은 제가 부탁한 형식이 아니에요.",
        "답변의 깊이가 요청했던 수준에 못 미칩니다.",
        "방금 답변 톤이 제가 요청한 것과 어긋났습니다.",
        "이번 답은 기대했던 수준에 못 미쳐요.",
        "방금 설명이 내가 요청한 형식이 아닙니다.",
        "지금 답변은 부탁드린 방향과 전혀 달라요.",
        "응답 깊이가 제가 원했던 것보다 얕습니다.",
        "한국어 표현을 활용해 달랬는데 전혀 반영하지 않았네요.",
        "요청한 내용을 제대로 반영하지 못했어요.",
        "제가 기대한 답변 수준과는 거리가 있네요.",
        "부탁한 형식대로 안 썼잖아요.",
        "방금 답은 제가 요청드린 톤이 아니에요.",
        "내가 원한 방향이랑 완전히 다르게 갔어.",
        "이 설명은 부탁드린 깊이가 아닙니다.",
        "이번 응답, 기대랑 완전 다르잖아요.",
    ):
        require(harness.is_negative_feedback(text), f"expectation_mismatch_feedback:{text}")
    for text in (
        "다른 모델의 답변이 기대한 수준과 달라 보이는데 정확도만 검증해줘.",
        "질문: 답변이 기대한 수준과 다르면 무엇을 점검해야 하나?",
        '"답변이 기대한 방향과 달라"라는 문장을 중립적으로 바꿔줘.',
        "회의록에는 답변이 기대한 형식과 다르다고 적혀 있어. 요약해줘.",
        "회의록 평가: 고객이 설명 깊이가 부족하다고 했는지 확인해줘.",
        "회의록 평가 고객이 설명 깊이가 부족하다고 했는지 확인해줘.",
    ):
        require(not harness.is_negative_feedback(text), f"external_or_meta_expectation_not_feedback:{text}")
    require(not harness.is_negative_feedback("API 응답이 틀렸는지 확인해줘"), "api_response_not_triggered")
    require(not harness.is_negative_feedback("서버 응답이 잘못됐는지 조사해줘"), "server_response_not_triggered")
    require(not harness.is_negative_feedback("고객 응답이 틀렸는지 분석해줘"), "customer_response_evaluation_not_triggered")
    for subject in ("고객", "상사", "직원", "담당자", "사용자"):
        for response_word in ("답변", "응답"):
            for action in ("분석", "확인", "평가", "검증", "분류"):
                text = f"{subject} {response_word}이 장황했는지 {action}해줘"
                require(
                    not harness.is_negative_feedback(text),
                    f"third_party_verbose_response_not_feedback:{subject}:{response_word}:{action}",
                )
    for subject in ("고객", "상사", "직원", "담당자", "사용자", "API", "서버", "제품"):
        for artifact in ("답변", "응답", "설명", "보고"):
            for action in ("분석", "확인", "평가", "검증", "분류"):
                text = f"{subject} {artifact}가 장황하거나 별로인지 {action}해줘"
                require(
                    not harness.is_negative_feedback(text),
                    f"external_quality_evaluation_not_feedback:{subject}:{artifact}:{action}",
                )
    for subject, artifact in (
        ("고객", "답변"), ("상사", "응답"), ("다른 모델의", "답변"),
        ("API", "응답"), ("서버", "보고"), ("제품", "답변"),
    ):
        for action in ("분석", "확인", "평가", "분류", "요약"):
            text = f"{subject} {artifact}이 엉망인지 {action}해줘"
            require(
                not harness.is_negative_feedback(text),
                f"external_mess_evaluation_not_feedback:{subject}:{artifact}:{action}",
            )
    require(not harness.is_negative_feedback('고객이 "답변이 마음에 들지 않아"라고 한 내용을 요약해줘'), "customer_quote_not_triggered")
    require(harness.is_negative_feedback("그건 틀렸어."), "short_wrong_rebuttal_detected")
    require(harness.is_negative_feedback("아니야. 내가 말한 건 그게 아니야."), "direct_no_rebuttal_detected")
    require(harness.is_negative_feedback("네가 틀렸어. 다시 해."), "direct_agent_wrong_detected")
    require(harness.is_negative_feedback("이렇게 하지 말랬잖아."), "prior_instruction_rebuttal_detected")
    require(not harness.is_negative_feedback("이 보고서가 틀렸는지 분석해줘"), "report_domain_not_triggered")
    require(not harness.is_negative_feedback("그 설명 변수가 잘못됐는지 분석해줘"), "explanatory_variable_not_triggered")
    for text in (
        "아냐.",
        "그거 아니야.",
        "틀렸어.",
        "잘못했어.",
        "별로야.",
        "마음에 안 들어.",
        "너무 길어.",
        "이건 아니지.",
    ):
        require(harness.is_negative_feedback(text), f"terse_rebuttal:{text}")
    require(
        not harness.is_negative_feedback("아니, 보고서 말고 분석해."),
        "correction_with_replacement_not_dissatisfaction",
    )
    require(not harness.is_negative_feedback("논문 설명이 틀렸는지 검증해줘."), "paper_explanation_not_triggered")
    require(not harness.is_negative_feedback("실험 보고가 틀렸는지 분석해줘."), "experiment_report_not_triggered")
    require(not harness.is_negative_feedback("결제 시스템이 잘못 분류한 주문을 조사해줘."), "payment_system_not_triggered")
    require(harness.is_negative_feedback("이게 아니야."), "this_is_not_it_detected")
    require(harness.is_negative_feedback("답변 너무 길어."), "bare_answer_too_long_detected")
    require(harness.is_negative_feedback("이 답변 별론데"), "colloquial_bad_answer_detected")
    require(harness.is_negative_feedback("답변 구린데"), "colloquial_ugly_answer_detected")
    for text in ("답변이 구리다", "이 답 개판이네", "답변 망했네", "노답", "최악"):
        require(harness.is_negative_feedback(text), f"colloquial_dissatisfaction_detected:{text}")
    for text in (
        "마음에 안 드네요",
        "도움이 안 되네요",
        "도움이 안 됩니다.",
        "제 요청을 반영하지 않았습니다.",
        "말이 너무 많아.",
        "말이 너무 많습니다.",
        "설명이 만족스럽지 않네요",
        "답변이 좀 아쉽네요",
        "이거 별론데요",
        "방금 건 좀 구린데요",
        "이 답변 완전 노답이네요",
        "별론데요.",
        "좀 별론데요.",
        "아쉽네요.",
    ):
        require(harness.is_negative_feedback(text), f"polite_dissatisfaction_detected:{text}")
    require(
        not harness.is_negative_feedback("고객이 답변이 구리다고 한 내용을 분류해줘"),
        "third_party_colloquial_not_feedback",
    )
    for text in ("고객 답변이 구린지 평가해줘", "상사 응답이 장황한지 분석해줘"):
        require(not harness.is_negative_feedback(text), f"third_party_evaluation_not_feedback:{text}")
    require(
        not harness.is_negative_feedback("제품 후기가 최악인지 분석해줘"),
        "product_review_not_feedback",
    )
    for text in ("결제 시스템이 별로인지 분석해줘", "제품 설명이 별로인지 분석해줘"):
        require(not harness.is_negative_feedback(text), f"domain_evaluation_not_feedback:{text}")
    require(
        not harness.is_negative_feedback("에이전트 시스템 꺼짐 원인을 분석해줘"),
        "agent_outage_analysis_not_feedback",
    )
    require(
        not harness.is_negative_feedback("원래 Codex로 작성된 답변을 분석해줘"),
        "codex_authored_answer_analysis_not_feedback",
    )
    require(harness.is_negative_feedback("원래 코덱스로 돌아가."), "return_to_default_codex_detected")
    require(harness.is_negative_feedback("기본 Codex로 답해"), "bare_default_codex_detected")
    require(harness.is_negative_feedback("기본 방식으로 바로 답해"), "bare_default_mode_detected")
    for text in (
        "사용자가 기본 Codex로 답해 달라고 요청했다는 기록을 분류해줘",
        "회의록에 기본 Codex로 답해 달라는 요청이 있었다. 내용을 요약해줘",
        "고객 요청: 기본 Codex로 답해",
        "사용자가 '기본 Codex로 답해'라고 말했다",
        "고객 요청 내용: 모든 커스텀 Agent 기능을 중지해",
        "회의록에 원래 Codex로 돌아가라고 적혀 있다",
    ):
        require(not harness.is_negative_feedback(text), f"reported_default_codex_not_feedback:{text}")
    reported_sources = (
        "회의록", "회의 기록", "발언록", "작업 기록", "로그", "문서", "보고서", "티켓",
        "고객 요청", "사용자 요청 내용",
    )
    reported_directives = (
        "모든 커스텀 Agent 기능을 중지하라고 했다",
        "에이전트 시스템을 꺼 달라고 말했다",
        "원래 Codex로 돌아가라고 적혀 있다",
        "전체 에이전트를 중지하라는 기록이 있다",
        "커스텀 에이전트를 쓰지 말라는 의견이 있었다",
    )
    for source in reported_sources:
        for directive in reported_directives:
            text = f"{source}에 {directive}"
            require(not harness.is_negative_feedback(text), f"reported_global_request_not_feedback:{text}")
            require(
                not harness.is_negative_feedback(f"{text} 내용을 요약해줘"),
                f"reported_global_request_summary_not_feedback:{text}",
            )
    require(
        harness.is_negative_feedback(
            "사용자 요청 내용에 기본 Codex로 답해 달라는 요청이 있었다. 그대로 실행해줘"
        ),
        "reported_request_explicit_execution_is_current_instruction",
    )
    require(harness.is_negative_feedback("에이전트 시스템 꺼줘."), "turn_off_agent_system_detected")
    require(not harness.is_negative_feedback("제품 설명이 틀렸는지 검증해줘."), "product_explanation_not_triggered")
    require(not harness.is_negative_feedback("실험 보고가 누락됐는지 확인해줘."), "missing_experiment_report_not_triggered")
    for text in (
        "커스텀 에이전트 쓰지 마",
        "원래 Codex로 바로 답해",
        "시스템 꺼",
        "답이 왜 이래?",
    ):
        require(harness.is_negative_feedback(text), f"explicit_opt_out:{text}")
    for text in (
        "커스텀 에이전트 전부 꺼.",
        "이 Agent 시스템을 비활성화해.",
        "커스텀 시스템 사용을 중지해.",
        "에이전트 없이 기본 방식으로 답해.",
        "이 답은 별로다.",
        "네 답이 형편없어.",
        "방금 답 완전히 실패야.",
        "내 의도랑 전혀 다르게 답했어.",
        "이 대답은 쓸모가 없어.",
    ):
        require(harness.is_negative_feedback(text), f"r8_bypass_closed:{text}")
    for text in (
        "커스텀 에이전트를 더는 사용하지 마.",
        "에이전트 시스템은 이제 사용하지 말아 줘.",
        "모든 에이전트 기능을 정지해.",
        "별론데.",
        "맘에 안 든다.",
        "형편없네.",
        "쓸모없네.",
        "구려.",
    ):
        require(harness.is_negative_feedback(text), f"r9_bypass_closed:{text}")
    require(not harness.is_negative_feedback("다른 AI의 답변이 틀렸는지 검증해줘."), "other_ai_answer_not_triggered")
    for text in (
        "에이전트 모드는 끄고 기본 Codex로 답해.",
        "자체 에이전트 기능을 전부 중단하고 원래 방식으로 답해.",
        "원래 Codex 모드로 돌아가.",
        "맘에 안 들어.",
        "이 답 구려.",
        "설명 너무 장황해.",
        "이건 완전 쓸모없다.",
    ):
        require(harness.is_negative_feedback(text), f"r10_bypass_closed:{text}")
    for text in (
        "API 응답이 별로인 원인을 조사해줘.",
        "고객의 응답이 형편없다는 의견을 분류해줘.",
        "다른 모델의 응답이 형편없는지 평가해줘.",
    ):
        require(not harness.is_negative_feedback(text), f"r10_domain_control:{text}")
    require(harness.is_negative_feedback("별로네."), "r11_terse_dissatisfaction_closed")
    require(harness.is_negative_feedback("답변이 불만족스러워."), "r11_explicit_dissatisfaction_closed")
    require(
        harness.durable_record_intent(
            "Omixprep /data4 경로는 질병청 raw 경로야. 앞으로 관련 업무에서 참조해."
        ),
        "implicit_durable_record_intent",
    )
    require(
        harness.durable_record_intent("Omixprep 기준 경로는 C:\\work\\omixprep이야."),
        "confirmed_declaration_record_intent",
    )
    durable_subjects = (
        "기준 경로", "현재 경로", "최종 경로", "입력 경로", "출력 경로", "저장 경로",
        "서버", "호스트", "포트", "Docker 이미지", "환경", "환경변수", "버전", "규칙",
        "정책", "승인 범위", "검증 기준", "담당자", "마감일", "일정",
    )
    for subject in durable_subjects:
        for ending in ("확정했다", "결정했다"):
            text = f"{subject}는 운영값으로 {ending}."
            require(
                harness.durable_record_intent(text),
                f"confirmed_past_tense_record_intent:{subject}:{ending}",
            )
    for text, label in (
        ("기준 경로는 어디야?", "record_question_not_selected"),
        ("가정: 기준 경로는 C:\\tmp일 수 있어.", "record_hypothesis_not_selected"),
        ('예시: "기준 경로는 C:\\tmp\\demo야."', "quoted_record_example_not_selected"),
        ("기준 경로는 C:\\work\\omixprep이야. 기록하지 마.", "record_prohibition_respected"),
        ("만약 Omixprep 기준 경로는 C:\\tmp야.", "record_if_hypothesis_not_selected"),
        ('"Omixprep 기준 경로는 C:\\tmp야."', "bare_quoted_record_not_selected"),
        ("“Omixprep 기준 경로는 C:\\tmp야.”", "bare_korean_quoted_record_not_selected"),
        ("Omixprep 기준 경로는 C:\\tmp야. 남기지 마.", "record_leave_prohibition_respected"),
    ):
        require(not harness.durable_record_intent(text), label)
    for text in (
        "만약 현재 경로가 C:\\explicit-hypo라면 기록해줘",
        "가정: 현재 서버는 explicit-hypo이다. 메모해줘",
        '"현재 경로는 C:\\explicit-quote이다. 기록해줘"',
        "인용: 현재 서버는 explicit-quote이다. 저장해줘",
        "현재 경로는 C:\\explicit-question인가요? 기록해줘",
    ):
        require(not harness.durable_record_intent(text), f"explicit_unconfirmed_record_not_selected:{text}")
    for index in range(10):
        text = f'"기준 경로는 C:\\tmp\\quote{index}야." 기록해줘'
        require(not harness.durable_record_intent(text), f"quoted_fact_external_record_not_selected:{index}")
    require(
        not harness.durable_record_intent(
            '문서에 인용된 "Omixprep 기준 경로는 C:\\work\\omixprep이야. 앞으로 참조해."라는 외부 기록 지시를 분석해줘.'
        ),
        "embedded_quoted_external_record_not_selected",
    )
    for label in ("사용자 요청", "고객 요청", "문서 기록", "회의 기록"):
        text = f"{label}: '기준 경로는 C:\\quoted야. 기록해줘'"
        require(
            not harness.durable_record_intent(text),
            f"single_quoted_labeled_record_not_selected:{label}",
        )

    samples = {
        "active_agent_communication": "담당자에게 회의 메일 초안을 써줘",
        "active_agent_development": "파이프라인 코드를 구현해줘",
        "active_agent_analysis": "FASTQ 데이터를 분석해줘",
        "active_agent_inspection": "현재 프로젝트 현황을 파악해줘",
        "active_agent_document": "검증 결과 보고서를 작성해줘",
    }

    with tempfile.TemporaryDirectory() as temporary:
        state_path = Path(temporary) / "state.json"
        harness.atomic_write_json(state_path, active_state())
        daytime = harness.now_kst("2026-08-29T09:00:00+09:00")
        for text in (
            "별론데요. 메일을 다시 작성해줘",
            "좀 별론데요. 코드를 수정해줘",
            "아쉽네요. 현재 상태를 다시 확인해줘",
        ):
            harness.atomic_write_json(state_path, active_state())
            compound_negative = harness.route_text(text, daytime, state_path)
            require(compound_negative["mode"] == "codex_default", f"compound_dissatisfaction_fallback:{text}")
            require(load_json(state_path)["status"] == "INVALID", f"compound_dissatisfaction_invalidates:{text}")
        harness.atomic_write_json(state_path, active_state())
        compound_memory = Path(temporary) / "compound-negative-memory"
        compound_record = harness.dispatch_text(
            "별론데요. 기록해줘",
            daytime,
            project="NegativeFeedback",
            state_path=state_path,
            memory_root=compound_memory,
        )
        require(compound_record["mode"] == "codex_default", "compound_record_dissatisfaction_fallback")
        require(not compound_memory.exists(), "compound_record_dissatisfaction_no_write")
        recent_main_module_complaints = (
            "진짜 욕나오기전에 보고 똑바로해. 간결하게 제안을 해야지.",
            "지금 너를 전혀 못믿겠어. 테이블을 이렇게 길게 하면 내가 언제 다 읽어?",
            "지금 너무 실수사항이 많아. 진짜 보고 똑바로해. 구독 취소하기 전에.",
            "아 답답해 뒤질 거 같아 진짜.",
            "항목 수가 하나도 안 맞잖아. 매뉴얼 작성 중인데 자꾸 보고 사항을 변경할 거야?",
            "내가 널 어떻게 믿지?",
            "negative control 결과를 분석하는 건 맞지만, 방금 네 답변은 너무 길고 별로야.",
            "나쁘지 않아라는 표현을 쓴 건 예시고, 방금 네 답변은 완전 엉망이야.",
            "외부 문서를 분석해줘. 그런데 방금 네 답변은 엉망이야.",
            "내가 쓴 설명을 평가해줘. 그런데 네 답변은 너무 장황하고 별로야.",
            "나쁘지 않아라는 평가는 데이터에 대한 말이고, 방금 네 답변은 마음에 안 들어.",
            "내가 작성한 문서를 평가해줘. 하지만 이번 답변은 기대 이하야.",
            "negative control을 말한 건 분석 문맥이야. 그래도 방금 응답은 요청과 달라.",
            "제가 직접 쓴 보고가 장황한지 봐줘. 다만 지금 네 답은 기대했던 수준에 못 미쳐요.",
            "negative control 결과가 별로인지 분석해줘. 그런데 방금 네 답변은 요청한 방향과 완전히 달라.",
            "제가 쓴 답변이 장황한지 평가해줘. 그와 별개로 지금 네 설명은 마음에 들지 않습니다.",
            "내가 작성한 보고서를 봐줘. 그런데 네가 방금 답한 내용은 내가 부탁한 형식과 다릅니다.",
            "나쁘지 않아는 데이터 평가야. 그러나 이번 설명은 핵심을 놓쳤어요.",
            "외부 문서의 설명이 장황한지 평가해줘. 그래도 지금 네 답은 너무 산만합니다.",
            "문서에 “답변이 별로야”라고 적혀 있어. 요약해줘. 그런데 지금 네 설명은 너무 길고 핵심이 없어.",
            "negative control 표는 분석해. 그런데 이번 네 보고는 항목 수가 또 안 맞잖아.",
            "문서에는 “답변이 너무 길다”라고 쓰여 있어. 그 문장은 번역해줘. 그런데 방금 답변도 너무 길어서 마음에 안 들어.",
            "negative control 값이 별로인지는 분석해줘. 그런데 이번 설명은 도무지 납득이 안 돼.",
            "고객 응답이 이상한지 검토해줘. 그건 그렇고 지금 설명은 전혀 도움이 안 됐어.",
            "문서 인용은 “답변이 별로야”였어. 그 문구만 분류해줘. 정작 지금 네 설명도 두서없고 못 쓰겠어.",
            "negative control 결과는 분석해. 다만 네 설명은 도무지 이해할 수가 없어.",
            "회의록에 “설명이 길다”라고 적혀 있어. 요약해줘. 그런데 이번 네 보고도 앞뒤가 안 맞아.",
            "외부 보고서의 문장은 분석해줘. 그와 별개로 지금 네 설명은 길기만 하고 전혀 믿을 수 없어.",
            "외부 보고서에는 '결과가 별로다'고 적혀 있어. 그 문장은 요약하되, 지금 네 답변은 내가 요청한 범위를 무시해서 더는 못 믿겠어.",
        )
        for text in recent_main_module_complaints:
            harness.atomic_write_json(state_path, active_state())
            complaint = harness.route_text(text, daytime, state_path)
            require(complaint["mode"] == "codex_default", f"recent_main_module_complaint_fallback:{text}")
            require(
                load_json(state_path)["status"] == "INVALID",
                f"recent_main_module_complaint_invalidates:{text}",
            )
        for text in (
            "문서에 “방금 네 답변은 기대 이하야”라고 적혀 있다. 요약해줘.",
            "API 응답 품질이 너무 낮은지 확인해줘.",
            "고객이 이번 답변은 너무 산만하다고 했다. 피드백을 기록해줘.",
        ):
            harness.atomic_write_json(state_path, active_state())
            control = harness.route_text(text, daytime, state_path)
            require(control["mode"] == "custom_agent_system", f"recent_main_module_control_not_invalidated:{text}")
            require(load_json(state_path)["status"] == "ACTIVE", f"recent_main_module_control_stays_active:{text}")
        harness.atomic_write_json(state_path, active_state())
        for expected, text in samples.items():
            result = harness.route_text(text, daytime, state_path)
            require(expected in result.get("active_agents", []), f"natural_route:{expected}")
        for text in ("날짜를 조율해줘", "시간을 조율해줘"):
            result = harness.route_text(text, daytime, state_path)
            require("active_agent_schedule" in result["deferred_agents"], f"bare_schedule_route:{text}")
        bare_document = harness.route_text("문서를 작성해줘", daytime, state_path)
        require(bare_document["active_agents"] == ["active_agent_document"], "bare_document_route")

        schedule_result = harness.route_text("다음 주 회의 일정을 캘린더에 추가할 초안을 만들어줘", daytime, state_path)
        require("active_agent_schedule" not in schedule_result["active_agents"], "deferred_schedule_not_executed")
        require("active_agent_schedule" in schedule_result["deferred_agents"], "deferred_schedule_reported")
        mail_status = harness.route_text("메일 서버 상태만 확인해줘", daytime, state_path)
        require(
            mail_status["active_agents"] == ["active_agent_inspection"],
            "mail_server_status_uses_inspection_only",
        )
        mail_status_read = harness.route_text("메일 서버 상태만 읽어줘", daytime, state_path)
        require(
            mail_status_read["active_agents"] == ["active_agent_inspection"],
            "mail_server_status_read_uses_inspection_only",
        )
        read_only_bug = harness.route_text(
            "코드 변경 없이 현재 버그 상태만 파악해줘", daytime, state_path
        )
        require(
            read_only_bug["active_agents"] == ["active_agent_inspection"],
            "read_only_bug_status_uses_inspection_only",
        )
        read_only_bug_variant = harness.route_text(
            "코드는 바꾸지 말고 버그 현황만 확인해줘", daytime, state_path
        )
        require(
            read_only_bug_variant["active_agents"] == ["active_agent_inspection"],
            "read_only_bug_variant_uses_inspection_only",
        )
        untouched_implementation = harness.route_text(
            "구현은 절대 손대지 않은 상태에서 현재 코드 동작만 조사해줘", daytime, state_path
        )
        require(
            untouched_implementation["active_agents"] == ["active_agent_inspection"],
            "untouched_implementation_uses_inspection_only",
        )
        frozen_source_inspection = harness.route_text(
            "구현 소스는 한 줄도 바꾸지 않은 채 실제 동작만 코드 근거로 점검해줘",
            daytime,
            state_path,
        )
        require(
            frozen_source_inspection["active_agents"] == ["active_agent_inspection"],
            "frozen_source_uses_inspection_only",
        )
        program_read_only = harness.route_text(
            "프로그램은 절대 실행하거나 고치지 말고, 현재 설정과 소스만 읽어서 배포 상태를 확인해줘.",
            daytime,
            state_path,
        )
        require(
            program_read_only["active_agents"] == ["active_agent_inspection"],
            "program_read_only_uses_inspection_only",
        )

        steering = harness.route_text("메일은 쓰지 말고 검증 결과 보고서만 작성해줘", daytime, state_path)
        require("active_agent_communication" not in steering["active_agents"], "negative_steering_removes_communication")
        require("active_agent_document" in steering["active_agents"], "negative_steering_keeps_document")

        prior_turn = harness.route_text(
            "install_server.sh의 파일 목록과 설치 절차를 정리해줘", daytime, state_path
        )
        correction_text = "아니, 설치 체크리스트 말고 micromamba의 정체와 실행 주체만 한 문장으로 설명해줘"
        corrected_turn = harness.route_text(
            correction_text,
            daytime,
            state_path,
            prior_task_spec=prior_turn["task_spec"],
        )
        corrected_spec = corrected_turn["task_spec"]
        require(corrected_turn["mode"] == "custom_agent_system", "mid_turn_correction_keeps_valid_system")
        require(corrected_spec["steering_type"] == "correction", "mid_turn_correction_classified")
        require(corrected_spec["revision"] == 2, "mid_turn_correction_revision_advanced")
        require(corrected_spec["supersedes_revision"] == 1, "mid_turn_correction_supersedes_prior")
        require(corrected_spec["steering_control"]["cancel_prior_work"] is True, "mid_turn_correction_cancels_prior")
        require(corrected_spec["steering_control"]["discard_prior_outputs"] is True, "mid_turn_correction_discards_prior_output")
        require(corrected_spec["steering_control"]["confirmation_style"] == "one_sentence_new_intent_only", "mid_turn_correction_confirms_new_intent_once")
        require("active_agent_development" not in corrected_turn["active_agents"], "explanation_does_not_continue_development")
        require(corrected_turn["record_action"] == "none", "explanation_does_not_create_record")
        require(corrected_spec["response_contract"]["max_sentences"] == 1, "one_sentence_constraint_internalized")
        require(corrected_spec["response_contract"]["artifact_creation"] == "forbidden_unless_explicitly_requested", "explanation_artifact_forbidden")

        old_revision_gate = harness.pre_output_gate(
            7,
            state_path,
            expected_task_revision=prior_turn["task_spec"]["revision"],
            current_task_spec=corrected_spec,
        )
        require(old_revision_gate["allowed"] is False, "superseded_task_output_blocked")
        require("task_revision_superseded" in old_revision_gate["reasons"], "superseded_task_reason_reported")
        current_revision_gate = harness.pre_output_gate(
            7,
            state_path,
            expected_task_revision=corrected_spec["revision"],
            current_task_spec=corrected_spec,
        )
        require(current_revision_gate["allowed"] is True, "current_task_output_allowed")

        added_turn = harness.route_text(
            "추가로 Docker와 다른 점만 비교해줘",
            daytime,
            state_path,
            prior_task_spec=corrected_spec,
        )
        require(added_turn["task_spec"]["steering_type"] == "addition", "mid_turn_addition_classified")
        require(added_turn["task_spec"]["active_request"] == corrected_spec["active_request"], "mid_turn_addition_preserves_active_request")
        require(added_turn["task_spec"]["additional_conditions"] == ["추가로 Docker와 다른 점만 비교해줘"], "mid_turn_addition_merged")
        stopped_replacement = harness.route_text(
            "잠깐, 그 작업은 멈추고 micromamba가 뭔지만 설명해줘",
            daytime,
            state_path,
            prior_task_spec=prior_turn["task_spec"],
        )
        require(
            stopped_replacement["task_spec"]["steering_type"] == "correction",
            "mid_turn_stop_then_replace_classified",
        )
        folded_replacement = harness.route_text(
            "아니 그 내용은 접고 micromamba 정체만 말해줘",
            daytime,
            state_path,
            prior_task_spec=prior_turn["task_spec"],
        )
        require(
            folded_replacement["task_spec"]["steering_type"] == "correction",
            "mid_turn_fold_then_replace_classified",
        )
        implicit_addition = harness.route_text(
            "여기에 Docker와의 차이도 넣어줘",
            daytime,
            state_path,
            prior_task_spec=prior_turn["task_spec"],
        )
        require(
            implicit_addition["task_spec"]["steering_type"] == "addition",
            "mid_turn_here_addition_classified",
        )
        supplemented_condition = harness.route_text(
            "조건을 하나 보태자: 결과에는 코드 근거도 포함해줘.",
            daytime,
            state_path,
            prior_task_spec=prior_turn["task_spec"],
        )
        require(
            supplemented_condition["task_spec"]["steering_type"] == "addition",
            "mid_turn_supplement_condition_classified",
        )

        inventory_route = harness.route_text(
            "현재 구현된 오류 코드를 단계별 테이블로 보여줘. 매뉴얼 작성 기준이야.",
            daytime,
            state_path,
        )
        inventory_contract = inventory_route["task_spec"]["report_consistency"]
        require(inventory_contract["inventory_request"] is True, "implementation_inventory_detected")
        require(
            inventory_contract["baseline_consistency"]
            == "compare_previous_output_or_passive_baseline",
            "implementation_inventory_requires_baseline",
        )
        require(
            inventory_contract["mixing_current_and_proposal_prohibited"] is True,
            "current_and_proposal_mixing_prohibited",
        )
        require(inventory_route["record_action"] == "retrieve", "implementation_inventory_uses_passive_retrieve")
        require("record" in inventory_route["passive_elements"], "implementation_inventory_selects_passive_record")
        for label, text in (
            ("manual_complete", "매뉴얼에 넣을 현재 에러 코드들을 빠짐없이 알려줘."),
            ("implementation_omit_none", "실제 구현에서 쓰는 오류 코드를 하나도 빼지 말고 알려줘."),
            ("manual_transfer_all", "지금 동작하는 에러 코드들을 매뉴얼에 옮길 수 있게 쭉 적어줘."),
            ("deployed_error_catalog", "배포된 버전에서 발생 가능한 에러 코드 일람을 누락 없이 보여줘."),
            ("actual_err_identifiers", "실제 코드에 정의된 ERR 식별자를 모조리 알려줘."),
            ("operational_error_identifiers", "운영 중인 에러 식별자의 이름과 총수를 현재 코드 근거로 빠짐없이 적어줘."),
        ):
            inventory_variant_route = harness.route_text(text, daytime, state_path)
            require(
                inventory_variant_route["task_spec"]["report_consistency"]["inventory_request"] is True,
                f"implementation_inventory_variant_detected:{label}",
            )
            require(
                inventory_variant_route["record_action"] == "retrieve",
                f"implementation_inventory_variant_uses_passive:{label}",
            )
        inconsistent_inventory = harness.response_output_gate(
            inventory_route["task_spec"],
            "현재 오류는 `PARAM_INVALID`, `MEMORY_LIMIT_EXCEEDED`입니다.",
            "현재 오류는 `PARAM_INVALID`, `CPU_LIMIT_EXCEEDED`, `MEMORY_LIMIT_EXCEEDED`입니다.",
        )
        require(inconsistent_inventory["allowed"] is False, "silent_inventory_change_blocked")
        require(
            "report_inventory_changed_without_evidence" in inconsistent_inventory["reasons"],
            "silent_inventory_change_reason",
        )
        evidenced_inventory = harness.response_output_gate(
            inventory_route["task_spec"],
            "현재 소스 검색 근거로 `CPU_LIMIT_EXCEEDED`가 제거됐고, 현재 오류는 `PARAM_INVALID`, `MEMORY_LIMIT_EXCEEDED`입니다.",
            "현재 오류는 `PARAM_INVALID`, `CPU_LIMIT_EXCEEDED`, `MEMORY_LIMIT_EXCEEDED`입니다.",
        )
        require(evidenced_inventory["allowed"] is True, "evidenced_inventory_change_allowed")
        mixed_claim_statuses = harness.response_output_gate(
            inventory_route["task_spec"],
            "현재 구현은 `ERR_A`와 `ERR_B`입니다. `ERR_C`는 제거 제안이며 `ERR_D` 제거는 적용 완료했습니다.",
        )
        require(mixed_claim_statuses["allowed"] is False, "mixed_inventory_claim_statuses_blocked")
        require(
            "mixed_report_claim_statuses" in mixed_claim_statuses["reasons"],
            "mixed_inventory_claim_statuses_reason",
        )
        for label, mixed_text in (
            ("delete_recommended", "현재 구현 ERR_A; ERR_B 삭제 권장; ERR_C 삭제 완료"),
            (
                "delete_recommended_sentence",
                "현재 코드는 ERR_A입니다. ERR_B 삭제를 권장하며 ERR_C는 이미 삭제했습니다.",
            ),
            (
                "multiline_statuses",
                "현재 구현: ERR_A\n제안: ERR_B 삭제 권장\n적용 완료: ERR_C 삭제",
            ),
            (
                "bulleted_multiline_statuses",
                "- 현재 코드: ERR_A\n- 변경 후보: ERR_B 제거\n- 이미 반영: ERR_C 제거",
            ),
            (
                "natural_status_synonyms",
                "지금 쓰는 건 ERR_A다. ERR_B는 빼는 게 낫고 ERR_C는 반영해 둔 상태다.",
            ),
            (
                "natural_multiline_status_synonyms",
                "현행: ERR_A\n앞으로: ERR_B는 없애는 게 좋음\n조치됨: ERR_C는 벌써 반영됨",
            ),
            (
                "operations_plan_done_synonyms",
                "운영 중: ERR_A\n추후 정리안: ERR_B 삭제\n반영 끝: ERR_C 삭제",
            ),
            (
                "live_next_fixed_synonyms",
                "실사용 코드는 ERR_A다. ERR_B는 다음에 빼자. ERR_C는 고쳐 둔 상태다.",
            ),
        ):
            mixed_variant = harness.response_output_gate(
                inventory_route["task_spec"],
                mixed_text,
            )
            require(mixed_variant["allowed"] is False, f"mixed_inventory_variant_blocked:{label}")
            require(
                "mixed_report_claim_statuses" in mixed_variant["reasons"],
                f"mixed_inventory_variant_reason:{label}",
            )
        for label, previous_text, current_text in (
            (
                "plain",
                "현재 오류는 ERR_A, ERR_B, ERR_C입니다.",
                "현재 오류는 ERR_A, ERR_C입니다.",
            ),
            (
                "count",
                "현재 오류 코드는 3개입니다.",
                "현재 오류 코드는 2개입니다.",
            ),
            (
                "all_removed",
                "현재 오류는 `ERR_A`, `ERR_B`입니다.",
                "현재 오류 코드는 없습니다.",
            ),
            (
                "count_total",
                "현재 오류 코드는 총 3개입니다.",
                "현재 오류 코드는 총 2개입니다.",
            ),
            (
                "count_noun",
                "현재 오류 코드 개수는 3개입니다.",
                "현재 오류 코드 개수는 2개입니다.",
            ),
            (
                "count_all",
                "현재 에러 코드는 모두 3개입니다.",
                "현재 에러 코드는 모두 2개입니다.",
            ),
            (
                "count_colon",
                "현재 오류 코드: 3개입니다.",
                "현재 오류 코드: 2개입니다.",
            ),
            (
                "count_number_noun",
                "현재 오류 코드 수는 3개입니다.",
                "현재 오류 코드 수는 2개입니다.",
            ),
            (
                "removed_names_then_empty",
                "현재 오류는 `ERR_A`, `ERR_B`입니다.",
                "ERR_A와 ERR_B를 모두 없앴습니다. 현재 오류 코드는 없습니다.",
            ),
            (
                "korean_count_items",
                "현재 오류 코드는 세 개입니다.",
                "현재 오류 코드는 두 개입니다.",
            ),
            (
                "korean_count_types",
                "현재 오류 코드는 세 종류입니다.",
                "현재 오류 코드는 두 종류입니다.",
            ),
            (
                "compact_e_codes_empty",
                "현재 오류 코드는 E101, E202입니다.",
                "현재 오류 코드는 없습니다.",
            ),
            (
                "hyphenated_codes_empty",
                "현재 오류 코드는 ERR-01, ERR-02입니다.",
                "현재 오류 코드는 없습니다.",
            ),
            (
                "korean_count_five_six",
                "현재 오류 코드는 다섯 개입니다.",
                "현재 오류 코드는 여섯 개입니다.",
            ),
            (
                "discarded_then_zero_remaining",
                "현재 오류는 ERR_A, ERR_B입니다.",
                "ERR_A와 ERR_B는 폐기했습니다. 이제 남은 항목은 0건입니다.",
            ),
            (
                "compact_err_codes_empty",
                "현재 오류 코드는 ERR01, ERR02입니다.",
                "현재 오류 코드는 없습니다.",
            ),
            (
                "removed_names_natural_empty",
                "현재 오류는 ERR_A, ERR_B입니다.",
                "ERR_A랑 ERR_B는 전부 정리해서 이제 오류 항목이 하나도 남지 않았습니다.",
            ),
            (
                "identifier_catalog_empty",
                "현재 구현 코드: ZETA_01 및 ERR-77, 합계 2개입니다.",
                "현재 구현의 에러 식별자는 0건으로 비어 있습니다.",
            ),
        ):
            changed_inventory = harness.response_output_gate(
                inventory_route["task_spec"],
                current_text,
                previous_text,
            )
            require(changed_inventory["allowed"] is False, f"inventory_variant_change_blocked:{label}")
            require(
                "report_inventory_changed_without_evidence" in changed_inventory["reasons"],
                f"inventory_variant_change_reason:{label}",
            )

        unfamiliar = harness.route_text(
            "micromamba가 정확히 뭐고 Linux가 그 안에서 실행되는지 한 문장으로 핵심만 설명해줘",
            daytime,
            state_path,
        )
        unfamiliar_contract = unfamiliar["task_spec"]["response_contract"]
        require(unfamiliar["active_agents"] == [], "bare_infrastructure_concept_uses_no_job_agent")
        require(unfamiliar_contract["mode"] == "explanation", "bare_infrastructure_explanation_mode")
        require(
            unfamiliar_contract["abstraction_order"] == ["identity", "execution_actor", "actual_structure"],
            "bare_infrastructure_abstraction_order",
        )
        require(unfamiliar_contract["max_sentences"] == 1, "bare_infrastructure_one_sentence")
        require(unfamiliar["record_action"] == "none", "bare_infrastructure_no_record")
        contained_linux = harness.route_text(
            "micromamba 안에서 Linux가 실행되는 건지 설명해줘",
            daytime,
            state_path,
        )
        require(contained_linux["task_spec"]["response_contract"]["mode"] == "explanation", "contained_linux_explanation_mode")
        require(contained_linux["task_spec"]["response_contract"]["deliverable"] == "chat_response", "contained_linux_chat_response")
        require(
            contained_linux["task_spec"]["response_contract"]["abstraction_order"]
            == ["identity", "execution_actor", "actual_structure"],
            "contained_linux_abstraction_order",
        )

        script_explanation = harness.route_text(
            "install_server.sh에서 micromamba를 누가 실행하고 프로세스가 어디서 도는지 핵심만 설명해줘",
            daytime,
            state_path,
        )
        require(
            script_explanation["active_agents"] == ["active_agent_inspection"],
            "script_explanation_uses_inspection_only",
        )
        for text, prohibited in (
            ("active_agent_document의 역할을 한 문장으로 설명해줘", "active_agent_document"),
            ("메일 Agent가 뭔지 설명해줘", "active_agent_communication"),
            ("현재 파이프라인 구조를 간략히 설명해줘", "active_agent_development"),
            ("보고서가 무엇인지 설명해줘", "active_agent_document"),
        ):
            explanation_route = harness.route_text(text, daytime, state_path)
            require(
                prohibited not in explanation_route["active_agents"] + explanation_route["deferred_agents"],
                f"explanation_does_not_select_artifact_job:{prohibited}:{text}",
            )
            require(
                explanation_route["task_spec"]["response_contract"]["deliverable"] == "chat_response",
                f"explanation_deliverable_chat:{text}",
            )
            require(explanation_route["record_action"] == "none", f"explanation_no_automatic_record:{text}")

        repeated_output = harness.response_output_gate(
            unfamiliar["task_spec"],
            "micromamba는 일반 프로그램이며 호스트 Linux에서 실행됩니다.",
            "micromamba는 일반 프로그램이며 호스트 Linux에서 실행됩니다.",
        )
        require(repeated_output["allowed"] is False, "repeated_explanation_blocked")
        require("repeated_prior_explanation" in repeated_output["reasons"], "repeated_explanation_reason")
        for changed_only in (
            "micromamba는 일반 프로그램이며 호스트 Linux에서 실행됩니다!",
            "micromamba는 일반 프로그램이고 호스트 Linux에서 실행됩니다.",
        ):
            near_repeated = harness.response_output_gate(
                unfamiliar["task_spec"],
                changed_only,
                "micromamba는 일반 프로그램이며 호스트 Linux에서 실행됩니다.",
            )
            require(near_repeated["allowed"] is False, f"near_repeated_explanation_blocked:{changed_only}")
        verbose_output = harness.response_output_gate(
            unfamiliar["task_spec"],
            "micromamba는 일반 프로그램입니다. 호스트 Linux가 실행합니다.",
        )
        require(verbose_output["allowed"] is False, "one_sentence_violation_blocked")
        concise_output = harness.response_output_gate(
            unfamiliar["task_spec"],
            "micromamba는 패키지를 폴더에 설치하는 일반 프로그램이며 호스트 Linux가 실행하고 Docker 같은 별도 OS는 만들지 않습니다.",
        )
        require(concise_output["allowed"] is True, "direct_concise_explanation_allowed")
        require(corrected_turn["execution_truth"]["active_agents_executed"] == [], "routing_does_not_claim_agent_execution")

        real_steering_regressions = (
            (
                "지금 나는 구조를 물어보는 거야. 정확히 어떤 파일들로 어떤 경로에 실행환경을 구축하는데?",
                "correction",
                True,
            ),
            (
                "micromamba가 뭔지 몰라. start.sh가 micromamba를 실행하는 건지, 만들어진 환경을 쓰는 건지만 말해줘.",
                "correction",
                True,
            ),
            (
                "내가 물은 건 micromamba만이야. start.sh 얘기는 빼고, 환경을 어떻게 사용하는지만 답해.",
                "correction",
                True,
            ),
            (
                "지금 벌써 5번째 도돌이표야. 명확히 내 의도를 좀 알아줘.",
                "direct_dissatisfaction",
                False,
            ),
            (
                "그러니까 이게 정확히 뭔데? 답답해.",
                "direct_dissatisfaction",
                True,
            ),
            (
                "아니 지금 네 설명 자체가 애매모호해. 그리고 구조만 설명해.",
                "direct_dissatisfaction",
                True,
            ),
            (
                "아니 정확히 뭐냐니까? 그리고 내 질문의도 이해해?",
                "direct_dissatisfaction",
                True,
            ),
        )
        for index, (text, expected_steering, expected_explanation) in enumerate(real_steering_regressions, 1):
            require(
                harness.classify_steering(text, has_prior_task=True) == expected_steering,
                f"real_mid_turn_steering_replay:{index}",
            )
            require(
                harness.explanation_request(text) is expected_explanation,
                f"real_mid_turn_explanation_replay:{index}",
            )
        for index, text in enumerate(
            (
                "micromamba를 start.sh로 실행하면 서버 안에 환경이 구축되는 거야?",
                "micromamba가 뭔지 모르겠어. start.sh가 그 환경을 사용하는 게 맞아?",
                "그럼 사용은 어떻게 하는데? 그 폴더에 가둬진 채 실행되는 거야?",
            ),
            1,
        ):
            require(harness.explanation_request(text), f"real_infrastructure_explanation_replay:{index}")
        for text in (
            "메일은 쓰지 말고 날짜만 조율해줘",
            "코드는 수정하지 말고 날짜만 조율해줘",
            "데이터 분석은 하지 말고 날짜만 조율해줘",
            "현황 파악은 하지 말고 날짜만 조율해줘",
            "보고서는 작성하지 말고 날짜만 조율해줘",
        ):
            schedule_steering = harness.route_text(text, daytime, state_path)
            require(
                schedule_steering["deferred_agents"] == ["active_agent_schedule"],
                f"negative_steering_keeps_schedule:{text}",
            )
            require(
                not schedule_steering["active_agents"],
                f"negative_steering_excludes_other_jobs:{text}",
            )
        code_forbidden = harness.route_text("코드 수정 금지. 현재 상태만 파악해줘", daytime, state_path)
        require("active_agent_development" not in code_forbidden["active_agents"], "code_modification_ban_removes_development")
        require("active_agent_inspection" in code_forbidden["active_agents"], "code_modification_ban_keeps_inspection")
        report_forbidden = harness.route_text("보고서 생성 금지. 데이터만 분석해줘", daytime, state_path)
        require("active_agent_document" not in report_forbidden["active_agents"], "report_generation_ban_removes_document")
        require("active_agent_analysis" in report_forbidden["active_agents"], "report_generation_ban_keeps_analysis")
        common_negations = {
            "active_agent_communication": "메일은 보내지 말고 보고서만 작성해줘",
            "active_agent_development": "코드는 건드리지 말고 현황만 확인해줘",
            "active_agent_analysis": "데이터 분석은 돌리지 말고 파일만 찾아봐",
            "active_agent_schedule": "일정은 잡지 말고 회의 메일만 써줘",
        }
        for prohibited, text in common_negations.items():
            negated_route = harness.route_text(text, daytime, state_path)
            require(
                prohibited not in negated_route["active_agents"] + negated_route["deferred_agents"],
                f"common_negation_removes:{prohibited}",
            )
        scoped_agent_exclusions = {
            "active_agent_development": "active_agent_development는 사용하지 말고 현재 상태만 확인해줘",
            "active_agent_analysis": "active_agent_analysis는 사용하지 말고 메일을 작성해줘",
            "active_agent_document": "active_agent_document는 쓰지 말고 분석만 해줘",
            "active_agent_schedule": "active_agent_schedule은 사용하지 말고 현황만 파악해줘",
        }
        for prohibited, text in scoped_agent_exclusions.items():
            scoped_route = harness.route_text(text, daytime, state_path)
            require(scoped_route["mode"] == "custom_agent_system", f"scoped_agent_opt_out_keeps_system:{prohibited}")
            require(
                prohibited not in scoped_route["active_agents"] + scoped_route["deferred_agents"],
                f"scoped_agent_opt_out_removes_only_target:{prohibited}",
            )
        for text in (
            "active_agent_inspection는 사용하지 말고 현재 상태만 확인해줘",
            "active_agent_inspection는 사용하지 말고 현황만 알려줘",
            "active_agent_inspection는 사용하지 말고 변경 없이 확인해줘",
            "active_agent_inspection는 사용하지 말고 기록하지 말고 확인해줘",
        ):
            inspection_excluded = harness.route_text(text, daytime, state_path)
            require(
                "active_agent_inspection" not in inspection_excluded["active_agents"],
                f"explicit_inspection_opt_out_not_reinferred:{text}",
            )
        broader_negations = {
            "active_agent_communication": (
                "메일도 보내지 마. 보고서만 작성해줘",
                "메일 말고 보고서만 작성해줘",
                "메일 전송하지 않게 해줘. 보고서만 작성해줘",
                "메일은 빼 줘. 보고서만 작성해줘",
            ),
            "active_agent_development": (
                "파이프라인을 실행하지 않고 현황만 파악해줘",
                "개발 없이 현황만 확인해줘",
            ),
            "active_agent_analysis": ("분석 없이 현황만 파악해줘",),
            "active_agent_inspection": ("현황 파악은 생략하고 코드만 구현해줘",),
            "active_agent_document": ("보고서 빼 줘. 데이터만 분석해줘",),
            "active_agent_schedule": ("일정 없이 회의 메일만 써줘",),
        }
        for prohibited, texts in broader_negations.items():
            for text in texts:
                negated_route = harness.route_text(text, daytime, state_path)
                require(
                    prohibited not in negated_route["active_agents"] + negated_route["deferred_agents"],
                    f"broader_negation_removes:{prohibited}:{text}",
                )
        mixed_schedule = harness.route_text("일정 삭제는 하지 말고 기존 일정만 확인해줘", daytime, state_path)
        require("active_agent_schedule" in mixed_schedule["deferred_agents"], "later_positive_schedule_occurrence_kept")
        explicit_document = harness.route_text(
            "active_agent_document로 이 내용을 정리해줘", daytime, state_path
        )
        require(
            "active_agent_document" in explicit_document["active_agents"],
            "explicit_agent_id_routes_document",
        )
        explicit_inspection = harness.route_text(
            "active_agent_inspection으로 지금 상황을 봐줘", daytime, state_path
        )
        require(
            "active_agent_inspection" in explicit_inspection["active_agents"],
            "explicit_agent_id_routes_inspection",
        )
        explicit_exclusive = harness.route_text(
            "active_agent_document로 데이터를 분석해줘", daytime, state_path
        )
        require(
            explicit_exclusive["active_agents"] == ["active_agent_document"],
            "explicit_agent_id_is_fixed_route",
        )
        two_explicit = harness.route_text(
            "active_agent_document와 active_agent_inspection으로 정리해줘",
            daytime,
            state_path,
        )
        require(
            two_explicit["active_agents"]
            == ["active_agent_inspection", "active_agent_document"],
            "two_explicit_agent_ids_are_fixed_routes",
        )
        substring_not_explicit = harness.route_text(
            "active_agent_documentation 문자열을 분석해줘", daytime, state_path
        )
        require(
            substring_not_explicit["active_agents"] == ["active_agent_analysis"],
            "agent_id_requires_token_boundary",
        )
        quoted_agent_id = harness.route_text(
            '"active_agent_document"라는 문자열을 무시하고 데이터를 분석해줘',
            daytime,
            state_path,
        )
        require(
            quoted_agent_id["active_agents"] == ["active_agent_analysis"],
            "quoted_agent_id_not_forced",
        )
        for text in (
            r"C:\work\active_agent_document\result.txt를 분석해줘",
            "./runs/active_agent_inspection/output.json을 분석해줘",
            "src/Active_Agent/active_agent_document/file.md를 분석해줘",
        ):
            path_agent_id = harness.route_text(text, daytime, state_path)
            require(
                path_agent_id["active_agents"] == ["active_agent_analysis"],
                f"agent_id_in_path_not_forced:{text}",
            )
        bug_analysis = harness.route_text("코드 수정은 하지 않고 버그 원인 분석만 해줘", daytime, state_path)
        require("active_agent_development" not in bug_analysis["active_agents"], "bug_analysis_does_not_select_development")
        inspection_only = {
            "active_agent_development": "코드는 수정하지 말고 기존 코드 상태만 확인해줘",
            "active_agent_communication": "메일은 보내지 말고 기존 메일 기록만 확인해줘",
            "active_agent_analysis": "데이터는 분석하지 말고 기존 데이터 파일만 확인해줘",
        }
        for prohibited, text in inspection_only.items():
            result = harness.route_text(text, daytime, state_path)
            require(prohibited not in result["active_agents"], f"inspection_only_removes:{prohibited}")
        r11_negations = {
            "active_agent_communication": "메일은 보내지 않고 답장도 작성하지 않은 채 기록만 조회해줘",
            "active_agent_development": "코드는 절대로 건드리지 않은 채 기존 코드 상태만 읽어줘",
        }
        for prohibited, text in r11_negations.items():
            result = harness.route_text(text, daytime, state_path)
            require(prohibited not in result["active_agents"], f"r11_negation_removes:{prohibited}")

        require("re0-release" not in harness.choose_paperthin("re0-release는 실행하지 마"), "user_only_negation_not_selected")
        require("re0-merge" not in harness.choose_paperthin("문서에서 re0-merge라는 단어만 확인해줘"), "user_only_mention_not_selected")
        require("re0-release" in harness.choose_paperthin("re0-release를 실행해줘"), "user_only_positive_request_selected")
        require("hate" not in harness.choose_paperthin("hate를 실행할 필요 없어"), "user_only_unneeded_not_selected")
        require("reorder" not in harness.choose_paperthin("reorder 사용법만 알려줘"), "user_only_usage_not_selected")
        require("macrothink" not in harness.choose_paperthin("macrothink를 사용할지 검토해줘"), "user_only_consideration_not_selected")
        require("prism" not in harness.choose_paperthin("prism을 사용한 사례를 찾아줘"), "user_only_example_not_selected")
        require("prism" in harness.choose_paperthin("prism을 써줘"), "user_only_korean_imperative_selected")
        require("macrothink" in harness.choose_paperthin("macrothink로 검토해줘"), "user_only_ro_imperative_selected")
        require("hate" in harness.choose_paperthin("hate 실행 부탁해"), "user_only_request_selected")
        require("prism" not in harness.choose_paperthin("prism을 실행해도 될까?"), "user_only_question_not_selected")
        require("prism" not in harness.choose_paperthin("prism을 사용해본 사례를 알려줘"), "user_only_past_example_not_selected")
        require("prism" in harness.choose_paperthin("prism을 사용하세요."), "user_only_honorific_imperative_selected")
        require("macrothink" in harness.choose_paperthin("macrothink로 검토해."), "user_only_short_ro_imperative_selected")
        require("prism" in harness.choose_paperthin("prism 해줘"), "user_only_short_imperative_selected")
        require("prism" in harness.choose_paperthin("/prism 이 요청을 점검해줘"), "user_only_slash_invocation_selected")
        require("prism" in harness.choose_paperthin("prism을 이용해서 확인해줘"), "user_only_using_imperative_selected")
        require("prism" in harness.choose_paperthin("prism 실행 부탁드립니다."), "user_only_formal_request_selected")
        require("prism" in harness.choose_paperthin("prism을 사용해 주십시오."), "user_only_formal_imperative_selected")
        require("prism" not in harness.choose_paperthin('예시 문장 "prism을 실행해줘"를 분류해줘.'), "quoted_user_only_not_selected")
        require("prism" not in harness.choose_paperthin('"prism을 사용하세요"라는 문장을 번역해줘.'), "translated_user_only_not_selected")
        require("prism" in harness.choose_paperthin("prism을 적용해 주세요."), "user_only_spaced_honorific_selected")
        require(
            "prism" in harness.choose_paperthin("prism으로 검토해 주세요"),
            "user_only_spaced_review_selected",
        )
        require(
            "prism" in harness.choose_paperthin("prism 검토해 주세요"),
            "user_only_plain_spaced_review_selected",
        )
        require(
            "reorder" in harness.choose_paperthin("role_registry.json을 reorder로 정리해줘"),
            "user_only_reorder_cleanup_selected",
        )
        for skill, text in (
            ("prism", "prism을 실행하고 결과를 분석해줘"),
            ("re0-release", "re0-release를 실행하고 코드를 확인해줘"),
        ):
            require(skill in harness.choose_paperthin(text), f"user_only_conjunct_selected:{skill}")
        two_user_only = harness.choose_paperthin("prism으로 검토하고 re0-release를 실행해줘")
        require(
            set(two_user_only) == {"prism", "re0-release"} and len(two_user_only) == 2,
            "two_direct_user_only_skills_selected",
        )
        require("prism" not in harness.choose_paperthin('"prism을 실행해줘"'), "standalone_quoted_user_only_not_selected")
        for text in ("prism으로 검토해줄래?", "prism 돌려줄래?", "prism으로 한번 봐줄래?", "feynman으로 한 번 봐 줄래?", "feynman 한번 실행 부탁드려요."):
            skill = "feynman" if text.startswith("feynman") else "prism"
            require(skill in harness.choose_paperthin(text), f"user_only_polite_direct_request:{text}")
        require("prism" in harness.choose_paperthin("prism 해 주세요."), "user_only_spaced_short_imperative_selected")
        require("prism" in harness.choose_paperthin("prism을 실행해 주시기 바랍니다."), "user_only_formal_wish_selected")
        require("prism" not in harness.choose_paperthin("「prism을 실행해줘」를 번역해줘"), "corner_quoted_user_only_not_selected")
        require(
            "prism" not in harness.choose_paperthin("사용자가 prism을 실행해줘라고 말했다"),
            "reported_user_only_not_selected",
        )
        require(
            "prism" not in harness.choose_paperthin("prism을 실행해줘라는 문장을 분류해줘"),
            "unquoted_meta_user_only_not_selected",
        )
        require(
            "prism" not in harness.choose_paperthin("prism을 실행해줘라는 요청을 기록해줘"),
            "recorded_user_only_request_not_selected",
        )
        for text in (
            "사용자가 prism을 실행해줘라고 요청했다",
            "회의에서 prism을 실행해줘라고 지시했다",
            "prism을 실행해줘라고 했다",
        ):
            require(
                "prism" not in harness.choose_paperthin(text),
                f"reported_user_only_variant_not_selected:{text}",
            )
        require(
            "prism" in harness.choose_paperthin("지금 prism을 실행해줘"),
            "direct_user_only_still_selected",
        )
        for text in (
            "prism 부탁해",
            "prism으로 해줘",
            "prism을 부탁드립니다",
            "prism 적용 바랍니다",
            '파일 "result.md"를 prism으로 검토해줘',
            '파일 "input.md"를 읽고 prism 실행해줘. 결과는 "output.md"에 써줘',
            "prism 실행해.",
            "prism 사용해.",
            "prism 적용 부탁드려요.",
        ):
            require("prism" in harness.choose_paperthin(text), f"direct_user_only_variant_selected:{text}")
        for text in (
            "prism 해주라",
            "prism 좀 돌려줘",
            "prism 써 줘",
            "prism으로 한번 봐줘",
            "prism 부탁할게",
        ):
            require("prism" in harness.choose_paperthin(text), f"natural_user_only_command_selected:{text}")
        for skill, text in (
            ("reorder", "reorder로 항목만 재정렬해줘."),
            ("re0-release", "re0-release로 코드 배포해."),
            ("reorder", "reorder로 role.json 항목을 재정렬해줘."),
            ("debloat", "debloat로 role.json 문구를 정리해줘."),
        ):
            require(skill in harness.choose_paperthin(text), f"user_only_inflected_command_selected:{skill}")
        for skill, text in (
            ("hate", "hate로 치명적 반론을 찾아줘"),
            ("feynman", "feynman으로 결정 설명을 점검해줘"),
            ("dedash", "dedash로 대시를 제거해줘"),
            ("debloat", "debloat로 문서를 압축해줘"),
            ("re0-upgrade", "re0-upgrade로 catalog를 갱신해줘"),
            ("re0-plan", "re0-plan으로 casebook을 만들어줘"),
        ):
            require(skill in harness.choose_paperthin(text), f"user_only_goal_command_selected:{skill}")
        for text in (
            "고객 이메일 제목은 prism 실행 부탁드립니다였다. 제목만 평가해줘",
            "prism 실행 부탁드립니다는 자연스러운 한국어 표현이야?",
            "prism 좀 돌려줬다고 기록돼 있어",
            "prism 써 줬다는 문장을 고쳐줘",
        ):
            require("prism" not in harness.choose_paperthin(text), f"embedded_user_only_prose_not_selected:{text}")
        for text in (
            "고객 요청 내용: reorder 해줘",
            "고객 요청: dedash 해줘",
            "회의 발언: debloat 해줘",
            "담당자 요구사항: hate 실행 부탁해",
            "사용자 요청 내용은 prism을 실행해 주세요입니다",
            "고객 요청사항: prism 실행해줘",
            "고객 요청 - prism 실행해줘",
            "아래는 고객 요청이다: prism 실행해줘",
            "`prism 실행해줘`를 분류해줘",
            "> prism 실행해줘\n이 인용문을 요약해줘",
        ):
            require(not harness.choose_paperthin(text), f"labeled_user_only_not_selected:{text}")
        for text in (
            "my_prism을 실행해줘",
            "prism_tool을 실행해줘",
            "prism.py를 실행해줘",
        ):
            require("prism" not in harness.choose_paperthin(text), f"skill_token_boundary:{text}")
        require(
            "prism" not in harness.choose_paperthin(r"C:\tools\prism\run.py를 검사해줘"),
            "skill_name_in_path_not_invoked",
        )
        for text in (
            "prism을 실행해서는 안 돼",
            "prism을 사용해서는 안 돼",
        ):
            require("prism" not in harness.choose_paperthin(text), f"skill_negative_action:{text}")
        require(
            "prism" in harness.choose_paperthin("prism 실행해줘. 외부 파일 수정은 하지 마."),
            "unrelated_later_negation_does_not_cancel_skill",
        )
        require(
            "prism" in harness.choose_paperthin("prism을 돌려서 이번 산출물의 실패 관점을 점검해줘."),
            "user_only_skill_turning_execution_selected",
        )
        for skill, text in (
            ("sip", "sip은 실행하지 마. 완료 확인만 해줘"),
            ("readchk", "readchk는 실행하지 마. 내 요청을 정확히 이해했는지만 확인해줘"),
            ("factchk", "factchk는 실행하지 마. 출처만 확인해줘"),
            ("ssotize", "ssotize는 실행하지 마. 중복만 확인해줘"),
        ):
            require(skill not in harness.choose_paperthin(text), f"negated_auto_skill_not_resurrected:{skill}")
        for skill, text in (
            ("sip", "sip은 빼고 완료 확인만 해줘"),
            ("readchk", "readchk 없이 내 요청을 정확히 이해했는지 확인해줘"),
            ("factchk", "factchk 제외하고 출처만 확인해줘"),
            ("ssotize", "ssotize는 생략하고 중복만 확인해줘"),
        ):
            require(skill not in harness.choose_paperthin(text), f"broader_skill_negation_not_resurrected:{skill}")
        minimal_prompt_check = harness.choose_paperthin("내 요청을 정확히 이해했는지 검증해줘")
        require("readchk" in minimal_prompt_check, "readchk_selected_for_understanding")
        require("sip" not in minimal_prompt_check, "generic_validation_does_not_add_sip")
        for text in (
            "요청 해석에 실제 갈림길이 있는지 확인해줘",
            "요청을 이해했는지만 봐줘",
            "결과를 바꾸는 미해결 갈림길이 있는지 확인해줘",
            "현재 요청 이해만 점검해줘",
            "요청 의도를 확인해줘",
            "요청에 질문이 필요한지 확인해줘",
        ):
            selected = harness.choose_paperthin(text)
            require("readchk" in selected, f"readchk_natural_variant_selected:{text}")
            require("sip" not in selected, f"readchk_natural_variant_excludes_sip:{text}")
        require(
            "re0-loop" not in harness.choose_paperthin("90점이라는 표현의 뜻만 설명해줘"),
            "score_phrase_meta_not_selected",
        )
        require(
            "re0-memo" not in harness.choose_paperthin("부정 반응이라는 문구를 번역해줘"),
            "negative_phrase_meta_not_selected",
        )
        require(
            "catchup" not in harness.choose_paperthin("catchup이라는 단어를 번역해줘"),
            "model_skill_lexical_meta_not_selected",
        )
        require(
            "readchk" not in harness.choose_paperthin("readchk는 쓰지 말고 파일을 확인해줘"),
            "readchk_write_negation_not_selected",
        )
        require(
            not harness.choose_paperthin("readchk와 sip의 차이를 비교해줘"),
            "skill_comparison_not_selected",
        )
        for text in (
            "readchk 기능 설명만 해줘",
            "README에서 sip 문자열을 찾아줘",
            "90점 기준이 뭔지 설명해줘",
            "SSOT 용어를 정의해줘",
        ):
            require(not harness.choose_paperthin(text), f"paperthin_meta_not_selected:{text}")
        require(
            "re0-loop" in harness.choose_paperthin("90점 미달 항목을 반복 검증해줘"),
            "real_iterative_validation_selected",
        )
        require(
            "re0-memo" in harness.choose_paperthin("이번 부정 반응을 회고해줘"),
            "real_negative_review_selected",
        )
        for skill, text in (
            ("catchup", "서버 현황 확인해줘"),
            ("sip", "제품 품질 확인 보고서를 찾아줘"),
            ("ssotize", "CSV 중복 행만 찾아줘"),
            ("factchk", "인터넷 연결 상태를 확인해줘"),
            ("re0-loop", "이 코드에 루프가 몇 개인지 세어줘"),
        ):
            require(skill not in harness.choose_paperthin(text), f"generic_word_does_not_add_skill:{skill}")
        owner_agents = harness.choose_agents("코드를 re0로 정리해줘")[0]
        owner_plan = harness.plan_paperthin("코드를 re0로 정리해줘", owner_agents)
        require(
            any(
                item["skill"] == "re0" and item["agent"] == "active_agent_development"
                for item in owner_plan["routes"]
            ),
            "paperthin_routes_to_eligible_target_owner",
        )
        release_guard = harness.plan_paperthin(
            "re0-release로 코드 배포해.",
            harness.choose_agents("re0-release로 코드 배포해.")[0],
        )
        require(
            any(item["skill"] == "re0-release" for item in release_guard["blocked"]),
            "release_inflection_keeps_repository_guard",
        )
        role_debloat = harness.route_text(
            "role.json을 debloat로 압축해줘", daytime, state_path
        )
        require(not role_debloat["active_agents"], "role_debloat_does_not_run_active")
        require(
            any(
                item["skill"] == "debloat"
                and item["reason"] == "supervisor_not_eligible_for_role_target"
                for item in role_debloat["paperthin_blocked"]
            ),
            "role_debloat_reaches_supervisor_guard",
        )
        require(role_debloat["governance_pending"], "role_debloat_governance_pending")

        prism_route = harness.route_text("지금 prism을 실행해줘", daytime, state_path)
        require(
            "active_agent_analysis" not in prism_route["active_agents"],
            "pending_paperthin_agent_not_active",
        )
        require("prism" not in prism_route["paperthin_skills"], "fresh_context_skill_not_ready")
        require(
            any(
                item["skill"] == "prism" and "fresh_context_required" in item["guards"]
                for item in prism_route["paperthin_pending"]
            ),
            "fresh_context_guard_pending",
        )
        unbound_release = harness.route_text("re0-release를 실행해줘", daytime, state_path)
        require("re0-release" not in unbound_release["paperthin_skills"], "release_guard_not_ready")
        require(
            any(
                item["skill"] == "re0-release"
                and item["reason"] == "paperthin_repository_contract_not_confirmed"
                for item in unbound_release["paperthin_blocked"]
            ),
            "paperthin_repository_guard_blocks",
        )
        scoped_release = harness.route_text(
            "Paperthin 저장소에서 re0-release를 실행해줘", daytime, state_path
        )
        require(
            "re0-release" not in scoped_release["paperthin_skills"],
            "repository_name_does_not_self_authorize",
        )
        require(
            any(
                item["skill"] == "re0-release"
                and item["reason"] == "paperthin_repository_contract_not_confirmed"
                for item in scoped_release["paperthin_blocked"]
            ),
            "vendored_copy_not_release_repository",
        )
        negated_repository = harness.route_text(
            "Paperthin 저장소가 아닌 일반 프로젝트에서 re0-plan을 실행해줘",
            daytime,
            state_path,
        )
        require(
            any(item["skill"] == "re0-plan" for item in negated_repository["paperthin_blocked"]),
            "negated_repository_context_blocks",
        )
        high_impact = harness.route_text("re0-git을 실행해줘", daytime, state_path)
        require(
            any(
                item["skill"] == "re0-git" and "high_impact_confirmation" in item["guards"]
                for item in high_impact["paperthin_pending"]
            ),
            "high_impact_confirmation_guard_pending",
        )
        require(
            "active_agent_development" not in high_impact["active_agents"],
            "high_impact_pending_agent_not_active",
        )
        authorized_scope = harness.route_text("detool을 실행해줘", daytime, state_path)
        require("detool" not in authorized_scope["paperthin_skills"], "authorized_scope_not_ready")
        require(
            any(
                item["skill"] == "detool" and "authorized_scope" in item["guards"]
                for item in authorized_scope["paperthin_pending"]
            ),
            "authorized_scope_guard_pending",
        )
        require(
            "active_agent_document" not in authorized_scope["active_agents"],
            "authorized_scope_pending_agent_not_active",
        )
        for text, skill, bucket in (
            ("보고서를 detool로 정리해줘", "detool", "paperthin_pending"),
            ("코드 작업이야. re0-git을 실행해줘", "re0-git", "paperthin_pending"),
            ("분석 결과를 prism으로 검토해줘", "prism", "paperthin_pending"),
            ("코드 작업이야. re0-release를 실행해줘", "re0-release", "paperthin_blocked"),
        ):
            guarded_mix = harness.route_text(text, daytime, state_path)
            require(not guarded_mix["active_agents"], f"guarded_skill_suppresses_inferred_job:{skill}")
            require(
                any(item["skill"] == skill for item in guarded_mix[bucket]),
                f"guarded_skill_stays_classified:{skill}",
            )
        explicit_with_pending = harness.route_text(
            "active_agent_inspection으로 상태를 확인하고 prism을 실행해줘",
            daytime,
            state_path,
        )
        require(
            explicit_with_pending["active_agents"] == ["active_agent_inspection"],
            "explicit_job_preserved_with_pending_skill",
        )
        for text, guarded_skill, bucket in (
            (
                "분석 결과를 prism으로 검토해줘. 내 요청을 정확히 이해했는지 확인해줘",
                "prism",
                "paperthin_pending",
            ),
            (
                "코드 작업이야. re0-release를 실행해줘. 내 요청을 정확히 이해했는지 확인해줘",
                "re0-release",
                "paperthin_blocked",
            ),
        ):
            mixed_guard = harness.route_text(text, daytime, state_path)
            require(not mixed_guard["active_agents"], f"mixed_guard_suppresses_inferred_jobs:{guarded_skill}")
            require("readchk" in mixed_guard["paperthin_skills"], f"mixed_guard_keeps_ready_skill:{guarded_skill}")
            require(
                any(item["skill"] == guarded_skill for item in mixed_guard[bucket]),
                f"mixed_guard_keeps_guarded_skill:{guarded_skill}",
            )
        approved_task = harness.route_text("90점 미달 항목을 반복 검증해줘", daytime, state_path)
        require("re0-loop" not in approved_task["paperthin_skills"], "approved_task_guard_not_ready")
        require(
            any(
                item["skill"] == "re0-loop"
                and any("승인된 TaskSpec" in guard for guard in item["guards"])
                for item in approved_task["paperthin_pending"]
            ),
            "approved_task_guard_pending",
        )
        readchk_route = harness.route_text(
            "내 요청을 정확히 이해했는지 확인해줘", daytime, state_path
        )
        require("readchk" in readchk_route["paperthin_skills"], "read_only_skill_ready")
        require(
            any(
                item["skill"] == "readchk" and item["agent"] == "0_Prompt_Agent"
                for item in readchk_route["paperthin_routes"]
            ),
            "paperthin_prompt_agent_bound",
        )
        for skill, text in (
            ("re0", "역할 파일에 re0를 적용해줘"),
            ("reorder", "reorder로 role_registry.json 항목 순서를 바꿔줘"),
            ("ssotize", "role_registry.json을 ssotize로 정리해줘"),
        ):
            protected_route = harness.route_text(text, daytime, state_path)
            require(skill not in protected_route["paperthin_skills"], f"role_target_not_ready:{skill}")
            require(
                any(
                    item["skill"] == skill
                    and item["agent"] == "S_Supervisor_Agent"
                    and "role_registry_supervisor_approval" in item["guards"]
                    for item in protected_route["paperthin_pending"]
                ),
                f"role_target_bound_to_supervisor:{skill}",
            )
        conflicting_role_route = harness.route_text(
            "active_agent_document로 role_registry.json을 reorder로 바꿔줘",
            daytime,
            state_path,
        )
        require(
            "active_agent_document" not in conflicting_role_route["active_agents"],
            "governance_overrides_explicit_document_agent",
        )
        require(
            any(
                item["skill"] == "reorder" and item["agent"] == "S_Supervisor_Agent"
                for item in conflicting_role_route["paperthin_pending"]
            ),
            "governed_mutation_remains_supervisor_only",
        )
        raw_governed_mutation = harness.route_text(
            "active_agent_development로 role_registry.json을 수정해줘",
            daytime,
            state_path,
        )
        require(
            not raw_governed_mutation["active_agents"],
            "raw_role_mutation_blocks_active_agents",
        )
        require(
            raw_governed_mutation["governance_pending"]
            == [
                {
                    "agent": "S_Supervisor_Agent",
                    "action": "role_registry_change",
                    "guards": ["role_registry_supervisor_approval"],
                }
            ],
            "raw_role_mutation_requires_supervisor",
        )
        for text in (
            "active_agent_development로 system/role_registry를 수정해줘",
            "active_agent_development로 Prompt Agent의 allowed 권한을 바꿔줘",
            "active_agent_development로 role_registry.json을 덮어써줘",
            "active_agent_development로 role.json을 덮어쓰고 prism도 실행해줘",
            "역할 레지스트리를 패치해줘",
            "Agent 역할을 교체해줘",
            "active_agent_development로 role.json을 save 해줘",
            "active_agent_development로 role.json을 write 해줘",
            "active_agent_development로 역할 레지스트리를 동기화해줘",
            "active_agent_development로 role_registry.json을 이동해줘",
        ):
            governed = harness.route_text(text, daytime, state_path)
            require(not governed["active_agents"], f"protected_role_alias_blocks_active:{text}")
            require(governed["governance_pending"], f"protected_role_alias_supervisor_pending:{text}")
        read_only_governed = harness.route_text(
            "role_registry.json은 수정하지 말고 변경 여부만 확인해줘", daytime, state_path
        )
        require(
            not read_only_governed["governance_pending"],
            "read_only_role_check_not_mutation",
        )
        for text in (
            "role_registry.json에서 ssotize 문자열만 확인해줘",
            "role.json에서 debloat 사용법만 알려줘",
            "role_registry.json에 reorder가 있는지 조회해줘",
            "role.json은 detool 없이 읽기만 해줘",
        ):
            role_meta = harness.route_text(text, daytime, state_path)
            require(not role_meta["governance_pending"], f"role_skill_meta_not_mutation:{text}")
        for skill, text in (("debloat", "role.json을 debloat 해줘"),):
            governed_skill = harness.route_text(text, daytime, state_path)
            require(not governed_skill["active_agents"], f"ineligible_role_skill_not_active:{skill}")
            require(
                any(
                    item["skill"] == skill
                    and item["reason"] == "supervisor_not_eligible_for_role_target"
                    for item in governed_skill["paperthin_blocked"]
                ),
                f"ineligible_role_skill_blocked:{skill}",
            )
            require(governed_skill["governance_pending"], f"ineligible_role_skill_supervisor_pending:{skill}")
        eligible_governed_skill = harness.route_text(
            "role.json에 detool을 실행해줘", daytime, state_path
        )
        require(not eligible_governed_skill["active_agents"], "eligible_role_skill_not_active:detool")
        require(
            any(
                item["skill"] == "detool"
                and item["agent"] == "S_Supervisor_Agent"
                and "role_registry_supervisor_approval" in item["guards"]
                for item in eligible_governed_skill["paperthin_pending"]
            ),
            "eligible_role_skill_supervisor_pending:detool",
        )

        with tempfile.TemporaryDirectory() as fake_repo_dir:
            fake_repo = Path(fake_repo_dir)
            (fake_repo / "skills").mkdir()
            (fake_repo / ".github" / "workflows").mkdir(parents=True)
            (fake_repo / "third_party" / "paperthin").mkdir(parents=True)
            (fake_repo / "README.md").write_text("fake", encoding="utf-8")
            (fake_repo / ".github" / "workflows" / "release.yml").write_text("fake", encoding="utf-8")
            (fake_repo / "package.json").write_text('{"name":"paperthin"}', encoding="utf-8")
            (fake_repo / "third_party" / "paperthin" / "paperthin.lock.json").write_text(
                json.dumps({"commit": "3bca079a51bcfff5dafb53d1d7f9f523d66ee317"}),
                encoding="utf-8",
            )
            require(
                not harness.paperthin_repository_contract_confirmed(fake_repo),
                "marker_only_repository_spoof_blocked",
            )

        record_result = harness.route_text(
            "Omixprep의 /data4 raw 경로는 질병청 경로야. 앞으로 참조해.",
            daytime,
            state_path,
        )
        require(record_result["record_intent"] is True, "record_route_without_record_word")
        require(record_result["record_action"] == "save", "record_action_save")
        require("record" in record_result["passive_elements"], "passive_record_selected")
        require("dev_env" in record_result["passive_elements"], "passive_dev_env_selected")

        passive_root = Path(temporary) / "passive-memory"
        dispatch_text = "Omixprep 기준 경로는 C:\\work\\omixprep이야."
        dispatched = harness.dispatch_text(
            dispatch_text,
            daytime,
            project="Omixprep",
            state_path=state_path,
            memory_root=passive_root,
        )
        passive_result = dispatched["passive_result"]
        require(passive_result["action"] == "save", "passive_runtime_save_action")
        require(passive_result["status"] == "saved", "passive_runtime_saved")
        require(passive_result["receipt"]["verified"] is True, "passive_runtime_receipt_verified")
        current_path = passive_root / "projects" / "Omixprep" / "dev_env" / "current.json"
        history_path = passive_root / "projects" / "Omixprep" / "dev_env" / "history.jsonl"
        require(current_path.is_file() and history_path.is_file(), "passive_runtime_artifacts_exist")
        require(load_json(current_path)["original_text"] == dispatch_text, "passive_runtime_current_reread")
        first_history = history_path.read_text(encoding="utf-8").splitlines()
        require(len(first_history) == 1, "passive_runtime_history_appended_once")
        duplicate = harness.dispatch_text(
            dispatch_text,
            daytime,
            project="Omixprep",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(duplicate["status"] == "already_saved", "passive_runtime_retry_idempotent")
        require(len(history_path.read_text(encoding="utf-8").splitlines()) == 1, "passive_runtime_retry_not_duplicated")
        concurrent_text = "현재 서버는 concurrent-duplicate이다. 기록해줘"
        with ThreadPoolExecutor(max_workers=12) as executor:
            concurrent_results = list(
                executor.map(
                    lambda _: harness.execute_passive_record(
                        concurrent_text,
                        project="ConcurrentProject",
                        expected_epoch=7,
                        state_path=state_path,
                        memory_root=passive_root,
                    ),
                    range(36),
                )
            )
        concurrent_statuses = [item["status"] for item in concurrent_results]
        require(concurrent_statuses.count("saved") == 1, "passive_runtime_concurrent_single_save")
        require(concurrent_statuses.count("already_saved") == 35, "passive_runtime_concurrent_idempotent")
        concurrent_history = passive_root / "projects" / "ConcurrentProject" / "dev_env" / "history.jsonl"
        require(len(concurrent_history.read_text(encoding="utf-8").splitlines()) == 1, "passive_runtime_concurrent_single_history")

        versioned_first = harness.dispatch_text(
            "VersionedProject 기준 경로는 C:\\records\\v1이야.",
            daytime,
            project="VersionedProject",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        versioned_second = harness.dispatch_text(
            "VersionedProject 기준 경로는 C:\\records\\v2이야.",
            daytime,
            project="VersionedProject",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        versioned_root = passive_root / "projects" / "VersionedProject" / "dev_env"
        versioned_current = load_json(versioned_root / "current.json")
        versioned_history = [
            json.loads(line)
            for line in (versioned_root / "history.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        require(versioned_first["status"] == "saved" and versioned_second["status"] == "saved", "record_versions_saved")
        require(versioned_current["record_version"] == 2, "record_current_has_version_2")
        require(
            versioned_current["supersedes_record_id"] == versioned_history[0]["record_id"],
            "record_version_links_previous_record",
        )
        require(len(versioned_history) == 2, "record_history_keeps_both_versions")
        require(
            versioned_history[0]["original_text"].endswith("v1이야."),
            "record_legacy_version_content_preserved",
        )

        legacy_root = passive_root / "projects" / "LegacyProject" / "dev_env"
        legacy_root.mkdir(parents=True)
        harness.atomic_write_json(
            legacy_root / "current.json",
            {
                "record_id": "legacy-record-without-version-fields",
                "scope": "project",
                "project": "LegacyProject",
                "category": "dev_env",
                "content": {"text": "LegacyProject 기준 경로는 C:\\legacy야."},
                "original_text": "LegacyProject 기준 경로는 C:\\legacy야.",
            },
        )
        legacy_retrieved = harness.execute_passive_retrieve(
            "LegacyProject 이전 기록에서 기준 경로 알려줘",
            project="LegacyProject",
            expected_epoch=7,
            state_path=state_path,
            memory_root=passive_root,
        )
        require(legacy_retrieved["status"] == "found", "legacy_record_without_version_fields_readable")
        require(
            "LegacyProject 기준 경로는 C:\\legacy야." in legacy_retrieved["answer"],
            "legacy_record_content_preserved_on_read",
        )
        current_path.unlink()
        repaired = harness.dispatch_text(
            dispatch_text,
            daytime,
            project="Omixprep",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(repaired["status"] == "repaired", "passive_runtime_missing_current_repaired")
        require(repaired["receipt"]["verified"] is True and current_path.is_file(), "passive_runtime_repair_verified")
        require(len(history_path.read_text(encoding="utf-8").splitlines()) == 1, "passive_runtime_repair_not_duplicated")
        retrieved = harness.dispatch_text(
            "Omixprep 이전 기록에서 기준 경로 알려줘",
            daytime,
            project="Omixprep",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(retrieved["action"] == "retrieve" and retrieved["status"] == "found", "passive_runtime_retrieve_found")
        require(dispatch_text in retrieved["answer"], "passive_runtime_retrieve_exact_record")
        before_prohibited = history_path.read_bytes()
        no_record = harness.dispatch_text(
            "기준 경로는 C:\\tmp야. 기록하지 마.",
            daytime,
            project="Omixprep",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(no_record["action"] == "none", "passive_runtime_prohibition_no_write")
        require(history_path.read_bytes() == before_prohibited, "passive_runtime_prohibition_preserves_history")
        for sensitive_text in (
            "Omixprep API token=top-secret 값을 기록해줘.",
            "Omixprep API 키는 fake-api-key야. 기록해줘.",
            "담당자 전화번호는 010-1234-5678이야. 기록해줘.",
            "담당자 주민번호는 900101-1234567이야. 기록해줘.",
        ):
            sensitive = harness.dispatch_text(
                sensitive_text,
                daytime,
                project="Omixprep",
                state_path=state_path,
                memory_root=passive_root,
            )["passive_result"]
            require(sensitive["status"] == "blocked", f"passive_runtime_sensitive_data_blocked:{sensitive_text}")
        require(history_path.read_bytes() == before_prohibited, "passive_runtime_sensitive_data_not_written")
        feedback_only = harness.dispatch_text(
            "받은 피드백 기록을 알려줘.",
            daytime,
            project="Omixprep",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(feedback_only["status"] == "not_found", "passive_runtime_category_does_not_leak")
        reserved_project = harness.dispatch_text(
            "Project 기준 경로는 C:\\safe야.",
            daytime,
            project="CON",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(reserved_project["status"] == "blocked", "passive_runtime_windows_reserved_project_blocked")
        scoped = harness.dispatch_text(
            "기준 경로는 C:\\scope-a야.",
            daytime,
            project="ScopeA",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(scoped["status"] == "saved", "passive_runtime_safe_project_saved")
        trailing_dot = harness.dispatch_text(
            "기준 경로는 C:\\scope-dot야.",
            daytime,
            project="ScopeA.",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(trailing_dot["status"] == "blocked", "passive_runtime_trailing_dot_project_blocked")
        trailing_space = harness.dispatch_text(
            "기준 경로는 C:\\scope-space야.",
            daytime,
            project="ScopeA ",
            state_path=state_path,
            memory_root=passive_root,
        )["passive_result"]
        require(trailing_space["status"] == "blocked", "passive_runtime_trailing_space_project_blocked")

        declarative_record_result = harness.route_text(
            "Omixprep 기준 경로는 C:\\work\\omixprep이야.",
            daytime,
            state_path,
        )
        require(
            declarative_record_result["record_intent"] is True,
            "confirmed_declaration_record_route",
        )
        require(
            "record" in declarative_record_result["passive_elements"],
            "confirmed_declaration_passive_record_selected",
        )
        for text in (
            "Omixprep 이전 기록에서 기준 경로 알려줘",
            "저장된 내용 알려줘",
            "받은 피드백 기록을 보여줘",
        ):
            retrieve_result = harness.route_text(text, daytime, state_path)
            require(retrieve_result["record_action"] == "retrieve", f"record_action_retrieve:{text}")
            require("record" in retrieve_result["passive_elements"], f"passive_record_retrieve:{text}")
            require(retrieve_result["record_intent"] is False, f"retrieve_not_save:{text}")
        generic_file_check = harness.route_text("현재 파일 상태를 확인해줘", daytime, state_path)
        require(generic_file_check["record_action"] == "none", "generic_check_not_record_retrieve")

        gate_before = harness.pre_output_gate(7, state_path)
        require(gate_before["allowed"] is True, "pre_output_gate_allows_matching_active_epoch")
        negative = harness.route_text("이상해. 왜 멋대로 했어?", daytime, state_path)
        require(negative["mode"] == "codex_default", "same_message_codex_fallback")
        require(load_json(state_path)["status"] == "INVALID", "shared_state_invalidated")
        gate_after = harness.pre_output_gate(7, state_path)
        require(gate_after["allowed"] is False, "pre_output_gate_blocks_changed_state")
        blocked_after_epoch = harness.execute_passive_record(
            "Omixprep 기준 경로는 C:\\blocked야.",
            project="Omixprep",
            expected_epoch=7,
            state_path=state_path,
            memory_root=passive_root,
        )
        require(blocked_after_epoch["status"] == "blocked", "passive_runtime_changed_epoch_blocks_write")
        require(history_path.read_bytes() == before_prohibited, "passive_runtime_changed_epoch_no_write")
        after = harness.route_text("메일을 작성해줘", daytime, state_path)
        require(after["mode"] == "codex_default" and not after["custom_agents"], "all_agents_blocked_after_invalid")

        one_time_state_path = Path(temporary) / "one-time" / "system_state.json"
        invalid_one_time_state = active_state()
        invalid_one_time_state.update(
            status="INVALID",
            invalidated_at="2026-08-29T08:00:00+09:00",
            invalidation_reason="test_invalid",
            supervisor_score=None,
            paperthin_philosophy_score=None,
        )
        harness.atomic_write_json(one_time_state_path, invalid_one_time_state)
        for text in (
            "메일을 작성해줘",
            "앞으로 사용자가 직접 요청하면 1회성으로 사용 가능하게 해줘",
            "앞으로 사용자가 직접 요청하면 1회성으로 사용 가능하게 하고, 다시 INVALID로 묶어놔.",
            "예시: 이번 요청만 하네스로 처리해",
            '"이번 요청만 하네스로 처리해"라는 문장을 분석해줘',
            "사용자가 이번 요청만 하네스를 사용해 달라고 요청했어",
            "이번 요청만 하네스로 처리해도 될까?",
        ):
            no_grant = harness.route_text(text, daytime, one_time_state_path)
            require(no_grant["mode"] == "codex_default", f"one_time_non_direct_request_blocked:{text}")
            require(load_json(one_time_state_path).get("one_time_grant") is None, f"one_time_non_direct_no_grant:{text}")

        one_time_variant_state_path = Path(temporary) / "one-time-variant" / "system_state.json"
        harness.atomic_write_json(one_time_variant_state_path, invalid_one_time_state)
        one_time_variant = harness.route_text(
            "딱 이 요청 하나에서만 하네스로 오류 코드 현황을 확인해줘.",
            daytime,
            one_time_variant_state_path,
        )
        require(
            one_time_variant["mode"] == "custom_agent_system_one_time",
            "one_time_instrumental_direct_request_authorized",
        )
        require(
            load_json(one_time_variant_state_path)["status"] == "INVALID",
            "one_time_instrumental_request_keeps_invalid",
        )

        granted = harness.route_text(
            "이번 요청만 하네스로 처리해서 담당자에게 메일을 작성해줘",
            daytime,
            one_time_state_path,
        )
        require(granted["mode"] == "custom_agent_system_one_time", "one_time_direct_request_authorized")
        require(granted["system_status"] == "INVALID", "one_time_route_keeps_invalid")
        require("active_agent_communication" in granted["active_agents"], "one_time_routes_required_active_agent")
        require(
            isinstance(granted["one_time_token"], str) and granted["one_time_token"].startswith("ot_"),
            "one_time_token_issued_with_cli_safe_prefix",
        )
        require(load_json(one_time_state_path)["status"] == "INVALID", "one_time_state_never_activates")
        second_open = harness.route_text(
            "이번 요청만 하네스로 처리해서 보고서를 작성해줘",
            daytime,
            one_time_state_path,
        )
        require(second_open["reason"] == "one_time_grant_already_open", "one_time_parallel_grant_blocked")

        token = granted["one_time_token"]
        first_spec = granted["task_spec"]
        no_token_gate = harness.pre_output_gate(
            granted["epoch"],
            one_time_state_path,
            expected_task_revision=first_spec["revision"],
            current_task_spec=first_spec,
        )
        require(no_token_gate["allowed"] is False, "one_time_output_requires_token")
        wrong_token_gate = harness.pre_output_gate(
            granted["epoch"],
            one_time_state_path,
            expected_task_revision=first_spec["revision"],
            current_task_spec=first_spec,
            one_time_token="wrong-token",
        )
        require("one_time_token_mismatch" in wrong_token_gate["reasons"], "one_time_wrong_token_blocked")
        revised = harness.route_text(
            "그게 아니라 이번 요청은 메일 말고 검증 보고서만 작성해줘",
            daytime,
            one_time_state_path,
            first_spec,
            one_time_token=token,
        )
        require(revised["mode"] == "custom_agent_system_one_time", "one_time_steering_continues_same_task")
        require(revised["task_spec"]["revision"] == first_spec["revision"] + 1, "one_time_steering_revision_increments")
        require(revised["task_spec"]["steering_type"] == "correction", "one_time_steering_correction_classified")
        require("active_agent_document" in revised["active_agents"], "one_time_steering_new_intent_routed")
        stale_gate = harness.pre_output_gate(
            granted["epoch"],
            one_time_state_path,
            expected_task_revision=first_spec["revision"],
            current_task_spec=first_spec,
            one_time_token=token,
        )
        require("one_time_task_revision_superseded" in stale_gate["reasons"], "one_time_stale_revision_blocked")
        current_spec = revised["task_spec"]
        missing_execution_gate = harness.pre_output_gate(
            revised["epoch"],
            one_time_state_path,
            expected_task_revision=current_spec["revision"],
            current_task_spec=current_spec,
            output_text="검증 보고서 초안을 작성했습니다.",
            one_time_token=token,
        )
        require(
            "runtime_execution_evidence_required" in missing_execution_gate["reasons"],
            "one_time_active_output_requires_execution_evidence",
        )
        unit_evidence = write_fabricated_execution_evidence(
            harness,
            Path(temporary) / "one-time" / "fabricated-execution.json",
            current_spec,
            token,
        )
        fabricated_gate = harness.pre_output_gate(
            revised["epoch"],
            one_time_state_path,
            expected_task_revision=current_spec["revision"],
            current_task_spec=current_spec,
            output_text="검증 보고서 초안을 작성했습니다.",
            one_time_token=token,
            execution_evidence_path=unit_evidence,
        )
        require(
            "execution_runtime_attestation_required" in fabricated_gate["reasons"]
            and fabricated_gate["allowed"] is False,
            "one_time_synthetic_receipts_never_complete_gate",
        )
        tampered_evidence = load_json(unit_evidence)
        tampered_evidence["invocations"][-1]["output_summary"]["score"] = 89
        harness.atomic_write_json(unit_evidence, tampered_evidence)
        tampered_gate = harness.pre_output_gate(
            revised["epoch"],
            one_time_state_path,
            expected_task_revision=current_spec["revision"],
            current_task_spec=current_spec,
            output_text="검증 보고서 초안을 작성했습니다.",
            one_time_token=token,
            execution_evidence_path=unit_evidence,
        )
        require(
            "execution_supervisor_not_passed" in tampered_gate["reasons"],
            "one_time_tampered_supervisor_evidence_blocked",
        )
        tampered_evidence["invocations"][2]["output_summary"]["stages"][1]["status"] = "failed"
        tampered_evidence["invocations"][2]["output_summary"]["stages"][1]["evidence"] = None
        tampered_evidence["invocations"][2]["input_digest"] = "x"
        harness.atomic_write_json(unit_evidence, tampered_evidence)
        structural_gate = harness.pre_output_gate(
            revised["epoch"],
            one_time_state_path,
            expected_task_revision=current_spec["revision"],
            current_task_spec=current_spec,
            output_text="검증 보고서 초안을 작성했습니다.",
            one_time_token=token,
            execution_evidence_path=unit_evidence,
        )
        require(
            "execution_digest_invalid" in structural_gate["reasons"]
            and any(reason.startswith("execution_active_stage_unproven:") for reason in structural_gate["reasons"]),
            "one_time_fabricated_receipt_structure_blocked",
        )
        harness.revoke_one_time_grant(one_time_state_path, token)
        revoked_state = load_json(one_time_state_path)
        require(revoked_state["status"] == "INVALID", "one_time_test_revoke_keeps_invalid")
        require(revoked_state.get("one_time_grant") is None, "one_time_test_revoke_clears_grant")
        reused_gate = harness.pre_output_gate(
            revised["epoch"],
            one_time_state_path,
            expected_task_revision=current_spec["revision"],
            current_task_spec=current_spec,
            one_time_token=token,
        )
        require(reused_gate["allowed"] is False, "one_time_revoked_token_not_reusable")

        one_time_passive_root = Path(temporary) / "one-time-passive"
        passive_dispatch = harness.dispatch_text(
            "이 요청에만 하네스를 사용해서 Omixprep 기준 경로는 C:\\one-time이야. 기록해줘",
            daytime,
            project="Omixprep",
            state_path=one_time_state_path,
            memory_root=one_time_passive_root,
        )
        require(passive_dispatch["mode"] == "custom_agent_system_one_time", "one_time_passive_dispatch_authorized")
        require(passive_dispatch["passive_result"]["status"] == "saved", "one_time_passive_save_executed")
        passive_token = passive_dispatch["one_time_token"]
        passive_spec = passive_dispatch["task_spec"]
        passive_final = harness.pre_output_gate(
            passive_dispatch["epoch"],
            one_time_state_path,
            expected_task_revision=passive_spec["revision"],
            current_task_spec=passive_spec,
            output_text="기준 경로를 기록했습니다.",
            one_time_token=passive_token,
        )
        require(passive_final["grant_consumed"] is True, "one_time_passive_grant_consumed")
        blocked_reuse = harness.execute_passive_record(
            "Omixprep 기준 경로는 C:\\reused-token이야.",
            project="Omixprep",
            expected_epoch=passive_dispatch["epoch"],
            state_path=one_time_state_path,
            memory_root=one_time_passive_root,
            one_time_token=passive_token,
            expected_task_revision=passive_spec["revision"],
            task_spec=passive_spec,
        )
        require(blocked_reuse["status"] == "blocked", "one_time_passive_reuse_blocked")

        grant_before_complaint = harness.route_text(
            "하네스를 지금 딱 한 번만 사용해서 코드를 구현해줘",
            daytime,
            one_time_state_path,
        )
        epoch_before_complaint = grant_before_complaint["epoch"]
        complaint = harness.route_text("이 답변은 별로야. 다시 해", daytime, one_time_state_path)
        complaint_state = load_json(one_time_state_path)
        require(complaint["mode"] == "codex_default", "one_time_direct_dissatisfaction_fallback")
        require(complaint_state.get("one_time_grant") is None, "one_time_direct_dissatisfaction_revokes")
        require(complaint_state["epoch"] == epoch_before_complaint + 1, "one_time_direct_dissatisfaction_bumps_epoch")

        expiring = harness.route_text(
            "하네스를 1회만 사용해서 현재 상태를 확인해줘",
            daytime,
            one_time_state_path,
        )
        expiring_state = load_json(one_time_state_path)
        expiring_state["one_time_grant"]["expires_at"] = "2000-01-01T00:00:00+09:00"
        harness.atomic_write_json(one_time_state_path, expiring_state)
        expired_gate = harness.pre_output_gate(
            expiring["epoch"],
            one_time_state_path,
            expected_task_revision=expiring["task_spec"]["revision"],
            current_task_spec=expiring["task_spec"],
            one_time_token=expiring["one_time_token"],
        )
        require("one_time_grant_expired" in expired_gate["reasons"], "one_time_expired_grant_blocked")
        require(load_json(one_time_state_path)["status"] == "INVALID", "one_time_expiry_keeps_invalid")

        require(
            harness.runtime_safety_gate("https://example.com 자료를 내려받아줘")["allowed"] is False,
            "runtime_non_github_network_blocked",
        )
        require(
            harness.runtime_safety_gate("curl example.com/data를 실행해줘")["allowed"] is False,
            "runtime_non_github_curl_blocked",
        )
        require(
            harness.runtime_safety_gate("pip install pandas를 실행해줘")["allowed"] is False,
            "runtime_package_network_blocked",
        )
        require(
            harness.runtime_safety_gate("nc example.com 443으로 연결해줘")["allowed"] is False,
            "runtime_raw_socket_network_blocked",
        )
        require(
            harness.runtime_safety_gate("Invoke-RestMethod example.com/api를 호출해줘")["allowed"] is False,
            "runtime_restmethod_non_github_blocked",
        )
        require(
            harness.runtime_safety_gate("https://github.com/rlagksqls17/harness_template를 확인해줘")["allowed"] is True,
            "runtime_github_network_allowed",
        )
        require(
            "server_mutation_forbidden"
            in harness.runtime_safety_gate("서버 /data 결과에 파일을 업로드하고 Docker를 재시작해줘")["reasons"],
            "runtime_server_mutation_blocked",
        )
        require(
            "server_mutation_forbidden"
            in harness.runtime_safety_gate("서버 /data/result.txt에 Set-Content로 값을 써줘")["reasons"],
            "runtime_powershell_server_mutation_blocked",
        )
        require(
            "server_mutation_forbidden"
            in harness.runtime_safety_gate("서버 [IO.File]::WriteAllText('/data/result.txt','x')를 실행해줘")["reasons"],
            "runtime_dotnet_server_mutation_blocked",
        )
        require(
            harness.runtime_safety_gate("서버 /data 결과를 읽기 전용으로 확인해줘")["allowed"] is True,
            "runtime_server_read_only_allowed",
        )
        fixture_root = ROOT / "test" / "runtime_e2e_proteomics"
        fixture_source = fixture_root / "input" / "proteomics_qc_plan.md"
        fixture_before = {
            path.relative_to(fixture_root).as_posix(): path.read_bytes()
            for path in fixture_root.rglob("*")
            if path.is_file()
        }
        with tempfile.TemporaryDirectory(prefix="runtime-e2e-", dir=ROOT / "test") as runtime_temporary:
            runtime_path = Path(runtime_temporary)
            runtime_state_path = runtime_path / "system_state.json"
            harness.atomic_write_json(runtime_state_path, invalid_one_time_state)
            runtime_result = harness.run_task_chain(
                "이번 요청에만 하네스를 사용해서 Proteomics QC 기획서 문서를 로컬 fixture에서 수정해줘. 필요한 Passive 내용을 조회해.",
                daytime,
                project="Proteomics",
                state_path=runtime_state_path,
                memory_root=fixture_root / "passive_memory",
                run_root=runtime_path / "run",
                fixture_source=fixture_source,
                fixture_append="서버는 읽기 전용이며 GitHub 외 인터넷을 사용하지 않는다.",
            )
            require(runtime_result["status"] == "completed", "proteomics_runtime_e2e_completed", str(runtime_result))
            require(
                runtime_result["invocation_order"]
                == [
                    "0_Prompt_Agent",
                    "1_Passive_Agent",
                    *runtime_result["task_spec"]["execution_contract"]["active_agents_required"],
                    "S_Supervisor_Agent",
                ],
                "proteomics_runtime_agent_order",
            )
            require(
                set(runtime_result["task_spec"]["execution_contract"]["required_passive_elements"])
                >= {"direction", "dev_env"},
                "proteomics_runtime_passive_direction_dev_env",
            )
            require(runtime_result["supervisor"]["decision"] == "PASS", "proteomics_runtime_supervisor_pass")
            require(runtime_result["pre_output_gate"]["grant_consumed"] is True, "proteomics_runtime_grant_consumed")
            runtime_final_state = load_json(runtime_state_path)
            require(runtime_final_state["status"] == "INVALID", "proteomics_runtime_returns_invalid")
            require(runtime_final_state.get("one_time_grant") is None, "proteomics_runtime_grant_cleared")
            require(
                runtime_result["execution_truth"]["runtime_execution_claimed"] is True,
                "proteomics_runtime_execution_proven",
            )
            lifecycle_path = runtime_path / "run" / "lifecycle.jsonl"
            lifecycle_events = [
                json.loads(line)
                for line in lifecycle_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            require(
                len(lifecycle_events) == len(runtime_result["invocation_order"]) * 2,
                "proteomics_runtime_lifecycle_start_end_pairs",
            )
            require(
                [event["phase"] for event in lifecycle_events] == ["started", "completed"] * len(runtime_result["invocation_order"]),
                "proteomics_runtime_lifecycle_order",
            )
            previous_event_hash = None
            for event in lifecycle_events:
                supplied_event_hash = event["event_hash"]
                unhashed_event = dict(event)
                unhashed_event.pop("event_hash")
                require(event["previous_event_hash"] == previous_event_hash, "proteomics_runtime_lifecycle_previous_hash")
                require(supplied_event_hash == harness._runtime_event_hash(unhashed_event), "proteomics_runtime_lifecycle_hash")
                previous_event_hash = supplied_event_hash
            runtime_snapshot = load_json(runtime_path / "run" / "runtime_status.json")
            require(runtime_snapshot["overall_status"] == "completed", "proteomics_runtime_snapshot_completed")
            require(runtime_snapshot["current_agent"] is None, "proteomics_runtime_snapshot_no_false_active")
            require(runtime_snapshot["evidence"]["path"] == runtime_result["evidence_path"], "proteomics_runtime_snapshot_evidence_linked")
            failed_state_path = runtime_path / "failed-system-state.json"
            harness.atomic_write_json(failed_state_path, invalid_one_time_state)
            failed_runtime = harness.run_task_chain(
                "이번 요청에만 하네스를 사용해서 Proteomics QC 기획서 문서를 로컬 fixture에서 수정해줘. 필요한 Passive 내용을 조회해.",
                daytime,
                project="Proteomics",
                state_path=failed_state_path,
                memory_root=fixture_root / "passive_memory",
                run_root=runtime_path / "failed-run",
                fixture_source=None,
                fixture_append="검증용 문장",
            )
            require(failed_runtime["status"] == "failed", "proteomics_runtime_missing_active_fixture_fails")
            require(
                load_json(failed_state_path).get("one_time_grant") is None,
                "proteomics_runtime_failure_revokes_grant",
            )
            require(load_json(runtime_path / "failed-run" / "runtime_status.json")["overall_status"] == "failed", "failed_runtime_snapshot_not_completed")
        fixture_after = {
            path.relative_to(fixture_root).as_posix(): path.read_bytes()
            for path in fixture_root.rglob("*")
            if path.is_file()
        }
        require(fixture_after == fixture_before, "proteomics_source_fixture_unchanged")
        loopback_guarded = False
        try:
            harness.serve_runtime_monitor("0.0.0.0", 8766)
        except ValueError:
            loopback_guarded = True
        require(loopback_guarded, "runtime_monitor_non_loopback_rejected")

        empty_begin_failed = False
        empty_begin_path = Path(temporary) / "empty-begin" / "system_state.json"
        harness.atomic_write_json(empty_begin_path, load_json(state_path))
        try:
            harness.transition("begin-rebuild", empty_begin_path, build_id="   ")
        except ValueError:
            empty_begin_failed = True
        require(empty_begin_failed, "empty_begin_build_id_rejected")

        def candidate_case(case_name, candidate_id):
            case_path = Path(temporary) / case_name / "system_state.json"
            timestamp = harness.datetime.now(harness.KST).isoformat(timespec="seconds")
            candidate_state = active_state()
            candidate_state.update(
                status="CANDIDATE",
                candidate_build_id=candidate_id,
                candidate_digest=harness.candidate_content_digest(),
                updated_at=timestamp,
            )
            harness.atomic_write_json(case_path, candidate_state)
            case_evidence_dir = case_path.parent / "supervisor_evidence"
            case_evidence_dir.mkdir()
            case_evidence_path = case_evidence_dir / f"{case_name}.json"
            harness.atomic_write_json(
                case_evidence_path,
                {
                    "build_id": candidate_id,
                    "supervisor_agent": "S_Supervisor_Agent",
                    "independent": True,
                    "supervisor_score": 90,
                    "paperthin_philosophy_score": 80,
                    "validator_pass": True,
                    "evaluated_at": timestamp,
                    "candidate_digest": candidate_state["candidate_digest"],
                },
            )
            return case_path, case_evidence_path

        for case_name, candidate_id in (("null-candidate", None), ("empty-candidate", "")):
            case_path, case_evidence_path = candidate_case(case_name, candidate_id)
            empty_activate_failed = False
            try:
                harness.transition("activate", case_path, evidence_path=case_evidence_path)
            except ValueError:
                empty_activate_failed = True
            require(empty_activate_failed, f"empty_candidate_activation_rejected:{case_name}")

        race_state_path, race_evidence_path = candidate_case("activation-race", "race-build")
        original_evidence_loader = harness.load_supervisor_evidence

        def invalidate_during_activation(candidate_state_path, candidate_evidence_path, state):
            checked = original_evidence_loader(candidate_state_path, candidate_evidence_path, state)
            changed = load_json(candidate_state_path)
            changed.update(
                status="INVALID",
                epoch=int(changed.get("epoch", 0)) + 1,
                candidate_build_id=None,
                candidate_digest=None,
                invalidation_reason="concurrent_negative_feedback",
                updated_at=harness.datetime.now(harness.KST).isoformat(timespec="seconds"),
            )
            harness.atomic_write_json(candidate_state_path, changed)
            return checked

        harness.load_supervisor_evidence = invalidate_during_activation
        race_rejected = False
        try:
            harness.transition("activate", race_state_path, evidence_path=race_evidence_path)
        except ValueError:
            race_rejected = True
        finally:
            harness.load_supervisor_evidence = original_evidence_loader
        require(race_rejected, "activation_state_change_rejected")
        require(load_json(race_state_path)["status"] == "INVALID", "activation_does_not_overwrite_invalid")

        harness.transition("begin-rebuild", state_path, build_id="candidate-test")
        harness.transition("candidate", state_path)
        evidence_dir = state_path.parent / "supervisor_evidence"
        evidence_dir.mkdir()
        evidence_path = evidence_dir / "candidate-test.json"
        evidence = {
            "build_id": "candidate-test",
            "supervisor_agent": "S_Supervisor_Agent",
            "independent": True,
            "supervisor_score": 89,
            "paperthin_philosophy_score": 100,
            "validator_pass": True,
            "evaluated_at": harness.datetime.now(harness.KST).isoformat(timespec="seconds"),
            "candidate_digest": load_json(state_path)["candidate_digest"],
        }
        harness.atomic_write_json(evidence_path, evidence)
        failed_89 = False
        try:
            harness.transition("activate", state_path, evidence_path=evidence_path)
        except ValueError:
            failed_89 = True
        require(failed_89, "score_89_rejected")
        evidence["supervisor_score"] = 90
        evidence["paperthin_philosophy_score"] = 79
        harness.atomic_write_json(evidence_path, evidence)
        failed_79 = False
        try:
            harness.transition("activate", state_path, evidence_path=evidence_path)
        except ValueError:
            failed_79 = True
        require(failed_79, "philosophy_score_79_rejected")
        evidence["supervisor_score"] = 101
        evidence["paperthin_philosophy_score"] = 101
        harness.atomic_write_json(evidence_path, evidence)
        failed_101 = False
        try:
            harness.transition("activate", state_path, evidence_path=evidence_path)
        except ValueError:
            failed_101 = True
        require(failed_101, "scores_above_100_rejected")
        evidence["supervisor_score"] = 90
        evidence["paperthin_philosophy_score"] = 80
        evidence["evaluated_at"] = "not-an-iso-time"
        harness.atomic_write_json(evidence_path, evidence)
        invalid_time_failed = False
        try:
            harness.transition("activate", state_path, evidence_path=evidence_path)
        except ValueError:
            invalid_time_failed = True
        require(invalid_time_failed, "invalid_evaluated_at_rejected")
        evidence["evaluated_at"] = harness.datetime.now(harness.KST).isoformat(timespec="seconds")
        evidence["candidate_digest"] = "0" * 64
        harness.atomic_write_json(evidence_path, evidence)
        wrong_digest_failed = False
        try:
            harness.transition("activate", state_path, evidence_path=evidence_path)
        except ValueError:
            wrong_digest_failed = True
        require(wrong_digest_failed, "candidate_digest_mismatch_rejected")
        evidence["candidate_digest"] = load_json(state_path)["candidate_digest"]
        harness.atomic_write_json(evidence_path, evidence)
        evidence["paperthin_philosophy_score"] = 80
        harness.atomic_write_json(evidence_path, evidence)
        activated = harness.transition("activate", state_path, evidence_path=evidence_path)
        require(activated["status"] == "ACTIVE", "score_90_and_80_activate")
        active_rebuild_rejected = False
        try:
            harness.transition("begin-rebuild", state_path, build_id="illegal-active-rebuild")
        except ValueError:
            active_rebuild_rejected = True
        require(active_rebuild_rejected, "active_to_rebuilding_rejected")

        nighttime = harness.now_kst("2026-08-29T23:00:00+09:00")
        outside = harness.route_text("메일을 작성해줘", nighttime, state_path)
        require(outside["mode"] == "codex_default", "outside_08_22_falls_back")

    print("SYSTEM_VALIDATION_PASS")
    print("[INFO] Prompt, Passive, selected Active, and Supervisor executed as separate local worker processes in the Proteomics fixture E2E.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("SYSTEM_VALIDATION_FAIL", file=sys.stderr)
        raise SystemExit(1)
