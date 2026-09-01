#!/usr/bin/env python3
"""Minimal runtime gate for the METAGENOME_WGS Agent contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "system" / "system_state.json"
TRUSTED_SIGNERS_PATH = ROOT / "system" / "trusted_signers.allowed_signers"
KST = timezone(timedelta(hours=9))
ACTIVE_AGENTS = {
    "active_agent_communication",
    "active_agent_development",
    "active_agent_analysis",
    "active_agent_inspection",
    "active_agent_document",
    "active_agent_schedule",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(KST)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def in_operation_window(current: datetime) -> bool:
    return 8 <= current.hour < 22


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def mask_quoted_text(text: str) -> str:
    value = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    value = "\n".join(line for line in value.splitlines() if not line.lstrip().startswith(">"))
    for pattern in (r'".*?"', r"'.*?'", r"“.*?”", r"‘.*?’", r"「.*?」", r"『.*?』"):
        value = re.sub(pattern, " ", value, flags=re.DOTALL)
    return value


def is_direct_dissatisfaction(text: str) -> bool:
    value = normalize(mask_quoted_text(text))
    if not value:
        return False
    if re.search(r"(?:negative[ -]?control|부정\s*대조).*(?:분류|예시|예문|문장|테스트)|(?:분류|예시|예문|문장|테스트).*(?:negative[ -]?control|부정\s*대조)", value):
        return False
    if re.search(
        r"(?:잘못(?:된|한)?\s*(?:게|것이)?|문제가?|불만이?|엉망이?|별로(?:가|라는? 뜻은?)?)\s*(?:아니|없)|"
        r"(?:아니|않은)\s*(?:잘못|문제|불만|엉망|별로)",
        value,
    ):
        return False
    if re.search(r"(?:외부|다른|타사|제3자|제3자의|고객|다른 팀|동료).{0,30}(?:agent|에이전트|답변|응답|출력|결과).{0,40}(?:평가|분석|검토|잘못|틀렸)", value):
        return False
    target = re.search(r"하네스|에이전트|agent|너(?:가|는|의)?|네가|답변|응답|출력|결과|처리", value)
    if not target:
        return False
    patterns = (
        r"왜.*(?:하네스|에이전트|agent)",
        r"(?:검증|확인).*(?:안 |없이).*(?:에이전트|agent|답변|응답|출력|결과).*(?:완료|통과|됐다고|했다고|했잖)",
        r"(?:에이전트|agent|답변|응답|출력|결과).*(?:검증|확인).*(?:안 |없이).*(?:완료|통과|됐다고|했다고|했잖)",
        r"(?:하네스|에이전트|agent|답변|응답|출력|결과|처리).*(?:안 |못 |실수|잘못|틀렸|엉망|해제|전혀|누락|별로)",
        r"(?:똑바로|제대로|그게 아니라|사용하라고 했잖|다시 하네스 사용해서)",
        r"여태까지.*뭐한",
        r"(?:agent|에이전트|답변|응답|출력|결과|처리).*(?:아쉽|마음에 들지 않|불만|답답|화나|구독 취소|별로|잘못|틀렸)",
        r"너.{0,20}(?:별로|잘못|틀렸|마음에 들지 않|답답)",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def steering_kind(text: str) -> str | None:
    value = normalize(text)
    if re.search(r"그게 아니라|^아니(?:요|야|,| )|다시|수정|대신|제외|말고|방향을 바|범위.{0,20}바꿔|취소(?:하고|해|한 뒤)", value):
        return "correction"
    if re.search(r"추가|그리고|또한|조건도|까지 포함|더 (?:보태|넣|붙여)|also|in addition", value):
        return "addition"
    return None


def classify_relation(text: str, prior: dict[str, Any] | None) -> str:
    if prior is None:
        return "new_task"
    return steering_kind(text) or "correction"


def is_explanation_request(text: str) -> bool:
    value = normalize(text)
    asks = re.search(r"설명|뭐야|무엇|무슨|어떻게 (?:돌아|구성)|구조|방법만|뜻", value)
    explicit_build = re.search(r"구축해|개발해|수정해|실행해|분석해|만들어|접속해줘|배포해", value)
    explicit_no_action = re.search(r"실제 .*하지 마|설명만|방법만|접속하지", value)
    return bool(asks and (not explicit_build or explicit_no_action))


def choose_route(text: str) -> tuple[str, str | None]:
    value = normalize(text)
    if re.search(r"메일|email|회신|메시지|전달문|소통", value):
        return "active_agent_communication", None
    if re.search(r"일정|스케줄|예약|reminder|calendar", value):
        return "active_agent_schedule", None
    if is_explanation_request(text):
        return "active_agent_communication", None
    if re.search(r"구축|개발|구현|코드|수정|고쳐|빌드|pipeline|파이프라인", value):
        return "active_agent_development", None
    if re.search(r"분석.*(?:프로세스|실행 상태|진행 상태|job|로그|컨테이너|runtime)|(?:프로세스|job|로그|컨테이너|runtime).*분석", value):
        return "active_agent_analysis", "analysis_process_status"
    if re.search(r"분석|통계|비교|qc|결과 해석|생물학", value):
        return "active_agent_analysis", "data_analysis"
    if re.search(r"상태|현황|파악|점검|목록|읽기 전용", value):
        return "active_agent_inspection", None
    if re.search(r"보고서|문서|매뉴얼|정리본|표로", value):
        return "active_agent_document", None
    return "active_agent_communication", None


def passive_requests(text: str, explanation: bool, agent: str) -> list[str]:
    requested = ["feedback", "direction", "user_info"]
    if agent in {"active_agent_development", "active_agent_analysis", "active_agent_inspection"} or re.search(
        r"서버|docker|경로|환경|gpu|cpu", normalize(text)
    ):
        requested.append("dev_env")
    if not explanation and re.search(r"이전|기존|기억|지난|계속", normalize(text)):
        requested.append("record")
    return requested


def approval_contract(text: str, explanation: bool) -> dict[str, Any]:
    value = normalize(text)
    actions: list[str] = []
    if re.search(r"서버.*(?:접속|변경|실행|구축)|ssh|aica", value):
        actions.append("server_access_or_mutation")
    if re.search(r"docker.*(?:빌드|삭제|변경|실행)|이미지.*(?:빌드|삭제|변경)", value):
        actions.append("docker_mutation")
    if re.search(r"push|commit|커밋|푸시", value):
        actions.append("git_shared_history")
    if explanation or re.search(r"하지 마|읽기만|읽기 전용|설명만|방법만", value):
        actions = []
    return {
        "required": bool(actions),
        "actions": actions,
        "status": "requires_separate_confirmation" if actions else "not_required",
        "authorization_is_not_execution": True,
    }


def task_digest(task: dict[str, Any]) -> str:
    payload = {key: value for key, value in task.items() if key != "task_digest"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_task_spec(text: str, prior: dict[str, Any] | None = None) -> dict[str, Any]:
    relation = classify_relation(text, prior)
    revision = int(prior.get("revision", 0)) + 1 if prior else 1
    task_id = str(prior.get("task_id")) if prior and prior.get("task_id") else str(uuid.uuid4())
    explanation = is_explanation_request(text)
    agent, analysis_mode = choose_route(text)
    if agent not in ACTIVE_AGENTS:
        raise ValueError(f"unregistered active agent: {agent}")
    if relation == "addition" and prior:
        goal = prior.get("goal", text)
        constraints = list(prior.get("constraints", [])) + [text]
    else:
        goal = text
        constraints = list(prior.get("constraints", [])) if prior and relation == "correction" else []
    task: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "revision": revision,
        "message_relation": relation,
        "goal": goal,
        "constraints": constraints,
        "route": {
            "selected_agent": agent,
            "analysis_mode": analysis_mode,
            "selection_is_execution_evidence": False,
        },
        "passive_context": {
            "requested": passive_requests(text, explanation, agent),
            "status": "requested_not_loaded",
        },
        "deliverable": "chat_explanation" if explanation else "authorized_task_result",
        "record_policy": "none" if explanation else "confirmed_facts_only",
        "response_contract": {
            "order": ["identity", "execution_actor", "actual_structure"]
            if explanation
            else ["result", "meaning", "next_action"],
            "default_lines": "3-8",
        },
        "approval": approval_contract(text, explanation),
        "execution": {
            "status": "not_started",
            "actor": "Codex_or_explicitly_approved_worker",
            "routing_or_dispatch_is_not_execution": True,
            "required_order": ["preparation", "execution", "verification", "completion"],
        },
        "revision_control": {
            "supersedes_revision": int(prior.get("revision", 0)) if prior else None,
            "prior_execution_cancelled": relation == "correction",
            "prior_output_stale": relation == "correction",
            "merged_addition": relation == "addition",
        },
    }
    task["task_digest"] = task_digest(task)
    return task


def invalidate(state_path: Path, reason: str) -> dict[str, Any]:
    state = load_json(state_path)
    if state.get("status") != "INVALID":
        state["epoch"] = int(state.get("epoch", 0)) + 1
    state["status"] = "INVALID"
    state["last_invalidation_reason"] = reason
    state["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    atomic_write_json(state_path, state)
    return state


def route_text(
    text: str,
    state_path: Path = DEFAULT_STATE_PATH,
    now_value: str | None = None,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if is_direct_dissatisfaction(text):
        state = invalidate(state_path, "direct_user_dissatisfaction")
        return {
            "allowed": False,
            "action": "fallback_default_codex",
            "reason": "direct_user_dissatisfaction",
            "system_status": state["status"],
            "epoch": state["epoch"],
            "agents_called": [],
            "passive_loaded": False,
            "record_written": False,
        }
    state = load_json(state_path)
    current = parse_now(now_value)
    if state.get("status") != "ACTIVE":
        return {
            "allowed": False,
            "action": "fallback_default_codex",
            "reason": "system_not_active",
            "system_status": state.get("status"),
            "epoch": state.get("epoch"),
        }
    if not in_operation_window(current):
        return {
            "allowed": False,
            "action": "fallback_default_codex",
            "reason": "outside_user_operation_window",
            "system_status": state.get("status"),
            "epoch": state.get("epoch"),
        }
    task = build_task_spec(text, prior)
    latest_state = load_json(state_path)
    if latest_state.get("status") != "ACTIVE" or int(latest_state.get("epoch", -1)) != int(state.get("epoch", -2)):
        return {
            "allowed": False,
            "action": "fallback_default_codex",
            "reason": "system_state_changed_during_routing",
            "system_status": latest_state.get("status"),
            "epoch": latest_state.get("epoch"),
        }
    latest_state["current_task"] = {
        "task_id": task["task_id"],
        "revision": task["revision"],
        "task_digest": task["task_digest"],
    }
    atomic_write_json(state_path, latest_state)
    state = latest_state
    return {
        "allowed": True,
        "action": "route",
        "system_status": state["status"],
        "epoch": state["epoch"],
        "task_spec": task,
    }


def load_passive_context(task: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / "Agent" / "1_Passive_Agent" / "memory" / "current.json"
    if not path.exists():
        return {"status": "not_available", "loaded": False, "requested": task["passive_context"]["requested"]}
    payload = load_json(path)
    return {
        "status": "loaded_read_only",
        "loaded": True,
        "requested": task["passive_context"]["requested"],
        "source": "Agent/1_Passive_Agent/memory/current.json",
        "source_revision": payload.get("revision"),
        "provenance_only": True,
    }


def dispatch_text(
    text: str,
    state_path: Path = DEFAULT_STATE_PATH,
    now_value: str | None = None,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    routed = route_text(text, state_path, now_value, prior)
    if not routed.get("allowed"):
        routed["action"] = "dispatch_blocked"
        return routed
    task = routed["task_spec"]
    return {
        "allowed": True,
        "action": "dispatch_contract",
        "system_status": routed["system_status"],
        "epoch": routed["epoch"],
        "task_spec": task,
        "passive_context": load_passive_context(task),
        "selected_agent": task["route"]["selected_agent"],
        "agent_execution_status": "not_started",
        "external_action_status": "not_started",
        "execution_evidence": None,
        "statement": "dispatch selected a role contract; Codex or another approved worker must still perform the work",
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_manifest_digest(artifacts: list[dict[str, Any]]) -> str:
    canonical = sorted(artifacts, key=lambda item: (str(item.get("path", "")), str(item.get("sha256", ""))))
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_signed_payload(value: dict[str, Any], signature_field: str) -> bytes:
    payload = {key: item for key, item in value.items() if key != signature_field}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def trusted_actor_key(actor: Any, namespace: str) -> tuple[str, str] | None:
    if not actor or not TRUSTED_SIGNERS_PATH.is_file():
        return None
    try:
        lines = TRUSTED_SIGNERS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            fields = shlex.split(stripped)
        except ValueError:
            continue
        if not fields or str(actor) not in fields[0].split(","):
            continue
        namespaces = [field.split("=", 1)[1] for field in fields if field.startswith("namespaces=")]
        if not namespaces or namespace not in namespaces[0].split(","):
            continue
        for index, field in enumerate(fields[:-1]):
            if field.startswith(("ssh-", "ecdsa-", "sk-")):
                return field, fields[index + 1]
    return None


def verify_ssh_signature(
    payload: dict[str, Any],
    signature_field: str,
    actor: Any,
    namespace: str,
    base_path: Path,
) -> bool:
    signature_value = payload.get(signature_field)
    if not actor or not signature_value or not TRUSTED_SIGNERS_PATH.is_file():
        return False
    signature_path = Path(str(signature_value))
    if not signature_path.is_absolute():
        signature_path = base_path / signature_path
    if not signature_path.is_file():
        return False
    try:
        completed = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify", "-f", str(TRUSTED_SIGNERS_PATH),
                "-I", str(actor), "-n", namespace, "-s", str(signature_path),
            ],
            input=canonical_signed_payload(payload, signature_field),
            capture_output=True,
            check=False,
        )
    except (OSError, ValueError):
        return False
    return completed.returncode == 0


def validate_verifier_receipt(
    receipt_value: Any,
    evidence_path: Path,
    evidence: dict[str, Any],
    task: dict[str, Any],
    expected_kind: str,
) -> bool:
    if not receipt_value:
        return False
    receipt_path = Path(str(receipt_value))
    if not receipt_path.is_absolute():
        receipt_path = evidence_path.parent / receipt_path
    if not receipt_path.is_file():
        return False
    try:
        receipt = load_json(receipt_path)
    except Exception:
        return False
    return bool(
        receipt.get("kind") == expected_kind
        and receipt.get("status") == "passed"
        and receipt.get("evidence_id") == evidence.get("evidence_id")
        and receipt.get("task_id") == task.get("task_id")
        and int(receipt.get("task_revision", -1)) == int(task.get("revision", -2))
        and receipt.get("artifact_manifest_digest") == evidence.get("artifact_manifest_digest")
        and receipt.get("task_digest") == task.get("task_digest")
        and receipt.get("verifier_actor")
        and receipt.get("verifier_actor") != evidence.get("worker_actor")
        and trusted_actor_key(receipt.get("verifier_actor"), "metagenome-wgs-verifier")
        != trusted_actor_key(evidence.get("worker_actor"), "metagenome-wgs-worker")
        and verify_ssh_signature(
            receipt,
            "signature_path",
            receipt.get("verifier_actor"),
            "metagenome-wgs-verifier",
            receipt_path.parent,
        )
    )


def validate_execution_evidence(
    evidence_path: Path | None,
    task: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, bool], list[str]]:
    reasons: list[str] = []
    if evidence_path is None or not evidence_path.is_file():
        return None, {"runtime": False, "scientific": False}, ["execution_evidence_file_missing"]
    try:
        evidence = load_json(evidence_path)
    except Exception:
        return None, {"runtime": False, "scientific": False}, ["execution_evidence_file_invalid"]
    if evidence.get("task_id") != task.get("task_id"):
        reasons.append("execution_evidence_task_mismatch")
    if int(evidence.get("task_revision", -1)) != int(task.get("revision", -2)):
        reasons.append("execution_evidence_revision_mismatch")
    if evidence.get("task_digest") != task.get("task_digest"):
        reasons.append("execution_evidence_task_digest_mismatch")
    if evidence.get("kind") != "actual_execution":
        reasons.append("execution_evidence_kind_invalid")
    if evidence.get("execution_complete") is not True:
        reasons.append("execution_not_complete")
    if not evidence.get("worker_actor"):
        reasons.append("execution_worker_actor_missing")
    elif not verify_ssh_signature(
        evidence,
        "worker_signature_path",
        evidence.get("worker_actor"),
        "metagenome-wgs-worker",
        evidence_path.parent,
    ):
        reasons.append("execution_worker_signature_untrusted")
    artifacts = evidence.get("artifacts")
    checked_artifacts: list[dict[str, Any]] = []
    if not isinstance(artifacts, list) or not artifacts:
        reasons.append("execution_artifacts_missing")
    else:
        for item in artifacts:
            if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
                reasons.append("execution_artifact_entry_invalid")
                continue
            if (
                item.get("role") != "task_deliverable"
                or item.get("task_id") != task.get("task_id")
                or int(item.get("task_revision", -1)) != int(task.get("revision", -2))
                or item.get("task_digest") != task.get("task_digest")
            ):
                reasons.append("execution_artifact_task_binding_invalid")
            artifact_path = Path(str(item["path"]))
            if not artifact_path.is_absolute():
                reasons.append("execution_artifact_path_not_absolute")
                continue
            if not artifact_path.is_file():
                reasons.append("execution_artifact_missing")
                continue
            actual_size = artifact_path.stat().st_size
            actual_sha = sha256_file(artifact_path)
            if int(item.get("size", -1)) != actual_size:
                reasons.append("execution_artifact_size_mismatch")
            if str(item.get("sha256")).lower() != actual_sha:
                reasons.append("execution_artifact_hash_mismatch")
            checked_artifacts.append(
                {
                    "path": str(item["path"]),
                    "sha256": actual_sha,
                    "size": actual_size,
                    "role": item.get("role"),
                    "task_id": item.get("task_id"),
                    "task_revision": item.get("task_revision"),
                    "task_digest": item.get("task_digest"),
                }
            )
    computed_manifest_digest = artifact_manifest_digest(checked_artifacts) if checked_artifacts else None
    if not computed_manifest_digest or evidence.get("artifact_manifest_digest") != computed_manifest_digest:
        reasons.append("execution_artifact_manifest_digest_mismatch")
    evidence_id = evidence.get("evidence_id")
    entry_id = evidence.get("ledger_entry_id")
    ledger_value = evidence.get("ledger_path")
    if not evidence_id or not entry_id or not ledger_value:
        reasons.append("execution_evidence_ledger_binding_missing")
        return evidence, {"runtime": False, "scientific": False}, reasons
    ledger_path = Path(str(ledger_value))
    if not ledger_path.is_absolute():
        ledger_path = evidence_path.parent / ledger_path
    if not ledger_path.is_file():
        reasons.append("execution_evidence_ledger_missing")
        return evidence, {"runtime": False, "scientific": False}, reasons
    linked = False
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if (
                item.get("entry_id") == entry_id
                and item.get("evidence_id") == evidence_id
                and item.get("task_id") == task.get("task_id")
                and int(item.get("task_revision", -1)) == int(task.get("revision", -2))
                and item.get("task_digest") == task.get("task_digest")
                and item.get("kind") == "actual_execution"
                and item.get("execution_complete") is True
                and item.get("artifact_manifest_digest") == evidence.get("artifact_manifest_digest")
            ):
                linked = True
                break
    except Exception:
        reasons.append("execution_evidence_ledger_invalid")
        return evidence, {"runtime": False, "scientific": False}, reasons
    if not linked:
        reasons.append("execution_evidence_ledger_entry_missing")
    receipts = {
        "runtime": validate_verifier_receipt(
            evidence.get("runtime_verifier_receipt"), evidence_path, evidence, task, "runtime_verification"
        ),
        "scientific": validate_verifier_receipt(
            evidence.get("scientific_verifier_receipt"), evidence_path, evidence, task, "scientific_verification"
        ),
    }
    return evidence, receipts, reasons


def pre_output_gate(
    state_path: Path,
    expected_epoch: int,
    expected_revision: int,
    task: dict[str, Any],
    evidence_status: str,
    execution_evidence_path: Path | None,
    output_text: str,
    now_value: str | None,
    latest_text: str | None,
) -> dict[str, Any]:
    if latest_text and is_direct_dissatisfaction(latest_text):
        state = invalidate(state_path, "direct_user_dissatisfaction_before_output")
        return {"allowed": False, "reasons": ["direct_user_dissatisfaction"], "status": state["status"], "epoch": state["epoch"]}
    latest_steering = steering_kind(latest_text) if latest_text else None
    state = load_json(state_path)
    reasons: list[str] = []
    if latest_steering:
        reasons.append(f"task_revision_superseded_by_{latest_steering}")
    if state.get("status") != "ACTIVE":
        reasons.append("system_not_active")
    if int(state.get("epoch", -1)) != expected_epoch:
        reasons.append("epoch_changed")
    if int(task.get("revision", -1)) != expected_revision:
        reasons.append("task_revision_changed")
    if task.get("task_digest") != task_digest(task):
        reasons.append("task_digest_changed")
    current_task = state.get("current_task")
    if not isinstance(current_task, dict):
        reasons.append("current_task_registry_missing")
    else:
        if current_task.get("task_id") != task.get("task_id"):
            reasons.append("current_task_id_changed")
        if int(current_task.get("revision", -1)) != int(task.get("revision", -2)):
            reasons.append("current_task_revision_changed")
        if current_task.get("task_digest") != task.get("task_digest"):
            reasons.append("current_task_digest_changed")
    if not in_operation_window(parse_now(now_value)):
        reasons.append("outside_user_operation_window")
    execution_claim = bool(
        re.search(
            r"완료|끝냈|마쳤|다 됐|\bdone\b|성공적으로.{0,20}(?:수행|처리|실행|분석|구축|변경|작성)|"
            r"(?:수행|처리|실행|분석|구축|변경|작성)(?:했|됐|되었|되었습니다)|"
            r"\bsuccessfully\s+(?:performed|executed|completed|processed|analy[sz]ed|built|changed|written)\b|"
            r"\b(?:analysis|execution|task|request|work|processing|implementation|build|deployment)\s+"
            r"(?:has\s+been\s+)?(?:completed|performed|executed|finished|succeeded|successful)\b|"
            r"\bhas\s+been\s+completed\b|\b(?:completed|performed|executed|finished)\s+successfully\b|"
            r"\b(?:i|we)\s+(?:have\s+)?(?:finished|completed|performed|executed)\b|"
            r"\b(?:analysis|execution|task|request|work|processing|implementation|build|deployment)\s+"
            r"(?:is|was)\s+(?:complete|completed|finished|successful)\b",
            output_text,
            flags=re.IGNORECASE,
        )
    )
    verification_claim = bool(
        re.search(
            r"\bpass(?:ed)?\b|통과|(?:검증|확인).{0,12}(?:완료|성공)|"
            r"\b(?:verification|validation|verified).{0,16}(?:succeeded|successful|successfully|passed|completed|complete)\b",
            output_text,
            flags=re.IGNORECASE,
        )
    )
    scientific_claim = bool(
        re.search(
            r"(?:과학|생물학|scientific|qc).{0,12}(?:pass|통과|성공|완료|적합)|분석.{0,12}(?:pass|통과|성공|적합)|분석\s*검증.{0,12}(?:완료|성공|통과|적합)|"
            r"\b(?:qc|quality\s+control|biological\s+validation|scientific\s+validation).{0,16}"
            r"(?:passed|succeeded|successful|completed|validated)\b|\bscientifically\s+validated\b",
            output_text,
            flags=re.IGNORECASE,
        )
    )
    evidence: dict[str, Any] | None = None
    receipts = {"runtime": False, "scientific": False}
    if evidence_status == "completed":
        evidence, receipts, evidence_reasons = validate_execution_evidence(execution_evidence_path, task)
        reasons.extend(evidence_reasons)
    if execution_claim and evidence_status != "completed":
        reasons.append("completion_claim_without_completed_evidence")
    if verification_claim and not receipts["runtime"]:
        reasons.append("verification_pass_without_runtime_evidence")
    if scientific_claim and not receipts["scientific"]:
        reasons.append("scientific_pass_without_independent_evidence")
    if task.get("approval", {}).get("required") and task.get("approval", {}).get("status") != "approved":
        reasons.append("required_approval_not_confirmed")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "status": state.get("status"),
        "epoch": state.get("epoch"),
        "task_revision": task.get("revision"),
        "evidence_status": evidence_status,
        "execution_evidence_id": evidence.get("evidence_id") if evidence else None,
    }


def load_prior(path: str | None) -> dict[str, Any] | None:
    return load_json(Path(path)) if path else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state")
    for name in ("route", "dispatch"):
        command = sub.add_parser(name)
        command.add_argument("--text", required=True)
        command.add_argument("--now")
        command.add_argument("--prior-task-spec")
    gate = sub.add_parser("pre-output-gate")
    gate.add_argument("--expected-epoch", type=int, required=True)
    gate.add_argument("--expected-task-revision", type=int, required=True)
    gate.add_argument("--current-task-spec", type=Path, required=True)
    gate.add_argument("--evidence-status", choices=["completed", "not_started", "not_verified", "not_required"], required=True)
    gate.add_argument("--execution-evidence", type=Path)
    gate.add_argument("--output-text", required=True)
    gate.add_argument("--latest-text")
    gate.add_argument("--now")
    invalidate_parser = sub.add_parser("invalidate")
    invalidate_parser.add_argument("--reason", default="explicit_manual_invalidation")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "state":
        result = load_json(args.state_path)
    elif args.command == "route":
        result = route_text(args.text, args.state_path, args.now, load_prior(args.prior_task_spec))
    elif args.command == "dispatch":
        result = dispatch_text(args.text, args.state_path, args.now, load_prior(args.prior_task_spec))
    elif args.command == "pre-output-gate":
        result = pre_output_gate(
            args.state_path,
            args.expected_epoch,
            args.expected_task_revision,
            load_json(args.current_task_spec),
            args.evidence_status,
            args.execution_evidence,
            args.output_text,
            args.now,
            args.latest_text,
        )
    else:
        result = invalidate(args.state_path, args.reason)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("allowed", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
