#!/usr/bin/env python3
"""Read-only validator for the minimal V1 agent scaffold."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


V1_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = V1_ROOT.parents[1]

ROLE_KEYS = {
    "id",
    "purpose",
    "roles",
    "permissions",
    "paperthin_skills",
    "status",
}
RUNTIME_MEMORY_KEYS = {
    "agent_id",
    "revision",
    "fixed",
    "current",
    "references",
    "updated_at",
}
EXPECTED_SKILLS = {
    "aim",
    "autobahn",
    "catchup",
    "debloat",
    "dedash",
    "detool",
    "factchk",
    "feynman",
    "hate",
    "macrothink",
    "mandela",
    "modelchk",
    "nba",
    "prism",
    "re0",
    "re0-git",
    "re0-loop",
    "re0-memo",
    "re0-merge",
    "re0-plan",
    "re0-release",
    "re0-upgrade",
    "re0-work",
    "readchk",
    "reorder",
    "shower",
    "sip",
    "ssotize",
}
USER_ONLY_SKILLS = {
    "hate",
    "macrothink",
    "feynman",
    "reorder",
    "dedash",
    "debloat",
    "re0-git",
    "re0-release",
    "re0-merge",
    "re0-upgrade",
    "re0-plan",
    "prism",
}

EXPECTED_AGENTS: dict[str, dict[str, Any]] = {
    "0_Prompt_Agent": {
        "path": "Agent/0_Prompt_Agent",
        "kind": "prompt",
        "status": "active",
        "roles": [
            "natural_language_intake",
            "task_spec_management",
            "agent_routing",
            "result_reporting",
        ],
    },
    "1_Passive_Agent": {
        "path": "Agent/1_Passive_Agent",
        "kind": "passive",
        "status": "passive",
        "roles": [
            "passive_element.feedback",
            "passive_element.direction",
            "passive_element.user_info",
            "passive_element.dev_env",
        ],
    },
    "active_agent_communication": {
        "path": "Agent/2_Active_Agent/active_agent_communication",
        "kind": "active",
        "status": "active",
        "roles": [
            "ai_communication",
            "employee_oral_communication",
            "employee_email_communication",
        ],
    },
    "active_agent_development": {
        "path": "Agent/2_Active_Agent/active_agent_development",
        "kind": "active",
        "status": "active",
        "roles": [
            "web_development",
            "pipeline_development",
            "field_installation_operation",
        ],
    },
    "active_agent_analysis": {
        "path": "Agent/2_Active_Agent/active_agent_analysis",
        "kind": "active",
        "status": "active",
        "roles": ["data_analysis", "analysis_process_status"],
    },
    "active_agent_inspection": {
        "path": "Agent/2_Active_Agent/active_agent_inspection",
        "kind": "active",
        "status": "active",
        "roles": ["current_work_status"],
    },
    "active_agent_document": {
        "path": "Agent/2_Active_Agent/active_agent_document",
        "kind": "active",
        "status": "active",
        "roles": [
            "report_creation",
            "meeting_minutes_creation",
            "manual_creation",
            "work_record_creation",
        ],
    },
    "active_agent_schedule": {
        "path": "Agent/2_Active_Agent/active_agent_schedule",
        "kind": "active",
        "status": "deferred",
        "roles": [
            "schedule_add",
            "schedule_update",
            "schedule_delete",
            "schedule_check",
        ],
    },
    "3_Improvement_Agent": {
        "path": "Agent/3_Improvement_Agent",
        "kind": "improvement",
        "status": "conditional",
        "roles": [
            "post_task_retrospective",
            "root_cause_classification",
            "improvement_proposal",
            "supervisor_change_request",
        ],
    },
    "S_Supervisor_Agent": {
        "path": "Agent/S_Supervisor_Agent",
        "kind": "supervisor",
        "status": "disabled",
        "roles": [
            "role_registry_governance",
            "role_contract_governance",
            "role_change_validation",
            "role_change_rollback",
        ],
    },
}


class Validation:
    def __init__(self, strict_runtime: bool) -> None:
        self.strict_runtime = strict_runtime
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def require(self, condition: bool, code: str, path: Path | str, detail: str) -> None:
        if not condition:
            self.errors.append(f"{code} | {path} | {detail}")

    def runtime_missing(self, code: str, path: Path) -> None:
        message = f"{code} | {path} | local runtime file is missing"
        if self.strict_runtime:
            self.errors.append(message)
        else:
            self.warnings.append(message)


def load_json(path: Path, validation: Validation) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        validation.require(False, "FILE_MISSING", path, "required JSON file is missing")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        validation.require(False, "JSON_INVALID", path, str(exc))
    return None


def validate_string_list(
    value: Any, validation: Validation, code: str, path: Path
) -> bool:
    valid = (
        isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        and len(value) == len(set(value))
    )
    validation.require(valid, code, path, "expected a unique list of non-empty strings")
    return valid


def validate_governance(validation: Validation) -> None:
    path = V1_ROOT / "system" / "immutable_governance.json"
    data = load_json(path, validation)
    if not isinstance(data, dict):
        return

    validation.require(
        set(data) == {"version", "input_contract", "workflow", "authority", "verification", "reporting"},
        "GOVERNANCE_SCHEMA",
        path,
        "top-level fields changed",
    )
    input_contract = data.get("input_contract", {})
    validation.require(
        input_contract.get("default_input") == "natural_language",
        "INPUT_CONTRACT",
        path,
        "natural language must be the default input",
    )
    validation.require(
        input_contract.get("yaml_usage") == "internal_working_state",
        "INPUT_CONTRACT",
        path,
        "YAML must remain internal working state",
    )
    validation.require(
        input_contract.get("steering_requires_yaml") is False,
        "STEERING_CONTRACT",
        path,
        "urgent steering must not require YAML",
    )
    validation.require(
        set(input_contract.get("urgent_signals", []))
        == {"stop", "scope_change", "direction_change", "exclude_path"},
        "STEERING_CONTRACT",
        path,
        "urgent steering signal set changed",
    )
    validation.require(
        input_contract.get("preserve_unaffected_fixed_values") is True,
        "STEERING_CONTRACT",
        path,
        "unaffected fixed values must be preserved",
    )
    validation.require(
        input_contract.get("complexity_scale")
        == ["simple", "medium", "complex", "ultra"]
        and input_contract.get("input_detail_policy")
        == "increase_only_when_complexity_or_risk_requires_it"
        and input_contract.get("question_policy")
        == "ask_only_when_the_answer_changes_result_scope_authority_or_safety",
        "ADAPTIVE_INPUT_CONTRACT",
        path,
        "adaptive input detail or minimal-question policy changed",
    )

    workflow = data.get("workflow", {})
    validation.require(
        workflow.get("agent_partition") == "job_only"
        and workflow.get("stages_are_internal") is True,
        "WORKFLOW_CONTRACT",
        path,
        "stage must stay internal to job-based agents",
    )
    validation.require(
        list(workflow.get("stages", {}))
        == ["preparation", "execution", "verification", "completion"],
        "WORKFLOW_CONTRACT",
        path,
        "the four ordered stages changed",
    )

    authority = data.get("authority", {})
    validation.require(
        authority.get("role_registry_owner") == "S_Supervisor_Agent"
        and authority.get("role_change_requires_user_approval") is True
        and authority.get("supervisor_activation") == "user_request_only",
        "AUTHORITY_CONTRACT",
        path,
        "Supervisor ownership or approval gate changed",
    )
    verification = data.get("verification", {})
    validation.require(
        verification.get("required_before_completion_claim") is True
        and verification.get("separate_verifier_for_changes") is True
        and verification.get("pass_requires_evidence") is True
        and verification.get("paperthin_is_not_a_verifier") is True,
        "VERIFICATION_CONTRACT",
        path,
        "evidence or independent verification gate changed",
    )
    validation.require(
        data.get("reporting", {}).get("summary_order")
        == ["result", "meaning", "user_action"],
        "REPORTING_CONTRACT",
        path,
        "concise report order changed",
    )


def validate_registry_and_roles(validation: Validation) -> list[tuple[str, Path, dict[str, Any]]]:
    registry_path = V1_ROOT / "system" / "role_registry.json"
    registry = load_json(registry_path, validation)
    loaded_roles: list[tuple[str, Path, dict[str, Any]]] = []
    if not isinstance(registry, dict):
        return loaded_roles

    entries = registry.get("agents")
    validation.require(isinstance(entries, list), "REGISTRY_SCHEMA", registry_path, "agents must be a list")
    if not isinstance(entries, list):
        return loaded_roles

    ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    validation.require(len(entries) == 10, "AGENT_COUNT", registry_path, "expected exactly 10 leaf agents")
    validation.require(
        len(ids) == len(set(ids)) and set(ids) == set(EXPECTED_AGENTS),
        "AGENT_INVENTORY",
        registry_path,
        "registry IDs do not match the canonical inventory",
    )
    validation.require(
        registry.get("active_group") == {"path": "Agent/2_Active_Agent", "is_agent": False},
        "ACTIVE_GROUP",
        registry_path,
        "2_Active_Agent must remain a non-agent container",
    )
    validation.require(
        registry.get("active_job_count") == 6,
        "ACTIVE_COUNT",
        registry_path,
        "expected exactly six Active jobs",
    )
    validation.require(
        registry.get("role_registry_owner") == "S_Supervisor_Agent"
        and registry.get("user_approval_required") is True,
        "REGISTRY_AUTHORITY",
        registry_path,
        "registry ownership or user approval requirement changed",
    )
    validation.require(
        not (V1_ROOT / "Agent" / "2_Active_Agent" / "role" / "role.json").exists(),
        "ACTIVE_GROUP",
        V1_ROOT / "Agent" / "2_Active_Agent",
        "container must not have its own role.json",
    )

    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") not in EXPECTED_AGENTS:
            continue
        agent_id = entry["id"]
        expected = EXPECTED_AGENTS[agent_id]
        validation.require(
            entry.get("path") == expected["path"]
            and entry.get("kind") == expected["kind"]
            and entry.get("status") == expected["status"],
            "REGISTRY_ENTRY",
            registry_path,
            f"canonical entry changed for {agent_id}",
        )
        agent_path = V1_ROOT / expected["path"]
        role_path = agent_path / "role" / "role.json"
        role = load_json(role_path, validation)
        if not isinstance(role, dict):
            continue
        loaded_roles.append((agent_id, agent_path, role))

        validation.require(set(role) == ROLE_KEYS, "ROLE_SCHEMA", role_path, "expected exactly six top-level fields")
        validation.require(role.get("id") == agent_id, "ROLE_ID", role_path, "role ID does not match registry")
        validation.require(
            isinstance(role.get("purpose"), str) and bool(role["purpose"].strip()),
            "ROLE_PURPOSE",
            role_path,
            "purpose must be a non-empty string",
        )
        if validate_string_list(role.get("roles"), validation, "ROLE_LIST", role_path):
            validation.require(
                role["roles"] == expected["roles"],
                "ROLE_INVENTORY",
                role_path,
                "canonical role list changed",
            )
        permissions = role.get("permissions")
        validation.require(
            isinstance(permissions, dict) and set(permissions) == {"allowed", "prohibited"},
            "PERMISSION_SCHEMA",
            role_path,
            "permissions must contain only allowed and prohibited",
        )
        if isinstance(permissions, dict):
            allowed_ok = validate_string_list(permissions.get("allowed"), validation, "PERMISSION_LIST", role_path)
            prohibited_ok = validate_string_list(permissions.get("prohibited"), validation, "PERMISSION_LIST", role_path)
            if allowed_ok and prohibited_ok:
                validation.require(
                    not (set(permissions["allowed"]) & set(permissions["prohibited"])),
                    "PERMISSION_CONFLICT",
                    role_path,
                    "the same permission is both allowed and prohibited",
                )
        if validate_string_list(role.get("paperthin_skills"), validation, "SKILL_REFERENCE", role_path):
            validation.require(
                set(role["paperthin_skills"]) <= EXPECTED_SKILLS,
                "SKILL_REFERENCE",
                role_path,
                "role references a skill outside the pinned catalog",
            )
        validation.require(
            role.get("status") == expected["status"],
            "ROLE_STATUS",
            role_path,
            "role status does not match registry",
        )

        for required_dir in (agent_path / "memory", agent_path / "record"):
            validation.require(required_dir.is_dir(), "RUNTIME_DIR", required_dir, "required directory is missing")
            validation.require((required_dir / ".gitkeep").is_file(), "RUNTIME_DIR", required_dir, ".gitkeep is missing")

        memory_path = agent_path / "memory" / "current.json"
        if memory_path.exists():
            memory = load_json(memory_path, validation)
            if isinstance(memory, dict):
                validation.require(
                    set(memory) == RUNTIME_MEMORY_KEYS and memory.get("agent_id") == agent_id,
                    "RUNTIME_MEMORY",
                    memory_path,
                    "runtime memory schema or agent_id is invalid",
                )
        else:
            validation.runtime_missing("RUNTIME_MEMORY", memory_path)

        history_path = agent_path / "record" / "history.jsonl"
        if history_path.exists():
            try:
                for line_number, line in enumerate(history_path.read_text(encoding="utf-8").splitlines(), 1):
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    validation.require(
                        isinstance(item, dict),
                        "RUNTIME_RECORD",
                        history_path,
                        f"line {line_number} must be a JSON object",
                    )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                validation.require(False, "RUNTIME_RECORD", history_path, str(exc))
        else:
            validation.runtime_missing("RUNTIME_RECORD", history_path)

    active_ids = [
        agent_id
        for agent_id, expected in EXPECTED_AGENTS.items()
        if expected["kind"] == "active"
    ]
    validation.require(len(active_ids) == 6, "ACTIVE_COUNT", registry_path, "validator inventory is not six")
    return loaded_roles


def validate_role_authority(
    validation: Validation, loaded_roles: list[tuple[str, Path, dict[str, Any]]]
) -> None:
    mutators: list[str] = []
    for agent_id, agent_path, role in loaded_roles:
        allowed = role.get("permissions", {}).get("allowed", [])
        if any(
            permission.startswith("modify_role_registry")
            or permission.startswith("modify_role_definitions")
            for permission in allowed
        ):
            mutators.append(agent_id)
        if agent_id != "S_Supervisor_Agent":
            prohibited = set(role.get("permissions", {}).get("prohibited", []))
            validation.require(
                {"modify_role_registry", "modify_role_definitions"} <= prohibited,
                "ROLE_READ_ONLY",
                agent_path / "role" / "role.json",
                "non-Supervisor must explicitly prohibit role changes",
            )

    validation.require(
        mutators == ["S_Supervisor_Agent"],
        "SOLE_ROLE_MUTATOR",
        V1_ROOT / "Agent",
        f"expected only Supervisor, found {mutators}",
    )

    roles_by_id = {agent_id: role for agent_id, _, role in loaded_roles}
    supervisor_prohibited = set(
        roles_by_id.get("S_Supervisor_Agent", {}).get("permissions", {}).get("prohibited", [])
    )
    validation.require(
        {
            "modify_immutable_governance",
            "expand_own_authority",
            "apply_change_without_user_approval",
            "alter_task_evidence",
        }
        <= supervisor_prohibited,
        "SUPERVISOR_BOUNDARY",
        V1_ROOT / "Agent" / "S_Supervisor_Agent" / "role" / "role.json",
        "Supervisor boundary is incomplete",
    )
    schedule_prohibited = set(
        roles_by_id.get("active_agent_schedule", {}).get("permissions", {}).get("prohibited", [])
    )
    validation.require(
        {"mutate_external_calendar", "claim_schedule_applied"} <= schedule_prohibited,
        "SCHEDULE_BOUNDARY",
        V1_ROOT / "Agent" / "2_Active_Agent" / "active_agent_schedule" / "role" / "role.json",
        "deferred schedule agent must not mutate a calendar or claim success",
    )


def skill_is_user_only(skill_file: Path, validation: Validation) -> bool | None:
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        validation.require(False, "PAPERTHIN_FRONTMATTER", skill_file, str(exc))
        return None
    if not lines or lines[0].strip() != "---":
        validation.require(False, "PAPERTHIN_FRONTMATTER", skill_file, "opening frontmatter delimiter is missing")
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return any(
                item.strip() == "disable-model-invocation: true"
                for item in lines[1 : lines.index(line, 1) + 1]
            )
    validation.require(False, "PAPERTHIN_FRONTMATTER", skill_file, "closing frontmatter delimiter is missing")
    return None


def validate_function_router(
    validation: Validation, loaded_roles: list[tuple[str, Path, dict[str, Any]]]
) -> None:
    path = V1_ROOT / "system" / "function_router.json"
    router = load_json(path, validation)
    if not isinstance(router, dict):
        return
    validation.require(
        set(router)
        == {
            "version",
            "catalog_source",
            "selection_contract",
            "invocation_modes",
            "guard_levels",
            "skills",
        },
        "FUNCTION_ROUTER_SCHEMA",
        path,
        "expected exactly six top-level fields",
    )
    source = router.get("catalog_source", {})
    validation.require(
        source.get("repository") == "https://github.com/rlagksqls17/paperthin"
        and source.get("commit") == "3bca079a51bcfff5dafb53d1d7f9f523d66ee317"
        and source.get("installed_skill_count") == 28,
        "FUNCTION_ROUTER_SOURCE",
        path,
        "catalog source or count changed",
    )
    selection = router.get("selection_contract", {})
    validation.require(
        selection.get("user_only_requires_explicit_skill_name") is True
        and selection.get("skills_cannot_chain_user_only_skills") is True
        and selection.get("model_invoked_dependency_may_route_to_another_eligible_agent") is True
        and selection.get("dependency_result_returns_to_calling_agent") is True
        and selection.get("role_and_governance_override_skill_text") is True
        and selection.get("no_matching_skill_means_normal_agent_work") is True,
        "FUNCTION_SELECTION_CONTRACT",
        path,
        "user control or permission precedence changed",
    )

    entries = router.get("skills")
    validation.require(isinstance(entries, list), "FUNCTION_ROUTER_SCHEMA", path, "skills must be a list")
    if not isinstance(entries, list):
        return
    validation.require(len(entries) == 28, "FUNCTION_COUNT", path, "expected 28 Paperthin functions")

    known_agents = set(EXPECTED_AGENTS)
    seen: list[str] = []
    mapped_by_agent: dict[str, set[str]] = {agent_id: set() for agent_id in known_agents}
    entries_by_skill: dict[str, dict[str, Any]] = {}
    invocation_by_skill: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            validation.require(False, "FUNCTION_ENTRY", path, "each function route must be an object")
            continue
        validation.require(
            set(entry) == {"skill", "category", "primary_agent", "eligible_agents", "invocation", "route"},
            "FUNCTION_ENTRY",
            path,
            "each function route must have exactly six fields",
        )
        skill = entry.get("skill")
        if not isinstance(skill, str):
            validation.require(False, "FUNCTION_ENTRY", path, "skill name must be a string")
            continue
        seen.append(skill)
        entries_by_skill[skill] = entry
        validation.require(skill in EXPECTED_SKILLS, "FUNCTION_INVENTORY", path, f"unknown skill: {skill}")
        validation.require(
            entry.get("category") in {"depth", "breadth", "coil", "mesh"},
            "FUNCTION_CATEGORY",
            path,
            f"invalid category for {skill}",
        )
        eligible = entry.get("eligible_agents")
        eligible_ok = validate_string_list(eligible, validation, "FUNCTION_AGENTS", path)
        if eligible_ok:
            validation.require(
                set(eligible) <= known_agents,
                "FUNCTION_AGENTS",
                path,
                f"unknown eligible Agent for {skill}",
            )
            validation.require(
                entry.get("primary_agent") in eligible,
                "FUNCTION_PRIMARY",
                path,
                f"primary Agent must be eligible for {skill}",
            )
            for agent_id in eligible:
                if agent_id in mapped_by_agent:
                    mapped_by_agent[agent_id].add(skill)
        invocation = entry.get("invocation")
        invocation_by_skill[skill] = invocation
        validation.require(
            invocation in {"model_or_user", "user_only"},
            "FUNCTION_INVOCATION",
            path,
            f"invalid invocation mode for {skill}",
        )
        route = entry.get("route")
        validation.require(
            isinstance(route, dict)
            and set(route) == {"when", "guards"}
            and isinstance(route.get("when"), str)
            and bool(route["when"].strip())
            and validate_string_list(route.get("guards"), validation, "FUNCTION_GUARDS", path),
            "FUNCTION_ROUTE",
            path,
            f"when/guards are invalid for {skill}",
        )

    validation.require(
        len(seen) == len(set(seen)) and set(seen) == EXPECTED_SKILLS,
        "FUNCTION_INVENTORY",
        path,
        "router must contain every Paperthin function exactly once",
    )
    routed_user_only = {
        skill for skill, invocation in invocation_by_skill.items() if invocation == "user_only"
    }
    validation.require(
        routed_user_only == USER_ONLY_SKILLS,
        "FUNCTION_INVOCATION",
        path,
        "user-only function set differs from the pinned Paperthin frontmatter",
    )

    skills_root = V1_ROOT / ".agents" / "skills"
    copied_user_only: set[str] = set()
    for skill in EXPECTED_SKILLS:
        marker = skill_is_user_only(skills_root / skill / "SKILL.md", validation)
        if marker:
            copied_user_only.add(skill)
    validation.require(
        copied_user_only == USER_ONLY_SKILLS,
        "FUNCTION_INVOCATION",
        skills_root,
        "vendored frontmatter user-only set changed",
    )

    roles_by_id = {agent_id: role for agent_id, _, role in loaded_roles}
    for agent_id in known_agents:
        declared = roles_by_id.get(agent_id, {}).get("paperthin_skills", [])
        validation.require(
            isinstance(declared, list) and declared == sorted(mapped_by_agent[agent_id]),
            "FUNCTION_AGENT_MAPPING",
            V1_ROOT / EXPECTED_AGENTS[agent_id]["path"] / "role" / "role.json",
            "role skill list must exactly match function_router eligibility",
        )

    for skill in ("re0-git", "re0-release", "re0-merge"):
        guards = set(entries_by_skill.get(skill, {}).get("route", {}).get("guards", []))
        validation.require(
            "high_impact_confirmation" in guards,
            "HIGH_IMPACT_GUARD",
            path,
            f"{skill} lacks a high-impact confirmation gate",
        )
    validation.require(
        "approval_before_mutation"
        in set(entries_by_skill.get("re0-work", {}).get("route", {}).get("guards", [])),
        "RESTART_GUARD",
        path,
        "re0-work must require approval before restart",
    )
    validation.require(
        "user_only skill 연쇄 호출 금지"
        in set(entries_by_skill.get("sip", {}).get("route", {}).get("guards", [])),
        "SKILL_CHAIN_GUARD",
        path,
        "sip must not chain user-only functions",
    )
    for skill in ("autobahn", "feynman", "macrothink", "prism", "shower"):
        guards = set(entries_by_skill.get(skill, {}).get("route", {}).get("guards", []))
        validation.require(
            "fresh_context_required" in guards,
            "FRESH_CONTEXT_GUARD",
            path,
            f"{skill} requires a fresh context Agent",
        )


def validate_paperthin(
    validation: Validation, loaded_roles: list[tuple[str, Path, dict[str, Any]]]
) -> None:
    skills_root = V1_ROOT / ".agents" / "skills"
    installed = {path.name for path in skills_root.iterdir() if path.is_dir()} if skills_root.is_dir() else set()
    validation.require(
        installed == EXPECTED_SKILLS,
        "PAPERTHIN_SCOPE",
        skills_root,
        f"expected exactly {sorted(EXPECTED_SKILLS)}, found {sorted(installed)}",
    )

    referenced: set[str] = set()
    for _, _, role in loaded_roles:
        skills = role.get("paperthin_skills", [])
        if isinstance(skills, list):
            referenced.update(item for item in skills if isinstance(item, str))
    validation.require(
        referenced == EXPECTED_SKILLS,
        "PAPERTHIN_MAPPING",
        V1_ROOT / "Agent",
        "role mappings must cover exactly the installed skills",
    )

    lock_path = V1_ROOT / "third_party" / "paperthin" / "paperthin.lock.json"
    lock = load_json(lock_path, validation)
    if isinstance(lock, dict):
        validation.require(
            lock.get("source") == "https://github.com/rlagksqls17/paperthin"
            and lock.get("commit") == "3bca079a51bcfff5dafb53d1d7f9f523d66ee317"
            and lock.get("paperthin_version") == "0.17.4"
            and lock.get("install_mode") == "copy",
            "PAPERTHIN_LOCK",
            lock_path,
            "source, commit, version, or install mode changed",
        )
        validation.require(
            set(lock.get("selected_skills", [])) == EXPECTED_SKILLS,
            "PAPERTHIN_LOCK",
            lock_path,
            "selected skill list changed",
        )
        hashes = lock.get("skill_sha256", {})
        validation.require(
            isinstance(hashes, dict) and set(hashes) == EXPECTED_SKILLS,
            "PAPERTHIN_HASH",
            lock_path,
            "hash inventory does not match selected skills",
        )
        for skill in installed:
            skill_file = skills_root / skill / "SKILL.md"
            validation.require(skill_file.is_file(), "PAPERTHIN_FILE", skill_file, "SKILL.md is missing")
            if not skill_file.is_file():
                continue
            content = skill_file.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            validation.require(
                isinstance(hashes, dict) and hashes.get(skill) == digest,
                "PAPERTHIN_HASH",
                skill_file,
                "SKILL.md differs from the pinned copy",
            )
            text = content.decode("utf-8")
            validation.require(
                re.search(rf"^name:\s*{re.escape(skill)}\s*$", text, flags=re.MULTILINE) is not None,
                "PAPERTHIN_FRONTMATTER",
                skill_file,
                "skill name does not match its directory",
            )

    third_party = V1_ROOT / "third_party" / "paperthin"
    for filename in ("LICENSE", "NOTICE"):
        path = third_party / filename
        validation.require(path.is_file() and path.stat().st_size > 0, "PAPERTHIN_LICENSE", path, "required attribution is missing")


def validate_runtime_task(validation: Validation) -> None:
    path = V1_ROOT / "system" / "current_task.yaml"
    if not path.exists():
        validation.runtime_missing("CURRENT_TASK", path)
        return
    try:
        top_level = {
            line.split(":", 1)[0]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line[0].isspace() and not line.startswith("#") and ":" in line
        }
    except UnicodeDecodeError as exc:
        validation.require(False, "CURRENT_TASK", path, str(exc))
        return
    validation.require(
        top_level == {"task_id", "revision", "objective", "result", "constraints", "workflow"},
        "CURRENT_TASK",
        path,
        "expected exactly six top-level YAML fields",
    )


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def validate_git_exclusions(validation: Validation) -> None:
    if run_git(["rev-parse", "--is-inside-work-tree"]).returncode != 0:
        validation.warnings.append("GIT_UNAVAILABLE | repository | Git exclusion checks skipped")
        return

    ignored_paths = [
        "reference/private-data.zip",
        "Project1/v1/system/current_task.yaml",
        "Project1/v1/Agent/0_Prompt_Agent/memory/current.json",
        "Project1/v1/Agent/0_Prompt_Agent/record/history.jsonl",
        ".env",
    ]
    for relative_path in ignored_paths:
        result = run_git(["check-ignore", "-q", "--no-index", "--", relative_path])
        validation.require(
            result.returncode == 0,
            "GIT_IGNORE",
            REPO_ROOT / relative_path,
            "path is not ignored",
        )

    attribute_result = run_git(
        ["check-attr", "eol", "--", "Project1/v1/.agents/skills/readchk/SKILL.md"]
    )
    validation.require(
        attribute_result.returncode == 0 and "eol: lf" in attribute_result.stdout,
        "GIT_EOL",
        REPO_ROOT / ".gitattributes",
        "vendored Paperthin hashes require LF checkout",
    )

    tracked_result = run_git(["ls-files", "-z"])
    validation.require(
        tracked_result.returncode == 0,
        "GIT_TRACKING",
        REPO_ROOT,
        tracked_result.stderr.strip() or "git ls-files failed",
    )
    if tracked_result.returncode != 0:
        return
    tracked = [path for path in tracked_result.stdout.split("\0") if path]
    forbidden = [
        path
        for path in tracked
        if path.startswith("reference/")
        or path.endswith("/memory/current.json")
        or path.endswith("/record/history.jsonl")
        or path == "Project1/v1/system/current_task.yaml"
        or path == ".env"
        or path.startswith(".env.")
    ]
    validation.require(
        not forbidden,
        "GIT_TRACKING",
        REPO_ROOT,
        f"private or runtime files are tracked: {forbidden}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-runtime",
        action="store_true",
        help="Fail when local current_task, memory, or history files are absent.",
    )
    args = parser.parse_args()
    validation = Validation(strict_runtime=args.strict_runtime)

    validate_governance(validation)
    loaded_roles = validate_registry_and_roles(validation)
    validate_role_authority(validation, loaded_roles)
    validate_function_router(validation, loaded_roles)
    validate_paperthin(validation, loaded_roles)
    validate_runtime_task(validation)
    validate_git_exclusions(validation)

    for warning in validation.warnings:
        print(f"[WARN] {warning}")
    if validation.errors:
        for error in validation.errors:
            print(f"[FAIL] {error}")
        print("STATIC_SCAFFOLD_FAIL")
        return 1

    print("[PASS] agents: 10")
    print("[PASS] active_agents: 6")
    print("[PASS] role_schema: 10/10")
    print("[PASS] sole_role_mutator: S_Supervisor_Agent")
    print("[PASS] schedule: deferred")
    print("[PASS] paperthin_skills: 28")
    print("[PASS] function_routes: 28")
    print("[PASS] git_exclusions")
    print("[PASS] natural_language_contract")
    print("[INFO] standalone_runtime_engine: NOT_IMPLEMENTED (Codex reads the contracts directly)")
    print("STATIC_SCAFFOLD_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
