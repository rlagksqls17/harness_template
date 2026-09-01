from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("metagenome_harness", ROOT / "src" / "harness.py")
assert SPEC and SPEC.loader
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


class HarnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.signing_temp = tempfile.TemporaryDirectory()
        signing_root = Path(cls.signing_temp.name)
        cls.worker_key = signing_root / "worker"
        cls.verifier_key = signing_root / "verifier"
        for key in (cls.worker_key, cls.verifier_key):
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                check=True,
                capture_output=True,
            )
        cls.allowed_signers = signing_root / "allowed_signers"
        worker_public = cls.worker_key.with_suffix(".pub").read_text(encoding="utf-8").split()
        verifier_public = cls.verifier_key.with_suffix(".pub").read_text(encoding="utf-8").split()
        cls.allowed_signers.write_text(
            f'approved_worker namespaces="metagenome-wgs-worker" {worker_public[0]} {worker_public[1]}\n'
            f'independent_verifier namespaces="metagenome-wgs-verifier" {verifier_public[0]} {verifier_public[1]}\n',
            encoding="utf-8",
        )
        cls.original_trusted_signers = harness.TRUSTED_SIGNERS_PATH
        harness.TRUSTED_SIGNERS_PATH = cls.allowed_signers

    @classmethod
    def tearDownClass(cls) -> None:
        harness.TRUSTED_SIGNERS_PATH = cls.original_trusted_signers
        cls.signing_temp.cleanup()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_path = self.root / "state.json"
        self.write_state("ACTIVE", 7)
        self.inside = "2026-09-01T21:00:00+09:00"
        self.evidence_counter = 0

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_state(self, status: str, epoch: int) -> None:
        self.state_path.write_text(
            json.dumps({"schema_version": 1, "status": status, "epoch": epoch, "build_id": "test"}),
            encoding="utf-8",
        )

    def route(self, text: str, prior=None):
        return harness.route_text(text, self.state_path, self.inside, prior)

    def write_signed_json(self, payload, path, signature_field, key, namespace):
        unsigned = path.with_suffix(".unsigned.json")
        unsigned.write_bytes(harness.canonical_signed_payload(payload, signature_field))
        subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace, str(unsigned)],
            check=True,
            capture_output=True,
        )
        payload[signature_field] = str(Path(str(unsigned) + ".sig"))
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def write_evidence(
        self,
        task,
        *,
        kind="actual_execution",
        runtime_receipt=True,
        scientific_receipt=False,
        verifier_key=None,
    ):
        self.evidence_counter += 1
        verifier_key = verifier_key or self.verifier_key
        suffix = str(self.evidence_counter)
        evidence_id = f"evidence-{suffix}"
        entry_id = f"ledger-{suffix}"
        artifact = self.root / f"result-{suffix}.txt"
        artifact.write_text("verified execution output\n", encoding="utf-8")
        artifact_item = {
            "path": str(artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "size": artifact.stat().st_size,
            "role": "task_deliverable",
            "task_id": task["task_id"],
            "task_revision": task["revision"],
            "task_digest": task["task_digest"],
        }
        manifest_digest = harness.artifact_manifest_digest([artifact_item])
        ledger = self.root / "ledger.jsonl"
        ledger.write_text(
            json.dumps(
                {
                    "entry_id": entry_id,
                    "evidence_id": evidence_id,
                    "task_id": task["task_id"],
                    "task_revision": task["revision"],
                    "task_digest": task["task_digest"],
                    "kind": kind,
                    "execution_complete": True,
                    "artifact_manifest_digest": manifest_digest,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_path = self.root / f"runtime_receipt-{suffix}.json"
        scientific_path = self.root / f"scientific_receipt-{suffix}.json"
        common_receipt = {
            "status": "passed",
            "evidence_id": evidence_id,
            "task_id": task["task_id"],
            "task_revision": task["revision"],
            "task_digest": task["task_digest"],
            "artifact_manifest_digest": manifest_digest,
            "verifier_actor": "independent_verifier",
        }
        if runtime_receipt:
            self.write_signed_json(
                {**common_receipt, "kind": "runtime_verification"},
                runtime_path,
                "signature_path",
                verifier_key,
                "metagenome-wgs-verifier",
            )
        if scientific_receipt:
            self.write_signed_json(
                {**common_receipt, "kind": "scientific_verification"},
                scientific_path,
                "signature_path",
                verifier_key,
                "metagenome-wgs-verifier",
            )
        evidence = self.root / f"evidence-{suffix}.json"
        self.write_signed_json(
            {
                "schema_version": 1,
                "evidence_id": evidence_id,
                "task_id": task["task_id"],
                "task_revision": task["revision"],
                "task_digest": task["task_digest"],
                "kind": kind,
                "execution_complete": True,
                "worker_actor": "approved_worker",
                "artifacts": [artifact_item],
                "artifact_manifest_digest": manifest_digest,
                "runtime_verifier_receipt": str(runtime_path) if runtime_receipt else None,
                "scientific_verifier_receipt": str(scientific_path) if scientific_receipt else None,
                "ledger_path": str(ledger),
                "ledger_entry_id": entry_id,
            },
            evidence,
            "worker_signature_path",
            self.worker_key,
            "metagenome-wgs-worker",
        )
        return evidence

    def test_bare_pipeline_request_routes_to_development(self):
        result = self.route("메타지놈 WGS 기초 파이프라인을 최소로 구축해줘")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["task_spec"]["route"]["selected_agent"], "active_agent_development")

    def test_data_analysis_and_process_status_are_separate(self):
        data = self.route("메타지놈 WGS 결과 데이터를 비교 분석해줘")["task_spec"]
        process = self.route("메타지놈 WGS 분석 프로세스 실행 상태와 로그를 확인해줘")["task_spec"]
        self.assertEqual(data["route"]["analysis_mode"], "data_analysis")
        self.assertEqual(process["route"]["analysis_mode"], "analysis_process_status")

    def test_six_job_routes(self):
        cases = {
            "질병청에 보낼 메일만 작성해줘": "active_agent_communication",
            "파이프라인 코드를 수정해줘": "active_agent_development",
            "결과를 통계 분석해줘": "active_agent_analysis",
            "현재 상태를 읽기 전용으로 파악해줘": "active_agent_inspection",
            "검증 보고서를 문서로 만들어줘": "active_agent_document",
            "내일 일정 초안만 작성해줘": "active_agent_schedule",
        }
        self.assertEqual({self.route(text)["task_spec"]["route"]["selected_agent"] for text in cases}, set(cases.values()))

    def test_direct_dissatisfaction_invalidates_before_agents(self):
        result = self.route("왜지? 아까 내가 하네스 사용하라고 했잖아. 다시 하네스 사용해서 재개발해")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "direct_user_dissatisfaction")
        self.assertEqual(result["agents_called"], [])
        self.assertFalse(result["passive_loaded"])
        self.assertFalse(result["record_written"])
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8"))["status"], "INVALID")

    def test_quoted_complaint_does_not_invalidate(self):
        result = self.route('테스트 문장 "왜 하네스를 안 썼어"를 negative control로 분류해줘')
        self.assertTrue(result["allowed"])
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8"))["status"], "ACTIVE")

    def test_short_direct_dissatisfaction_invalidates(self):
        for text in (
            "Agent 답변은 아쉽네요",
            "답변이 마음에 들지 않습니다",
            "이 답변은 틀렸어요",
            "이 답변은 잘못됐고 설명도 없네요",
            "너 결과 별로야",
            "검증도 안 하고 Agent가 완료라고 했잖아",
            "Agent가 검증 없이 완료라고 했잖아",
        ):
            self.write_state("ACTIVE", 7)
            self.assertEqual(self.route(text)["reason"], "direct_user_dissatisfaction")

    def test_external_evaluation_and_negation_do_not_invalidate(self):
        for text in (
            "다른 Agent 답변이 잘못됐는지 분석해줘",
            "제3자의 Agent 답변이 틀렸는지 평가해줘",
            "동료가 이 답변은 틀렸다고 평가했어요",
            "이 Agent 답변은 잘못된 게 아니야",
            "Agent 결과가 엉망이 아니야",
            "이 답변이 별로라는 뜻은 아니야",
            "“너 결과 별로야”라는 문장을 분류해줘",
            "「너 결과 별로야」라는 문장을 분류해줘",
            "negative control로 너 결과 별로야를 분류해줘",
            "부정 대조 예문: 너 결과 별로야",
            "부정대조 예문: 너 결과 별로야",
        ):
            self.write_state("ACTIVE", 7)
            self.assertTrue(self.route(text)["allowed"])

    def test_correction_replaces_prior_revision(self):
        prior = self.route("메타지놈 WGS 결과를 분석해줘")["task_spec"]
        revised = self.route("그게 아니라 파이프라인 구현으로 다시 수정해", prior)["task_spec"]
        self.assertEqual(revised["revision"], prior["revision"] + 1)
        self.assertEqual(revised["message_relation"], "correction")
        self.assertTrue(revised["revision_control"]["prior_execution_cancelled"])
        self.assertTrue(revised["revision_control"]["prior_output_stale"])
        self.assertEqual(revised["route"]["selected_agent"], "active_agent_development")

    def test_addition_merges_prior_task(self):
        prior = self.route("메타지놈 WGS 파이프라인을 구현해줘")["task_spec"]
        revised = self.route("추가로 서버에는 실제 접속하지 마", prior)["task_spec"]
        self.assertEqual(revised["message_relation"], "addition")
        self.assertEqual(revised["goal"], prior["goal"])
        self.assertIn("추가로 서버에는 실제 접속하지 마", revised["constraints"])
        self.assertTrue(revised["revision_control"]["merged_addition"])

    def test_explanation_is_chat_only_and_identity_first(self):
        before = {path.relative_to(self.root) for path in self.root.rglob("*")}
        result = self.route("Kraken2가 뭐고 누가 실행하며 실제 구조가 어떻게 되는지 핵심만 설명해줘")
        after = {path.relative_to(self.root) for path in self.root.rglob("*")}
        task = result["task_spec"]
        self.assertEqual(before, after)
        self.assertEqual(task["deliverable"], "chat_explanation")
        self.assertEqual(task["record_policy"], "none")
        self.assertEqual(task["response_contract"]["order"], ["identity", "execution_actor", "actual_structure"])

    def test_server_access_explanation_does_not_authorize_connection(self):
        task = self.route("AICA 서버 접속 방법만 설명해줘. 실제 접속하지 마")["task_spec"]
        self.assertEqual(task["deliverable"], "chat_explanation")
        self.assertFalse(task["approval"]["required"])
        self.assertEqual(task["execution"]["status"], "not_started")

    def test_dispatch_loads_passive_read_only_but_does_not_claim_execution(self):
        result = harness.dispatch_text("메타지놈 WGS 파이프라인을 구현해줘", self.state_path, self.inside)
        self.assertTrue(result["allowed"])
        self.assertTrue(result["passive_context"]["loaded"])
        self.assertEqual(result["passive_context"]["status"], "loaded_read_only")
        self.assertEqual(result["agent_execution_status"], "not_started")
        self.assertIsNone(result["execution_evidence"])

    def test_candidate_and_after_hours_fall_back(self):
        self.write_state("CANDIDATE", 7)
        self.assertEqual(self.route("결과 분석해줘")["reason"], "system_not_active")
        self.write_state("ACTIVE", 7)
        outside = harness.route_text("결과 분석해줘", self.state_path, "2026-09-01T23:00:00+09:00")
        self.assertEqual(outside["reason"], "outside_user_operation_window")

    def test_pre_output_gate_allows_completed_evidence(self):
        task = self.route("결과 데이터를 분석해줘")["task_spec"]
        evidence = self.write_evidence(task)
        result = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", evidence, "분석 완료", self.inside, None
        )
        self.assertTrue(result["allowed"])

    def test_pre_output_gate_blocks_missing_evidence_and_stale_revision(self):
        task = self.route("결과 데이터를 분석해줘")["task_spec"]
        evidence = self.write_evidence(task)
        missing = harness.pre_output_gate(
            self.state_path, 7, 1, task, "not_verified", None, "분석 완료", self.inside, None
        )
        stale = harness.pre_output_gate(
            self.state_path, 7, 2, task, "completed", evidence, "분석 완료", self.inside, None
        )
        self.assertIn("completion_claim_without_completed_evidence", missing["reasons"])
        self.assertIn("task_revision_changed", stale["reasons"])

    def test_trusted_current_task_registry_blocks_old_revision_without_latest_text(self):
        old_task = self.route("결과 데이터를 분석해줘")["task_spec"]
        old_evidence = self.write_evidence(old_task)
        self.route("그게 아니라 문서 작성으로 바꿔", old_task)
        result = harness.pre_output_gate(
            self.state_path, 7, 1, old_task, "completed", old_evidence, "분석 완료", self.inside, None
        )
        self.assertIn("current_task_revision_changed", result["reasons"])

    def test_explanation_correction_also_supersedes_current_task(self):
        old_task = self.route("결과 데이터를 분석해줘")["task_spec"]
        old_evidence = self.write_evidence(old_task)
        revised = self.route("그게 아니라 구조만 설명해줘", old_task)["task_spec"]
        self.assertEqual(revised["deliverable"], "chat_explanation")
        result = harness.pre_output_gate(
            self.state_path, 7, 1, old_task, "completed", old_evidence, "분석 완료", self.inside, None
        )
        self.assertIn("current_task_revision_changed", result["reasons"])

    def test_evidence_is_bound_to_task_digest(self):
        task = self.route("결과 데이터를 분석해줘")["task_spec"]
        evidence = self.write_evidence(task)
        task["goal"] = "다른 목표"
        task["task_digest"] = harness.task_digest(task)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["current_task"] = {
            "task_id": task["task_id"], "revision": task["revision"], "task_digest": task["task_digest"]
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        result = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", evidence, "분석 완료", self.inside, None
        )
        self.assertIn("execution_evidence_task_digest_mismatch", result["reasons"])

    def test_pre_output_gate_rechecks_epoch_and_digest(self):
        task = self.route("결과 데이터를 분석해줘")["task_spec"]
        evidence = self.write_evidence(task)
        task["goal"] = "tampered"
        result = harness.pre_output_gate(
            self.state_path, 6, 1, task, "completed", evidence, "분석 완료", self.inside, None
        )
        self.assertIn("epoch_changed", result["reasons"])
        self.assertIn("task_digest_changed", result["reasons"])

    def test_pre_output_direct_dissatisfaction_invalidates(self):
        task = self.route("결과 데이터를 분석해줘")["task_spec"]
        evidence = self.write_evidence(task)
        result = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", evidence, "분석 완료", self.inside,
            "왜 Agent가 또 실행하지 않은 걸 완료라고 해?"
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "INVALID")

    def test_external_mutation_requires_separate_confirmation(self):
        task = self.route("서버에 접속해서 Docker 이미지를 빌드해")["task_spec"]
        self.assertTrue(task["approval"]["required"])
        self.assertEqual(task["approval"]["status"], "requires_separate_confirmation")

    def test_pre_output_blocks_correction_and_addition_until_rerouted(self):
        task = self.route("결과 데이터를 분석해줘")["task_spec"]
        evidence = self.write_evidence(task)
        correction = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", evidence, "분석 완료", self.inside,
            "그게 아니라 파이프라인 구현으로 다시 수정해",
        )
        addition = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", evidence, "분석 완료", self.inside,
            "추가로 서버에는 접속하지 마",
        )
        self.assertIn("task_revision_superseded_by_correction", correction["reasons"])
        self.assertIn("task_revision_superseded_by_addition", addition["reasons"])
        for latest, expected in (
            ("취소하고 문서 작성으로 바꿔", "correction"),
            ("범위를 QC 요약으로 바꿔줘", "correction"),
            ("표도 더 보태줘", "addition"),
        ):
            result = harness.pre_output_gate(
                self.state_path, 7, 1, task, "completed", evidence, "분석 완료", self.inside, latest,
            )
            self.assertIn(f"task_revision_superseded_by_{expected}", result["reasons"])

    def test_routing_artifact_and_dry_run_evidence_cannot_claim_completion(self):
        task = self.route("결과 데이터를 분석해줘")["task_spec"]
        for kind in ("routing_only", "artifact_only", "dry_run"):
            evidence = self.write_evidence(task, kind=kind)
            result = harness.pre_output_gate(
                self.state_path, 7, 1, task, "completed", evidence, "분석 완료", self.inside, None
            )
            self.assertIn("execution_evidence_kind_invalid", result["reasons"])

    def test_scientific_pass_requires_independent_scientific_evidence(self):
        task = self.route("메타지놈 WGS 데이터를 분석해줘")["task_spec"]
        weak = self.write_evidence(task, scientific_receipt=False)
        blocked = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", weak, "분석 PASS", self.inside, None
        )
        strong = self.write_evidence(task, scientific_receipt=True)
        allowed = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", strong, "분석 PASS", self.inside, None
        )
        self.assertIn("scientific_pass_without_independent_evidence", blocked["reasons"])
        self.assertTrue(allowed["allowed"])

    def test_missing_or_changed_actual_artifact_blocks_completion(self):
        task = self.route("메타지놈 WGS 데이터를 분석해줘")["task_spec"]
        evidence_path = self.write_evidence(task)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        Path(evidence["artifacts"][0]["path"]).unlink()
        result = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", evidence_path, "분석 완료", self.inside, None
        )
        self.assertIn("execution_artifact_missing", result["reasons"])

    def test_completion_word_blocks_when_status_is_not_started(self):
        task = self.route("메타지놈 WGS 데이터를 분석해줘")["task_spec"]
        for output in (
            "분석을 끝냈습니다",
            "요청한 분석을 성공적으로 수행했습니다",
            "successfully performed",
            "Analysis completed.",
            "execution succeeded",
            "has been completed",
            "I finished the analysis",
            "I have completed the task",
            "The work is complete",
        ):
            result = harness.pre_output_gate(
                self.state_path, 7, 1, task, "not_started", None, output, self.inside, None
            )
            self.assertIn("completion_claim_without_completed_evidence", result["reasons"])

    def test_verification_success_and_qc_pass_require_signed_receipts(self):
        task = self.route("메타지놈 WGS 데이터를 분석해줘")["task_spec"]
        weak = self.write_evidence(task, runtime_receipt=False, scientific_receipt=False)
        runtime = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", weak, "검증에 성공했습니다", self.inside, None
        )
        qc = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", weak, "QC PASS", self.inside, None
        )
        self.assertIn("verification_pass_without_runtime_evidence", runtime["reasons"])
        self.assertIn("scientific_pass_without_independent_evidence", qc["reasons"])

        for output in (
            "verification succeeded",
            "validation successful",
            "verified successfully",
            "확인 완료",
            "verification complete",
            "validation is complete",
        ):
            result = harness.pre_output_gate(
                self.state_path, 7, 1, task, "completed", weak, output, self.inside, None
            )
            self.assertIn("verification_pass_without_runtime_evidence", result["reasons"])
        for output in (
            "QC succeeded",
            "quality control passed",
            "biological validation succeeded",
            "분석 검증 완료",
            "QC 적합",
            "scientifically validated",
        ):
            result = harness.pre_output_gate(
                self.state_path, 7, 1, task, "completed", weak, output, self.inside, None
            )
            self.assertIn("scientific_pass_without_independent_evidence", result["reasons"])

    def test_untrusted_self_authored_verifier_receipt_is_rejected(self):
        task = self.route("메타지놈 WGS 데이터를 분석해줘")["task_spec"]
        evidence_path = self.write_evidence(task)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        receipt_path = Path(evidence["runtime_verifier_receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["verifier_actor"] = "invented_verifier"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = harness.pre_output_gate(
            self.state_path, 7, 1, task, "completed", evidence_path, "검증 PASS", self.inside, None
        )
        self.assertIn("verification_pass_without_runtime_evidence", result["reasons"])

    def test_distinct_actor_names_with_same_ssh_key_are_not_independent(self):
        original = self.allowed_signers.read_text(encoding="utf-8")
        worker_public = self.worker_key.with_suffix(".pub").read_text(encoding="utf-8").split()
        self.allowed_signers.write_text(
            f'approved_worker namespaces="metagenome-wgs-worker" {worker_public[0]} {worker_public[1]}\n'
            f'independent_verifier namespaces="metagenome-wgs-verifier" {worker_public[0]} {worker_public[1]}\n',
            encoding="utf-8",
        )
        try:
            task = self.route("메타지놈 WGS 데이터를 분석해줘")["task_spec"]
            evidence = self.write_evidence(task, verifier_key=self.worker_key)
            result = harness.pre_output_gate(
                self.state_path, 7, 1, task, "completed", evidence, "검증 PASS", self.inside, None
            )
            self.assertIn("verification_pass_without_runtime_evidence", result["reasons"])
        finally:
            self.allowed_signers.write_text(original, encoding="utf-8")

    def test_artifact_manifest_digest_is_order_independent(self):
        left = [{"path": "b", "sha256": "2"}, {"path": "a", "sha256": "1"}]
        right = list(reversed(left))
        self.assertEqual(harness.artifact_manifest_digest(left), harness.artifact_manifest_digest(right))

    def test_cli_state_route_dispatch_and_pre_output_sequence(self):
        cli = ROOT / "src" / "harness.py"

        def run(*args: str, expected_returncode: int = 0):
            completed = subprocess.run(
                [sys.executable, "-B", str(cli), "--state-path", str(self.state_path), *args],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self.assertEqual(completed.returncode, expected_returncode, completed.stderr or completed.stdout)
            return json.loads(completed.stdout)

        state = run("state")
        routed = run("route", "--text", "메타지놈 WGS 결과를 분석해줘", "--now", self.inside)
        dispatched = run("dispatch", "--text", "메타지놈 WGS 결과를 분석해줘", "--now", self.inside)
        task_path = self.root / "task.json"
        task_path.write_text(json.dumps(dispatched["task_spec"], ensure_ascii=False), encoding="utf-8")
        evidence = self.write_evidence(dispatched["task_spec"])
        gated = run(
            "pre-output-gate",
            "--expected-epoch", str(state["epoch"]),
            "--expected-task-revision", str(dispatched["task_spec"]["revision"]),
            "--current-task-spec", str(task_path),
            "--evidence-status", "completed",
            "--execution-evidence", str(evidence),
            "--output-text", "분석 완료",
            "--now", self.inside,
            expected_returncode=2,
        )
        self.assertTrue(routed["allowed"])
        self.assertEqual(dispatched["agent_execution_status"], "not_started")
        self.assertIn("execution_worker_signature_untrusted", gated["reasons"])


if __name__ == "__main__":
    unittest.main()
