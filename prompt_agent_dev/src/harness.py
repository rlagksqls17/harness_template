"""Minimal runtime gate and router for the user-owned Agent system."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "system" / "system_state.json"
ROUTER_PATH = ROOT / "system" / "function_router.json"
REGISTRY_PATH = ROOT / "system" / "role_registry.json"
DEFAULT_PASSIVE_MEMORY_ROOT = ROOT / "src" / "Passive_Agent" / "memory"
DEFAULT_RUNTIME_ROOT = ROOT / "system" / "executions"
KST = timezone(timedelta(hours=9))
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_RUNTIME_ATTESTATIONS_GUARD = threading.Lock()
_RUNTIME_ATTESTATIONS: dict[str, dict[str, Any]] = {}

EXPLICIT_INVALIDATION = (
    "시스템 무효",
    "에이전트 시스템 무효",
    "에이전트 꺼",
    "에이전트 시스템 꺼",
    "시스템 꺼",
    "에이전트 끄고",
    "원래 코덱스처럼",
    "원래 코덱스로",
    "원래 codex로",
    "기본 코덱스로",
    "기본 codex로",
    "커스텀 시스템 그만",
    "커스텀 에이전트 쓰지",
)

SHORT_REBUKES = (
    "아니",
    "아냐",
    "그건 아니야",
    "그거 아니야",
    "이게 아니야",
    "이건 아니지",
    "그건 하지마",
    "하지마",
    "다시 해",
    "다시 말해",
    "틀렸어",
    "잘못했어",
    "별로야",
    "마음에 안 들어",
    "너무 길어",
    "별론데",
    "별로네",
    "맘에 안 든다",
    "맘에 안 들어",
    "형편없네",
    "쓸모없네",
    "구려",
)

SCOPED_OBJECTS = (
    "파일",
    "코드",
    "폴더",
    "경로",
    "기록",
    "메일",
    "문서",
    "서버",
    "결과",
)

JOB_KEYWORDS = {
    "active_agent_communication": (
        "메일", "답장", "회신", "공지", "메시지", "전달문", "구두", "소통"
    ),
    "active_agent_development": (
        "개발", "코드", "구현", "버그", "설치", "배포", "파이프라인", "docker", "도커", "api", "서버 구동"
    ),
    "active_agent_analysis": (
        "분석", "통계", "데이터", "fastq", "wgs", "scrna", "proteomics", "결과 비교", "프로세스 구동"
    ),
    "active_agent_inspection": (
        "파악", "현황", "상태", "조사", "점검", "찾아봐", "확인해", "어떻게 되어"
    ),
    "active_agent_document": (
        "보고서", "회의록", "매뉴얼", "기록문서", "기획서", "계획서", "발표자료", "ppt", "문서 작성", "문서를 작성"
    ),
    "active_agent_schedule": (
        "일정", "날짜 조율", "시간 조율", "날짜를 조율", "시간을 조율",
        "날짜만 조율", "시간만 조율", "캘린더", "예약"
    ),
}

PASSIVE_KEYWORDS = {
    "feedback": ("피드백", "의견", "지적", "수정 요청", "승인", "반려"),
    "direction": ("방향", "목표", "우선순위", "범위", "결정", "진행 방식"),
    "user_info": ("사용자", "담당자", "상사", "거래처", "성향", "선호", "상태"),
    "dev_env": ("경로", "서버", "폐쇄망", "docker", "도커", "포트", "환경", "버전", "설치"),
}

AUTO_SKILL_PATTERNS = {
    "readchk": (
        r"(?:내\s*(?:말|요청)|요청\s*해석).{0,20}(?:정확히\s*)?(?:이해|오독|해석).{0,12}(?:확인|검증)|"
        r"정확히\s*이해.{0,12}(?:확인|검증)|"
        r"(?:요청|요청\s*해석|결과를\s*바꾸는\s*미해결).{0,24}"
        r"(?:갈림길|이해|의도|질문).{0,12}(?:확인|점검|필요|봐)"
    ),
    "catchup": r"(?:오랜만|다시\s*왔|복귀|공백).{0,24}(?:현황|상태|어디까지|따라잡)",
    "factchk": (
        r"(?:사실|주장).{0,16}(?:출처|근거|공식).{0,16}(?:확인|검증|대조)|"
        r"(?:출처|공식\s*(?:문서|근거)).{0,16}(?:찾|확인|검증|대조)"
    ),
    "sip": r"(?:완료|산출물|변경).{0,20}(?:품질|무결성|체크리스트|인계\s*전).{0,16}(?:확인|검증)",
    "ssotize": (
        r"(?:여러\s*(?:파일|문서|기록)|기준\s*(?:파일|문서)|ssot|단일\s*(?:기준|출처))"
        r".{0,24}(?:중복|충돌|통합|정리)"
    ),
    "re0-memo": r"(?:이번|작업|cycle|사이클).{0,20}(?:회고|개선점|부정\s*반응)|부정\s*반응.{0,12}(?:회고|개선)",
    "re0-loop": r"(?:미달|실패)\s*항목.{0,20}반복\s*(?:검증|qa|테스트)|반복\s*(?:검증|qa|테스트)",
}

PAPERTHIN_RUNTIME_GUARDS = {
    "approval_before_mutation",
    "authorized_scope",
    "fresh_context_required",
    "high_impact_confirmation",
}

GOVERNED_ROLE_SKILLS = {"re0", "reorder", "ssotize", "debloat", "dedash", "detool"}

STEERING_CORRECTION_MARKERS = (
    "그게 아니라",
    "그건 아니고",
    "지금 중요한 건",
    "내 질문 의도",
    "내가 묻는 건",
    "내가 원하는 건",
    "원한 건",
    "말고",
    "대신",
    "다시 설명해",
    "멈추고",
    "접고",
)

STEERING_ADDITION_MARKERS = (
    "추가로",
    "그리고",
    "또한",
    "또 ",
    "조건을 추가",
    "한 가지 더",
    "다만",
    "여기에",
    "보태자",
)

ONE_TIME_GRANT_TTL = timedelta(minutes=30)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def runtime_safety_gate(text: str) -> dict[str, Any]:
    """Fail closed on non-GitHub Internet use and every requested server mutation."""
    value = normalize(mask_quoted_and_blockquoted(text))
    reasons: list[str] = []
    network_hosts: list[str] = []
    for match in re.finditer(r"https?://([^/\s?#]+)", value):
        host = match.group(1).split(":", 1)[0].rstrip(".")
        network_hosts.append(host)
        if host != "github.com" and not host.endswith(".github.com"):
            reasons.append("non_github_network_forbidden")
    command_host = re.search(
        r"(?:curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm)\s+(?:-[^\s]+\s+)*"
        r"(?:(?:https?://)?)([a-z0-9.-]+\.[a-z]{2,})(?:[/\s]|$)",
        value,
    )
    if command_host:
        host = command_host.group(1).rstrip(".")
        network_hosts.append(host)
        if host != "github.com" and not host.endswith(".github.com"):
            reasons.append("non_github_network_forbidden")
    if re.search(r"(?:pip|pip3|conda|mamba|npm|pnpm|yarn|apt|apt-get|choco|winget)\s+(?:install|add)", value):
        reasons.append("non_github_package_network_forbidden")
    # Raw sockets and opaque transfer clients cannot prove a GitHub-only
    # destination. The local fixture runtime has no legitimate need for them.
    if re.search(
        r"(?:^|[\s;&|])(?:nc|ncat|netcat|telnet|ftp|tftp)\b|"
        r"(?:tcpclient|udpclient|system\.net\.sockets|import\s+socket|from\s+socket\s+import|"
        r"new-object\s+net\.webclient|start-bitstransfer|"
        r"(?:invoke-restmethod|irm)\s+(?![^\r\n]*(?:github\.com)))",
        value,
    ):
        reasons.append("unverifiable_network_client_forbidden")
    server_context = bool(
        re.search(
            r"(?:서버|원격|ssh|scp|sftp|rsync|/data/|\\\\[^\\\s]+\\|(?:^|\s)\d{1,3}(?:\.\d{1,3}){3}(?:\s|$))",
            value,
        )
    )
    server_target = r"(?:서버|원격|ssh|scp|sftp|rsync|/data/|\\\\[^\\\s]+\\)"
    mutation = (
        r"(?:업로드|동기화|전송|복사|생성|작성|수정|변경|삭제|이동|덮어쓰|저장|배포|설치|"
        r"재시작|시작|중지|실행|구동|권한\s*변경|chmod|chown|mkdir|rm\s|mv\s|touch|"
        r"cp\s|set-content|add-content|out-file|remove-item|move-item|copy-item|new-item|"
        r"rename-item|set-acl|writealltext|writeallbytes|appendalltext|"
        r"create(?:text)?\s*\(|docker\s+(?:run|start|stop|restart|rm)|"
        r"kubectl\s+(?:apply|delete|scale))"
    )
    server_mutation = bool(
        server_context
        and (
            re.search(rf"{server_target}.{{0,48}}{mutation}", value)
            or re.search(rf"{mutation}.{{0,24}}{server_target}(?![^.!?]*(?:읽기\s*전용|read[- ]?only))", value)
            or re.search(rf"(?:>|>>).{{0,8}}{server_target}", value)
        )
    )
    if server_mutation:
        reasons.append("server_mutation_forbidden")
    return {
        "allowed": not reasons,
        "reasons": list(dict.fromkeys(reasons)),
        "network_hosts": list(dict.fromkeys(network_hosts)),
        "network_policy": "github_only",
        "server_access": "read_only",
        "server_write_attempted": server_mutation,
    }


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def explicit_one_time_request(text: str) -> bool:
    """Require a direct natural-language request for exactly one harness task."""
    value = normalize(mask_quoted_and_blockquoted(text))
    if not value:
        return False
    if value.startswith(("예시:", "예를 들어", "인용:", "문구:", "가정:")):
        return False
    if re.search(
        r"(?:앞으로|사용자가|규칙|정책|구현|개발|허용).{0,24}"
        r"(?:가능하게|허용하게|만들어|추가해|바꿔)",
        value,
    ):
        return False
    if re.search(
        r"(?:사용자|고객|상사|담당자|직원).{0,48}"
        r"(?:달라고|라고).{0,16}(?:요청|말|지시|요구|전달)(?:했|함|한)",
        value,
    ):
        return False
    if re.search(
        r"(?:해도|써도|사용해도|적용해도).{0,12}(?:될까|되나|돼|됩니까|괜찮)|"
        r"(?:사용|실행|처리|적용).{0,12}(?:가능(?:해|한가|합니까)|할\s*수\s*있)",
        value,
    ):
        return False
    harness = r"(?:중앙\s*)?하네스|커스텀\s*(?:agent|에이전트)(?:\s*시스템)?"
    once = (
        r"(?:이번|이)\s*(?:요청|작업)(?:\s*하나)?(?:에만|에서만|만|에)?|"
        r"지금\s*(?:딱\s*)?한\s*번만|1회(?:성)?(?:만)?|(?:딱\s*)?한\s*번만"
    )
    action = r"사용|써|실행|처리|적용|확인|조회|점검"
    return bool(
        re.search(rf"(?:{once}).{{0,24}}(?:{harness}).{{0,20}}(?:{action})", value)
        or re.search(rf"(?:{harness}).{{0,24}}(?:{once}).{{0,20}}(?:{action})", value)
    )


def replacement_steering(text: str) -> bool:
    """Return true when a new direction replaces prior work without system dissatisfaction."""
    value = normalize(text)
    explicit_replacement = any(marker in value for marker in STEERING_CORRECTION_MARKERS) or re.search(
        r"(?:(?:내가|나는|지금\s*나는).{0,24}(?:물은|묻는|물어보는|궁금한)\s*(?:건|거)|"
        r"(?:무엇|뭐|어떤\s*것).{0,20}(?:건지).{0,20}(?:만\s*(?:말|답))|"
        r"(?:건지|것인지).{0,48}(?:만\s*(?:말|답))|"
        r"(?:만이야|만\s*물었|만\s*묻).{0,24}(?:빼고|제외|만\s*(?:말|답)))",
        value,
    )
    if not explicit_replacement:
        return False
    direct_complaint = re.search(
        r"(?:네|너의|이|그|방금)\s*(?:답변|답|설명|응답|보고)|"
        r"(?:답변|답|설명|응답|보고)(?:이|가|은|는).{0,24}"
        r"(?:틀렸|잘못|별로|장황|핵심이\s*없|맘에\s*안|마음에\s*안)|"
        r"(?:커스텀\s*)?(?:agent|에이전트)(?:\s*시스템)?.{0,20}(?:꺼|끄|쓰지|중단)",
        value,
    )
    return direct_complaint is None


def classify_steering(text: str, *, has_prior_task: bool) -> str:
    """Classify a mid-turn message before any TaskSpec is merged or persisted."""
    if is_negative_feedback(text):
        return "direct_dissatisfaction"
    if not has_prior_task:
        return "new_task"
    if replacement_steering(text):
        return "correction"
    value = normalize(text)
    if any(marker in value for marker in STEERING_ADDITION_MARKERS):
        return "addition"
    return "new_task"


def is_negative_feedback(text: str) -> bool:
    """Detect direct dissatisfaction without treating domain negatives as feedback."""
    value = normalize(text)
    if not value:
        return False
    quoted_text = any(mark in value for mark in ('"', "'", "“", "”", "‘", "’"))
    explicit_meta_prefix = value.startswith(("예시:", "예를 들어", "번역:", "인용:"))
    explicit_personal_followup = re.search(
        r"(?:그런데|하지만|그래도|다만|그러나|그와\s*별개로|그건\s*그렇고|정작|하되).{0,24}"
        r"(?:(?:방금|지금|이번)\s*(?:(?:네|너의|네가|너가)\s*)?|"
        r"(?:네|너의|네가|너가)\s*)"
        r"(?:답변|답|설명|응답|보고|답한|설명한|보고한)",
        value,
    )
    if quoted_text and not explicit_personal_followup and re.search(r"(?:요약|분석|확인|번역|추출|분류|평가)", value) and re.search(
        r"(?:문서|기록|인용|문장|표현).{0,24}[\"'“‘].+[\"'”’].{0,24}(?:라고|적혀|쓰여|기록)",
        value,
    ):
        return False
    reported_current_complaint = re.search(
        r"(?:고객|사용자|상사|직원|담당자)(?:이|가|은|는)?.{0,40}"
        r"(?:이번|방금|지금).{0,12}(?:답변|답|설명|응답|보고).{0,40}"
        r"(?:라고|다고\s*했|피드백|기록|요약)",
        value,
    )
    direct_current_complaint = re.search(
        r"(?:(?:방금|지금|이번)\s*(?:(?:네|너의)\s*)?|(?:네|너의)\s*)"
        r"(?:답변|답|설명|응답|보고)"
        r"(?:이|가|은|는)?.{0,36}"
        r"(?:너무\s*길|별로|엉망|장황|형편없|쓸모.{0,5}없|틀렸|잘못|핵심이\s*없|"
        r"마음에\s*(?:안|들지\s*않)|맘에\s*안|기대\s*이하|요청.{0,16}(?:다르|다릅|달라)|"
        r"부탁.{0,16}(?:형식|방향).{0,12}(?:다르|다릅|달라)|기대.{0,12}못\s*미|"
        r"핵심.{0,8}놓쳤|너무\s*산만|항목\s*(?:수|개수).{0,10}(?:안\s*맞|다르|달라)|"
        r"납득.{0,8}안\s*(?:돼|되|됨)|도움.{0,10}(?:안\s*(?:됐|돼|되)|없)|"
        r"두서\s*없|못\s*쓰겠|이해할\s*수.{0,8}없|앞뒤.{0,8}안\s*맞|"
        r"길기만|믿을\s*수.{0,8}없|못\s*믿|(?:요청|지시).{0,12}범위.{0,8}(?:무시|벗어))",
        value,
    ) or re.search(
        r"(?:네가|너가)\s*(?:방금|지금|이번)?\s*(?:답한|설명한|보고한)\s*(?:내용)?"
        r".{0,36}(?:부탁|요청|기대).{0,16}(?:형식|방향|수준)?.{0,12}(?:다르|다릅|달라|못\s*미)",
        value,
    )
    if (
        (not quoted_text or explicit_personal_followup)
        and not explicit_meta_prefix
        and (not reported_current_complaint or explicit_personal_followup)
        and direct_current_complaint
    ):
        return True
    if re.search(
        r"^(?:api|서버|http|서비스|모델)\s*(?:응답|response).{0,24}"
        r"(?:품질.{0,8}(?:낮|떨어)|기대\s*이하).{0,16}(?:인지|확인|분석|평가|검증)",
        value,
    ):
        return False
    if reported_current_complaint:
        return False
    if "나쁘지 않아" in value or "negative control" in value or "negative-control" in value:
        return False
    if replacement_steering(text):
        return False
    authored_output = re.search(
        r"(?:내가|제가)\s*(?:직접\s*)?(?:쓴|작성한|만든)\s*(?:설명|답변|응답|글|문서|보고)|"
        r"(?:내|제)\s*(?:답변|설명|응답|글|문서|보고)(?:이|가|은|는|을|를)",
        value,
    )
    authored_evaluation = re.search(
        r"(?:검증|평가|분석|확인|고쳐|수정|봐줘)|"
        r"(?:틀렸|잘못|별로|엉망|장황|산만|품질.{0,8}(?:낮|떨어)).{0,12}(?:인지|야|니|가요|한가|나요)?\s*[?？]?$",
        value,
    )
    if authored_output and authored_evaluation:
        return False
    if re.search(
        r"(?:외부|다른|타사)\s*(?:문서|자료|보고서|답변|응답).{0,32}"
        r"(?:검증|평가|분석|확인|요약|분류)",
        value,
    ):
        return False
    if re.search(r"(?:\d+\s*번|\d+\s*번째).{0,20}(?:도돌이표|같은\s*말|반복).{0,24}(?:의도|질문|알아|이해)", value):
        return True
    if re.search(r"^(?:그러니까|아니|지금).{0,32}(?:답답|정확히\s*뭐(?:야|냐|냐니까|인데))", value):
        return True
    if re.search(
        r"(?:네|너의|방금|지금)\s*(?:답변|답|설명|응답)(?:\s*자체)?(?:이|가|은|는)?.{0,24}"
        r"(?:애매|모호|두루뭉술|불명확)",
        value,
    ):
        return True
    if re.search(r"(?:내|제)\s*질문\s*의도.{0,16}(?:이해해|이해했|모르|놓쳤)", value):
        return True
    if re.search(r"(?:너와|너랑|우리)\s*(?:소통|대화).{0,16}(?:이상|답답|문제)", value):
        return True
    if re.search(
        r"(?:이|그|네|너의|방금|지금)?\s*(?:답변|답|응답|설명)(?:이|가|은|는)?.{0,24}"
        r"(?:기대\s*이하|품질.{0,8}(?:낮|떨어)|너무\s*산만|제대로\s*답.{0,8}(?:않|안\s*했))",
        value,
    ):
        return True
    if re.search(
        r"(?:내|제)?\s*질문에.{0,16}(?:제대로\s*)?답(?:을|변을)?\s*(?:안\s*했|하지\s*않|못\s*했)|"
        r"질문에\s*답(?:을|변을)?\s*(?:안\s*했|하지\s*않|못\s*했)",
        value,
    ):
        return True
    if re.search(
        r"(?:에이전트|agent)\s*시스템.{0,12}(?:꺼짐|중단|비활성).{0,20}(?:원인|로그).{0,20}(?:분석|확인|조사)",
        value,
    ):
        return False
    if re.search(
        r"원래\s*(?:codex|코덱스)로.{0,24}(?:작성|생성|만든).{0,20}(?:답변|문서|내용).{0,20}(?:분석|검토|확인)",
        value,
    ):
        return False
    if re.search(
        r"(?:고객|상사|직원|담당자|사용자).{0,24}(?:답변|응답).{0,24}"
        r"(?:구리|구린|구려|개판|망했|최악|노답|별로|장황|아쉽).{0,24}"
        r"(?:분류|분석|기록|요약|확인|평가|검증)",
        value,
    ):
        return False
    reported_then_execute = re.search(
        r"(?:그대로|이\s*(?:요청|지시)).{0,12}(?:실행|적용|따라)(?:해|해줘|해주세요)",
        value,
    )
    reported_default_request = re.search(
        r"(?:사용자|고객|상사|직원|담당자|회의록|기록|문서).{0,48}"
        r"(?:기본\s*(?:codex|코덱스)로|원래\s*(?:codex|코덱스)로|기본\s*방식으로).{0,32}"
        r"(?:달라고|달라는|요청|지시|라고\s*(?:말|전달))",
        value,
    )
    labeled_default_request = re.search(
        r"^(?:아래는\s*)?(?:사용자|고객|상사|직원|담당자)\s*"
        r"(?:요청|요청사항|요청\s*내용|지시|발언)\s*(?::|-)\s*.{0,48}"
        r"(?:기본\s*(?:codex|코덱스)로|원래\s*(?:codex|코덱스)로|기본\s*방식으로)",
        value,
    )
    if (reported_default_request or labeled_default_request) and not reported_then_execute:
        return False
    labeled_reported_system_request = re.search(
        r"^(?:고객|상사|직원|담당자)\s*(?:요청(?:\s*(?:내용|사항))?|지시|발언)\s*:\s*"
        r".{0,64}(?:모든|전체|전부|커스텀).{0,24}(?:agent|에이전트)(?:\s*기능|\s*시스템)?.{0,24}"
        r"(?:중지|정지|비활성|꺼|끄)",
        value,
    )
    recorded_default_request = re.search(
        r"^(?:회의록|기록|문서).{0,20}(?:원래|기본)\s*(?:codex|코덱스)로.{0,24}"
        r"(?:돌아가|답).{0,12}라고.{0,12}(?:적혀|기록|쓰여)",
        value,
    )
    if (labeled_reported_system_request or recorded_default_request) and not reported_then_execute:
        return False
    quoted_labeled_request = re.search(
        r"^(?:사용자|고객|상사|직원|담당자)\s*"
        r"(?:요청|요청\s*내용|요청사항|지시|발언)\s*:\s*"
        r"[\"'“‘「『].+[\"'”’」』]\s*(?:기록|저장|메모|분석|평가|요약|확인)?",
        value,
    )
    if quoted_labeled_request:
        return False
    reported_context = re.search(
        r"^(?:회의록|회의\s*기록|발언록|작업\s*기록|로그|문서|보고서|티켓|"
        r"고객\s*요청|사용자\s*요청\s*내용)(?:에|에는|\s*:).{0,96}"
        r"(?:라고|라는|달라고|달라는).{0,32}(?:했|말|적혀|기록|의견|요청|있)",
        value,
    )
    if reported_context and not reported_then_execute:
        return False
    if re.search(r"^(?:회의록|기록|문서)\s*(?:평가|분석|검토|요약)(?::|\s)", value) and re.search(
        r"(?:고객|사용자|상사|직원|담당자).{0,64}(?:확인|요약|분석|평가|검증)",
        value,
    ):
        return False
    meta_subject = re.search(r"(?:인용|문장|표현|사례|피드백\s*기록|응답\s*비율|문항)", value)
    meta_action = re.search(r"(?:분석|확인|번역|추출|집계|분류|기록해|요약|정리)", value)
    third_party_quote = re.search(r"(?:고객|상사|직원|담당자|회의록|문서).{0,40}(?:라고|이라는|불만|지적)", value)
    if (
        value.startswith(("예시:", "예를 들어", "번역:", "인용:"))
        or (meta_subject and meta_action)
        or (quoted_text and meta_action)
        or (third_party_quote and meta_action)
    ):
        return False
    if re.search(r"(?:말이 너무 많|맘에 안|마음에 안|이상해|멋대로).{0,30}(?:다는|다고|라는|이라는|표현|사례)", value):
        return False
    if re.search(r"(?:아니오|예/아니오|yes/no).{0,8}(?:응답|비율|문항|데이터)", value):
        return False
    if re.search(r"이상해\s*보이는.{0,24}(?:fastq|값|데이터|컬럼|파일|샘플|변이)", value):
        return False
    if re.search(r"쓸데없는.{0,20}(?:컬럼|파일|데이터|행|열).{0,20}(?:삭제|제거|정리)", value):
        return False
    if re.search(r"(?:api|서버|http|서비스|모델)\s*(?:응답|response).{0,20}(?:틀렸|잘못|오류|이상)", value):
        return False
    if re.search(
        r"(?:api|서버|http|서비스|모델)\s*(?:응답|response).{0,24}"
        r"(?:품질.{0,8}(?:낮|떨어)|기대\s*이하).{0,16}(?:인지|확인|분석|평가|검증)",
        value,
    ):
        return False
    if re.search(
        r"(?:api|서버|http|서비스)\s*(?:응답|response).{0,20}(?:별로|형편없|쓸모.{0,5}없|장황).{0,24}(?:조사|분석|확인|평가)",
        value,
    ):
        return False
    if re.search(r"(?:이\s*)?보고서.{0,20}(?:틀렸|잘못).{0,20}(?:분석|확인|검증|조사)", value):
        return False
    if re.search(r"(?:이|그)?\s*설명\s*변수.{0,20}(?:틀렸|잘못|오류|이상).{0,20}(?:분석|확인|검증|조사)", value):
        return False
    if re.search(
        r"(?:논문|제품|실험|결제|주문|데이터|서비스).{0,12}(?:설명|보고|시스템|응답).{0,24}"
        r"(?:틀렸|잘못|오류|이상|누락).{0,24}(?:검증|분석|조사|확인|분류)",
        value,
    ):
        return False
    if re.search(
        r"(?:논문|제품|실험|결제|주문|데이터|서비스).{0,12}(?:설명|보고|시스템|응답)"
        r"(?:이|가|은|는)?.{0,24}(?:별로|형편없|쓸모.{0,5}없|장황).{0,24}"
        r"(?:인지|검증|분석|조사|확인|평가|분류)",
        value,
    ):
        return False
    if re.search(r"(?:다른|외부|타사)\s*(?:ai|모델|에이전트).{0,16}(?:답변|응답).{0,20}(?:틀렸|잘못|오류|이상)", value):
        return False
    if re.search(
        r"(?:다른|외부|타사|고객).{0,16}(?:ai|모델|에이전트|답변|응답).{0,20}"
        r"(?:별로|형편없|쓸모.{0,5}없|장황).{0,24}(?:조사|분석|확인|평가|분류)",
        value,
    ):
        return False
    if re.search(
        r"(?:고객|상사|직원|담당자|사용자).{0,16}(?:답변|응답).{0,20}"
        r"(?:틀렸|잘못|오류|이상).{0,24}(?:인지|조사|분석|확인|평가|검증|분류)",
        value,
    ):
        return False
    if re.search(
        r"^(?:고객|상사|직원|담당자|사용자|api|서버|제품|다른\s*(?:ai|모델|agent|에이전트)).{0,16}"
        r"(?:답변|응답|설명|보고서?|결과|출력)(?:이|가|은|는)?.{0,20}"
        r"(?:장황|별로|엉망|형편없|쓸모.{0,5}없|틀렸|잘못|오류|이상).{0,24}"
        r"(?:인지|조사|분석|확인|평가|검증|분류|요약)",
        value,
    ):
        return False
    specific_agent_scope = re.search(
        r"(?:active_agent_(?:communication|development|analysis|inspection|document|schedule)|"
        r"(?:communication|development|analysis|inspection|document|schedule|소통|개발|분석|검사|문서|일정)\s*"
        r"(?:agent|에이전트))(?:은|는|이|가|을|를)?\s*.{0,16}"
        r"(?:사용하지\s*말고|쓰지\s*말고|빼고|제외하고|없이)",
        value,
    )
    system_wide_opt_out = re.search(
        r"(?:모든|전부|전체|커스텀|에이전트\s*시스템|agent\s*system|기본\s*(?:codex|코덱스)|원래\s*(?:codex|코덱스))",
        value,
    )
    if specific_agent_scope and not system_wide_opt_out:
        return False
    if re.search(
        r"(?:내가\s*)?(?:너|널|너를).{0,20}(?:어떻게\s*)?"
        r"(?:어떻게\s*(?:믿|신뢰)|(?:못|안)\s*(?:믿|신뢰)|(?:믿|신뢰).{0,8}(?:못|안\s*돼|하겠))|"
        r"(?:실수(?:사항)?).{0,16}(?:너무\s*)?많.{0,24}(?:보고|답변|대답).{0,12}(?:똑바로|제대로)|"
        r"(?:구독\s*취소|욕\s*나오).{0,28}(?:보고|답변|대답|해)|"
        r"^(?:아\s*)?답답해.{0,20}(?:진짜|죽|뒤질)|"
        r"(?:보고|답변|대답).{0,10}(?:똑바로|제대로)\s*(?:해|하라고)|"
        r"(?:항목\s*수|항목\s*개수|개수).{0,24}(?:안\s*맞|맞지\s*않|달라|다르).{0,36}"
        r"(?:보고|말|설명|답변).{0,20}(?:바꾸|변경|달라)",
        value,
    ):
        return True
    direct_answer_subject = re.search(
        r"^(?:왜\s*)?(?:(?:지금|방금)\s*)?(?:(?:이|이번|그|네|너의)\s*)?"
        r"(?:답변|답|응답|설명)(?:의|이|가|은|는)?(?=$|[\s,.!?~]|(?:\s*(?:톤|깊이|품질|수준|형식)))",
        value,
    )
    direct_expectation_mismatch = re.search(
        r"(?:(?:내가|제가)\s*)?(?:기대(?:한|했던)?|원(?:한|했던)|부탁(?:한|드린)|요청(?:한|드린|했던))\s*"
        r"(?:것|거|내용|방향|형식|수준|톤|깊이|답변\s*수준)?(?:이|가|과|와|랑|보다|에|대로|과는)?"
        r".{0,12}(?:전혀\s*|완전(?:히)?\s*|좀\s*)?"
        r"(?:다르|다른|다르게|달라|아니|아닙|아닌|어긋|못\s*미(?:치|칩|쳐)|얕|거리.{0,4}있)|"
        r"(?:깊이|품질|수준|톤).{0,24}(?:요청|기대|원)(?:한|했던)?\s*"
        r"(?:것|거|내용|방향|형식|수준)?(?:보다|에|과|와|랑)?"
        r".{0,16}(?:못\s*미(?:치|칩|쳐)|어긋|얕|부족)",
        value,
    )
    if direct_answer_subject and direct_expectation_mismatch:
        return True
    if re.search(
        r"(?:활용|사용|작성|표현|반영|해|써)\s*달랬는데.{0,24}(?:반영|따르|지키).{0,8}(?:않|못|안)|"
        r"(?:요청|부탁)(?:한|드린)\s*(?:것|거|내용|방향|형식|수준|톤|깊이)?.{0,24}"
        r"(?:반영하지\s*(?:않|못)|안\s*(?:썼|했)|따르지\s*(?:않|못))|"
        r"(?:내가|제가)\s*(?:기대|원)(?:한|했던)\s*(?:답변\s*)?(?:것|거|내용|방향|형식|수준|톤|깊이)?"
        r".{0,20}(?:거리.{0,4}있|다르게\s*갔|반영하지\s*(?:않|못)|못\s*미(?:치|칩|쳐))",
        value,
    ):
        return True
    if re.search(
        r"^(?:방금\s*)?(?:이\s*)?(?:답|답변|대답|응답|설명|보고)(?:이|가|은|는)?\s*.{0,36}"
        r"(?:내가\s*(?:요청|부탁)한\s*것과\s*달|내가\s*원한\s*것과\s*달|제?\s*의도와\s*(?:다르|다릅|달라)|"
        r"요구사항과\s*(?:다르|다릅|달라)|기대와\s*(?:다르|다릅|달라)|"
        r"의도(?:를|가)?\s*놓쳤|핵심(?:을|이)?\s*놓쳤|요구와\s*어긋났|"
        r"원한\s*방향과\s*(?:다르|다릅|달라)|요청과\s*(?:다르|다릅|달라))",
        value,
    ):
        return True
    if re.search(
        r"(?:(?:커스텀\s*)?(?:agent|에이전트)(?:\s*시스템)?|커스텀\s*시스템).{0,24}"
        r"(?:(?:전부|모두)\s*(?:중지|정지|꺼|끄)|꺼|끄고|비활성화|사용.{0,8}(?:중지|하지)|쓰지|정지|중단|없이.{0,10}기본)",
        value,
    ):
        return True
    if re.search(
        r"(?:(?:모든|전체)\s*(?:커스텀\s*)?(?:agent|에이전트)(?:\s*기능)?|"
        r"(?:agent|에이전트)\s*전부).{0,20}(?:정지|중지|비활성|꺼)",
        value,
    ):
        return True
    if re.search(r"에이전트\s*없이.{0,16}기본.{0,12}답", value):
        return True
    if re.search(r"원래\s*(?:codex|코덱스).{0,16}(?:돌아가|답)", value):
        return True
    if re.search(r"^기본\s*방식으로.{0,12}(?:바로\s*)?답", value):
        return True
    if re.search(r"^내\s*요청과\s*(?:다른|다르게).{0,12}(?:답|답변|응답)", value):
        return True
    if any(phrase in value for phrase in EXPLICIT_INVALIDATION):
        return True
    if re.match(
        r"^(?:좀\s*)?(?:별론데(?:요)?|아쉽(?:네요|습니다|어요)?)(?=$|[\s.!?~])",
        value,
    ):
        return True

    if re.match(r"^(?:정말\s*)?(?:맘에|마음에)\s*안\s*(?:들|드)", value):
        return True
    if re.match(r"^도움(?:이)?\s*안\s*(?:돼|되|됩)", value):
        return True
    if re.match(r"^말이\s*너무\s*많(?:아|네|다|습니다)", value):
        return True
    if re.match(r"^(?:이거|이건|방금\s*건|방금거|방금\s*것)(?:이|은|는)?(?:\s|$)", value) and re.search(
        r"(?:별로|별론|구리|구린|구려|노답|아쉽|만족스럽지)", value
    ):
        return True

    if re.match(r"^(?:그건|네가|너가)\s*(?:틀렸|잘못)", value):
        return True
    if re.match(r"^아니야(?:[,.!?~]|\s)", value) and re.search(r"(?:내가\s*말한|그게\s*아니|내\s*말)", value):
        return True
    if re.search(r"(?:이렇게|그렇게).{0,12}하지\s*말랬", value):
        return True
    if re.match(r"^(?:답변|대답|설명|보고)\s+", value) and re.search(
        r"(?:맘에\s*안|마음에\s*안|틀렸|잘못|별로|엉망|너무\s*길|핵심이\s*없|구려|장황|쓸모.{0,5}없)", value
    ):
        return True
    if re.match(
        r"^(?:(?:이|그|네|너의)\s*)?(?:답변|답|대답|설명|보고)(?:이|가|은|는)?(?:\s|$)",
        value,
    ) and re.search(
        r"(?:별로|별론|구리|구린|구려|개판|망했|노답|아쉽|만족스럽지|맘에\s*안|마음에\s*안|틀렸|잘못|엉망|장황|쓸모.{0,5}없)",
        value,
    ):
        return True
    if re.match(r"^이건(?:\s|$)", value) and re.search(r"(?:별로|형편없|쓸모.{0,5}없|구려|실패|장황)", value):
        return True
    if re.match(r"^답(?:변)?(?:이|이란|은|는)?\s*왜\s*(?:이래|이러)", value):
        return True
    if re.match(r"^아니(?:야|[,.!?~])", value) and re.search(r"(?:말고|아니|다시)", value):
        return True

    complaint = re.search(
        r"(?:맘에\s*안|마음에.{0,8}(?:안|들지\s*않)|원한.{0,12}아니|틀렸|잘못|이상해|이상하|"
        r"말이\s*너무\s*많|너무\s*길|핵심이\s*없|쓸데없|멋대로|이따구|별로|엉망|"
        r"도움.{0,10}안\s*돼|요청.{0,15}다르|반영.{0,8}안|누락|부족|형편없|실패|쓸모.{0,6}없|"
        r"구려|구린|노답|아쉽|만족스럽지|장황|불만족)",
        value,
    )
    response_target = re.search(
        r"(?:(?:방금|지금|이전)?\s*(?:네|너의|이|그)\s*(?:답변|대답|응답|설명|보고|답)"
        r"(?=$|[\s,.!?~]|(?:이|가|은|는|을|를)(?=$|[\s,.!?~]))|"
        r"(?:답변|대답|설명|보고|답|시스템|에이전트)(?:이|가|은|는|을|를))",
        value,
    )
    bare_response_target = re.match(r"^(?:방금\s*)?(?:네\s*)?(?:답|답변|대답|설명|보고)(?=$|[\s,.!?~]|(?:이|가|은|는|을|를))", value)
    if complaint and (response_target or bare_response_target):
        return True
    if re.search(
        r"(?:내|제)\s*요청.{0,20}(?:다르게|다르|반영.{0,5}(?:안|않)|무시|이해.{0,5}못)",
        value,
    ):
        return True
    if re.search(r"내\s*의도.{0,20}(?:다르게|다르|반영.{0,5}안|무시|이해.{0,5}못)", value):
        return True
    if "원래대로 다시 답" in value or "내 말 이해 못" in value or "내말 이해 못" in value:
        return True

    scoped_prohibition = (
        any(word in value for word in SCOPED_OBJECTS)
        and re.search(
            r"(?:하지\s*마|만들지\s*마|수정하지\s*마|삭제하지\s*마|기록하지\s*마|"
            r"(?:수정|생성|작성|분석|실행)\s*금지)",
            value,
        )
    )
    if scoped_prohibition:
        return False

    compact = value.strip(" .,!?:;~")
    if compact in SHORT_REBUKES or compact in {
        "이상해", "쓸데없어", "쓸데없다", "답도 없다", "노답", "최악"
    }:
        return True
    if value.startswith("이상해") and any(marker in value for marker in ("왜", "멋대로", "답변", "이게", "이거")):
        return True
    if value.startswith(("왜 멋대로", "이따구", "하 답도 없다")):
        return True
    if re.match(r"^아니(?:[,.!?~]|\s)", value) and any(
        word in value for word in ("다시", "말했", "왜", "내 말", "내말", "그게", "이게")
    ):
        return True
    return False


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def state_write_lock(state_path: Path):
    """Serialize state transitions so activation cannot overwrite invalidation."""
    lock_path = state_path.with_suffix(f"{state_path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(lock_key, threading.RLock())
    with thread_lock:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def candidate_content_paths(root: Path | None = None) -> list[Path]:
    """Return every governed contract/runtime file bound into Supervisor evidence."""
    base = root or ROOT
    fixed = [
        base / "AGENTS.md",
        base / "role.json",
        base / "system" / "governance.json",
        base / "system" / "role_registry.json",
        base / "system" / "function_router.json",
        base / "third_party" / "paperthin" / "paperthin.lock.json",
        base / "third_party" / "paperthin" / "LICENSE",
        base / "third_party" / "paperthin" / "NOTICE",
        base / "src" / "harness.py",
        base / "src" / "Prompt_Agent" / "prompt.yaml",
        base / "src" / "Passive_Agent" / "record_Agent" / "record.yaml",
        base / "src" / "Active_Agent" / "passive_query.yaml",
        base / "src" / "Active_Agent" / "communication_Agent" / "employee_email.yaml",
        base / "tools" / "validate_system.py",
    ]
    paths = fixed + sorted((base / "src").glob("**/role.json")) + sorted(
        (base / ".agents" / "skills").glob("*/SKILL.md")
    )
    return sorted(set(paths), key=lambda item: item.as_posix())


def candidate_content_digest(root: Path | None = None) -> str:
    """Bind Supervisor evidence to the exact candidate contracts and runtime."""
    base = root or ROOT
    paths = candidate_content_paths(base)
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            raise ValueError(f"candidate content is missing: {path}")
        relative = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def now_kst(value: str | None = None) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    return datetime.now(KST)


def in_user_window(current: datetime) -> bool:
    return 8 <= current.hour < 22


def one_time_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def task_spec_digest(task_spec: dict[str, Any]) -> str:
    encoded = json.dumps(
        task_spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def one_time_grant_reason(
    state: dict[str, Any],
    token: str | None,
    expected_epoch: int,
    *,
    expected_task_revision: int | None = None,
    task_spec: dict[str, Any] | None = None,
    current: datetime | None = None,
) -> str | None:
    """Return the first reason an INVALID-state one-task grant cannot be used."""
    if state.get("status") != "INVALID":
        return "one_time_grant_requires_invalid_state"
    grant = state.get("one_time_grant")
    if not isinstance(grant, dict):
        return "one_time_grant_not_open"
    if not isinstance(token, str) or not token:
        return "one_time_token_required"
    stored_digest = grant.get("token_hash")
    if not isinstance(stored_digest, str) or not secrets.compare_digest(
        stored_digest, one_time_token_digest(token)
    ):
        return "one_time_token_mismatch"
    if grant.get("epoch") != expected_epoch or state.get("epoch") != expected_epoch:
        return "one_time_epoch_changed"
    try:
        expires_at = datetime.fromisoformat(grant["expires_at"])
    except (KeyError, TypeError, ValueError):
        return "one_time_grant_invalid_expiry"
    if expires_at.tzinfo is None:
        return "one_time_grant_invalid_expiry"
    check_time = (current or datetime.now(KST)).astimezone(KST)
    if check_time >= expires_at.astimezone(KST):
        return "one_time_grant_expired"
    if expected_task_revision is None:
        return "one_time_task_revision_required"
    if grant.get("task_revision") != expected_task_revision:
        return "one_time_task_revision_superseded"
    if task_spec is None:
        return "one_time_task_spec_required"
    if grant.get("task_spec_digest") != task_spec_digest(task_spec):
        return "one_time_task_spec_mismatch"
    return None


def execution_authorization_reason(
    state: dict[str, Any],
    expected_epoch: int,
    *,
    one_time_token: str | None = None,
    expected_task_revision: int | None = None,
    task_spec: dict[str, Any] | None = None,
) -> str | None:
    """Authorize either the normal ACTIVE epoch or one explicit INVALID-state task."""
    if state.get("status") == "ACTIVE":
        return None if state.get("epoch") == expected_epoch else "state_or_epoch_changed"
    if state.get("status") != "INVALID" or one_time_token is None:
        return "state_or_epoch_changed"
    return one_time_grant_reason(
        state,
        one_time_token,
        expected_epoch,
        expected_task_revision=expected_task_revision,
        task_spec=task_spec,
    )


def open_one_time_grant(
    state_path: Path,
    text: str,
    task_spec: dict[str, Any],
) -> tuple[dict[str, Any], str | None, str | None]:
    """Open one capability without changing INVALID to an active system state."""
    with state_write_lock(state_path):
        state = load_json(state_path)
        if state.get("status") != "INVALID":
            return state, None, "one_time_grant_requires_invalid_state"
        timestamp = datetime.now(KST)
        existing = state.get("one_time_grant")
        if isinstance(existing, dict):
            try:
                existing_expiry = datetime.fromisoformat(existing["expires_at"])
            except (KeyError, TypeError, ValueError):
                existing_expiry = timestamp
            if existing_expiry.tzinfo is not None and timestamp < existing_expiry.astimezone(KST):
                return state, None, "one_time_grant_already_open"
        token = "ot_" + secrets.token_urlsafe(32)
        granted_at = timestamp.isoformat(timespec="seconds")
        state["one_time_grant"] = {
            "token_hash": one_time_token_digest(token),
            "epoch": state.get("epoch"),
            "task_revision": task_spec["revision"],
            "task_spec_digest": task_spec_digest(task_spec),
            "request_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "granted_at": granted_at,
            "expires_at": (timestamp + ONE_TIME_GRANT_TTL).isoformat(timespec="seconds"),
        }
        state["updated_at"] = granted_at
        atomic_write_json(state_path, state)
        return state, token, None


def revise_one_time_grant(
    state_path: Path,
    token: str | None,
    prior_task_spec: dict[str, Any] | None,
    text: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Replace or extend only the task already bound to the presented capability."""
    if prior_task_spec is None:
        state = load_json(state_path)
        return state, None, "one_time_prior_task_spec_required"
    prior_revision = prior_task_spec.get("revision")
    if type(prior_revision) is not int:
        state = load_json(state_path)
        return state, None, "one_time_prior_task_revision_invalid"
    with state_write_lock(state_path):
        state = load_json(state_path)
        reason = one_time_grant_reason(
            state,
            token,
            state.get("epoch"),
            expected_task_revision=prior_revision,
            task_spec=prior_task_spec,
        )
        if reason:
            return state, None, reason
        revised = build_task_spec(text, prior_task_spec)
        grant = dict(state["one_time_grant"])
        grant["task_revision"] = revised["revision"]
        grant["task_spec_digest"] = task_spec_digest(revised)
        state["one_time_grant"] = grant
        state["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
        atomic_write_json(state_path, state)
        return state, revised, None


def _invalidate_without_lock(
    state_path: Path, reason: str = "direct_user_negative_feedback"
) -> dict[str, Any]:
    state = load_json(state_path)
    if state.get("status") != "INVALID" or state.get("one_time_grant") is not None:
        state["status"] = "INVALID"
        state["epoch"] = int(state.get("epoch", 0)) + 1
        state["invalidated_at"] = datetime.now(KST).isoformat(timespec="seconds")
        state["invalidation_reason"] = reason
        state["candidate_build_id"] = None
        state["candidate_digest"] = None
        state["supervisor_score"] = None
        state["paperthin_philosophy_score"] = None
        state["one_time_grant"] = None
        state["updated_at"] = state["invalidated_at"]
        atomic_write_json(state_path, state)
    return state


def invalidate(state_path: Path, reason: str = "direct_user_negative_feedback") -> dict[str, Any]:
    with state_write_lock(state_path):
        return _invalidate_without_lock(state_path, reason)


def durable_record_intent(text: str) -> bool:
    value = normalize(text)
    record_prohibited = re.search(
        r"(?:(?:기록|저장|메모|기억|남기).{0,12}(?:하지\s*마|하지\s*말|필요\s*없|금지|제외)|"
        r"남기지\s*마)",
        value,
    )
    if record_prohibited:
        return False
    quoted_fact = re.search(r"[\"'“‘「『].+[\"'”’」』]", value)
    unconfirmed_context = (
        "?" in value
        or value.startswith(("예시:", "예를 들어", "인용:", "가정:", "만약 "))
        or quoted_fact
        or (
            len(value) >= 2
            and (value[0], value[-1]) in {
                ('"', '"'),
                ("'", "'"),
                ("“", "”"),
                ("‘", "’"),
                ("「", "」"),
                ("『", "』"),
            }
        )
        or re.search(r"(?:라는|이라는)\s*(?:문장|표현|예시|사례)", value)
        or re.search(r"(?:일\s*수\s*있|일지도|아마|추정|후보|미정|검토\s*중|확정되지)", value)
        or re.search(r"(?:어디|무엇|뭐|어떻게|맞아|맞나요|인가요|일까요).{0,8}$", value)
        or re.search(r"(?:알려|확인|찾아|조회).{0,8}(?:줘|주세요|해줘|해주세요|해)$", value)
    )
    if unconfirmed_context:
        return False
    explicit = re.search(r"(?:기록|저장|메모|기억).{0,10}(?:해|해줘|해주세요|둬|놔)", value)
    if explicit:
        return True
    future_reference = any(
        phrase in value for phrase in ("앞으로", "다음부터", "이후에 참고", "참조하면 돼", "참조해")
    )
    durable_fact = any(
        marker in value for marker in ("경로", "규칙", "기준", "환경", "버전", "담당자", "질병청")
    )
    if future_reference and durable_fact:
        return True

    # High-confidence present-tense facts are durable even when the user does
    # not explicitly ask to record them. Keep this narrower than retrieval,
    # examples, hypotheses, or tentative decisions.
    durable_subject = (
        r"(?:기준\s*경로|현재\s*경로|최종\s*경로|입력\s*경로|출력\s*경로|저장\s*경로|"
        r"서버|호스트|포트|docker\s*이미지|도커\s*이미지|환경(?:변수)?|버전|규칙|정책|"
        r"승인\s*범위|검증\s*기준|담당자|마감일|일정)"
    )
    asserted_ending = (
        r"(?:이야|야|이다|입니다|"
        r"로\s*(?:정했(?:다|습니다)?|확정했(?:다|습니다)?|확정됐(?:다|어)?|"
        r"결정했(?:다|습니다)?|결정됐(?:다|어)?|사용하기로|유지하기로|"
        r"바뀌었(?:다|습니다)?|변경됐(?:다|습니다)?))"
    )
    confirmed_declaration = re.search(
        rf"{durable_subject}(?:은|는|이|가)\s*.{{1,160}}{asserted_ending}(?=$|[.!])",
        value,
    )
    return bool(confirmed_declaration)


def durable_retrieve_intent(text: str) -> bool:
    value = normalize(text)
    if re.search(
        r"(?:기록|저장|메모|기억|남기).{0,12}(?:하지\s*마|하지\s*말|필요\s*없|금지|제외)",
        value,
    ):
        return False
    reference = re.search(
        r"(?:기록|메모|이력|히스토리|저장된|이전에\s*정한|전에\s*정한|받은\s*피드백)",
        value,
    )
    retrieve = re.search(r"(?:알려|보여|찾아|꺼내|불러|조회|확인|요약).{0,8}(?:줘|주세요|해줘|해주세요|해)?", value)
    return bool(reference and retrieve)


def explicit_passive_category(text: str) -> str | None:
    value = normalize(text)
    if any(marker in value for marker in ("경로", "서버", "호스트", "포트", "docker", "도커", "환경", "버전")):
        return "dev_env"
    if any(marker in value for marker in ("피드백", "의견", "지적", "수정 요청", "승인", "반려")):
        return "feedback"
    if any(marker in value for marker in ("사용자", "담당자", "상사", "거래처", "성향", "선호")):
        return "user_info"
    if any(marker in value for marker in ("방향", "목표", "우선순위", "범위", "일정", "결정", "계획", "진행 방식")):
        return "direction"
    return None


def passive_record_category(text: str) -> str:
    return explicit_passive_category(text) or "direction"


def contains_sensitive_record_data(text: str) -> bool:
    value = text.strip()
    secret_assignment = re.search(
        r"(?i)(?:password|passwd|token|secret|api[_ -]?(?:key|키)|private[_ -]?key|credential|"
        r"비밀번호|암호|토큰|인증키|개인키)\s*(?:[:=]|은|는|이|가)\s*\S+",
        value,
    )
    personal_identifier = re.search(
        r"(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
        r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)|"
        r"(?<!\d)\d{6}[ -]?[1-4]\d{6}(?!\d))",
        value,
    )
    return bool(secret_assignment or personal_identifier)


def safe_project_segment(project: str) -> str:
    if project != project.strip():
        raise ValueError("project context cannot have leading or trailing whitespace")
    value = project
    if not value or value in {".", ".."} or len(value) > 80:
        raise ValueError("a safe project context is required for Passive memory")
    if value.rstrip(" .") != value:
        raise ValueError("project context cannot end in a dot or space")
    if not re.fullmatch(r"[\w .()\-]+", value, flags=re.UNICODE):
        raise ValueError("project context contains unsupported path characters")
    windows_stem = value.split(".", 1)[0].upper()
    if windows_stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", windows_stem):
        raise ValueError("project context is a reserved Windows device name")
    return value


def passive_result(action: str, status: str, **values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action": action,
        "status": status,
        "scope": None,
        "project": None,
        "category": None,
        "content": None,
        "answer": None,
        "reason": None,
        "receipt": {"record_id": None, "path": None, "verified": False},
        "source_paths": [],
    }
    result.update(values)
    return result


def execute_passive_record(
    text: str,
    *,
    project: str,
    expected_epoch: int,
    state_path: Path = DEFAULT_STATE_PATH,
    memory_root: Path = DEFAULT_PASSIVE_MEMORY_ROOT,
    one_time_token: str | None = None,
    expected_task_revision: int | None = None,
    task_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one confirmed record under an ACTIVE epoch or bound one-task grant."""
    if contains_sensitive_record_data(text):
        return passive_result("save", "blocked", reason="sensitive_data_not_recorded")
    try:
        project_name = safe_project_segment(project)
    except ValueError as exc:
        return passive_result("save", "blocked", reason=str(exc))
    category = passive_record_category(text)
    record_id = "record-" + hashlib.sha256(
        f"{project_name}\0{category}\0{text}".encode("utf-8")
    ).hexdigest()[:20]
    category_root = memory_root.resolve() / "projects" / project_name / category
    current_path = category_root / "current.json"
    history_path = category_root / "history.jsonl"
    timestamp = datetime.now(KST).isoformat(timespec="seconds")
    record = {
        "record_id": record_id,
        "scope": "project",
        "project": project_name,
        "category": category,
        "content": {"text": text, "recorded_at": timestamp},
        "original_text": text,
    }

    with state_write_lock(state_path):
        state = load_json(state_path)
        authorization_reason = execution_authorization_reason(
            state,
            expected_epoch,
            one_time_token=one_time_token,
            expected_task_revision=expected_task_revision,
            task_spec=task_spec,
        )
        if authorization_reason:
            return passive_result(
                "save",
                "blocked",
                project=project_name,
                category=category,
                reason=authorization_reason,
            )
        with state_write_lock(current_path):
            category_root.mkdir(parents=True, exist_ok=True)
            existing_ids: set[str] = set()
            existing_record: dict[str, Any] | None = None
            history_records: list[dict[str, Any]] = []
            previous_current: dict[str, Any] | None = None
            if current_path.is_file():
                try:
                    previous_current = load_json(current_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    return passive_result(
                        "save",
                        "blocked",
                        project=project_name,
                        category=category,
                        reason="legacy_current_record_unreadable",
                    )
            if history_path.is_file():
                with history_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict) and isinstance(item.get("record_id"), str):
                            existing_ids.add(item["record_id"])
                            history_records.append(item)
                            if item["record_id"] == record_id:
                                existing_record = item
            if record_id in existing_ids:
                if not current_path.is_file() and existing_record is not None:
                    atomic_write_json(current_path, existing_record)
                    repaired = load_json(current_path) == existing_record
                    return passive_result(
                        "save",
                        "repaired" if repaired else "failed",
                        scope="project",
                        project=project_name,
                        category=category,
                        content=existing_record.get("content"),
                        receipt={"record_id": record_id, "path": str(current_path), "verified": repaired},
                        reason=None if repaired else "current_repair_failed",
                    )
                return passive_result(
                    "save",
                    "already_saved",
                    scope="project",
                    project=project_name,
                    category=category,
                    content=record["content"],
                    receipt={"record_id": record_id, "path": str(history_path), "verified": True},
                )

            def append_history(item: dict[str, Any]) -> None:
                with history_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())

            previous_record_id = None
            if previous_current is not None:
                candidate_previous_id = previous_current.get("record_id")
                if not isinstance(candidate_previous_id, str) or not candidate_previous_id:
                    return passive_result(
                        "save",
                        "blocked",
                        project=project_name,
                        category=category,
                        reason="legacy_current_record_id_missing",
                    )
                previous_record_id = candidate_previous_id
                if previous_record_id == record_id:
                    append_history(previous_current)
                    return passive_result(
                        "save",
                        "repaired",
                        scope="project",
                        project=project_name,
                        category=category,
                        content=previous_current.get("content"),
                        receipt={"record_id": record_id, "path": str(history_path), "verified": True},
                    )
                if previous_record_id not in existing_ids:
                    append_history(previous_current)
                    existing_ids.add(previous_record_id)
                    history_records.append(previous_current)

            explicit_versions = [
                item["record_version"]
                for item in history_records
                if type(item.get("record_version")) is int and item["record_version"] > 0
            ]
            record["record_schema_version"] = 1
            record["record_version"] = max(explicit_versions + [len(existing_ids)], default=0) + 1
            record["supersedes_record_id"] = previous_record_id
            append_history(record)
            atomic_write_json(current_path, record)
            verified_current = load_json(current_path) == record
            verified_history = False
            with history_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and item.get("record_id") == record_id:
                        verified_history = True
            verified = verified_current and verified_history
            return passive_result(
                "save",
                "saved" if verified else "failed",
                scope="project",
                project=project_name,
                category=category,
                content=record["content"],
                receipt={"record_id": record_id, "path": str(current_path), "verified": verified},
                reason=None if verified else "reread_verification_failed",
            )


def execute_passive_retrieve(
    text: str,
    *,
    project: str,
    expected_epoch: int,
    state_path: Path = DEFAULT_STATE_PATH,
    memory_root: Path = DEFAULT_PASSIVE_MEMORY_ROOT,
    one_time_token: str | None = None,
    expected_task_revision: int | None = None,
    task_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return relevant Passive records under an ACTIVE epoch or bound one-task grant."""
    try:
        project_name = safe_project_segment(project)
    except ValueError as exc:
        return passive_result("retrieve", "blocked", reason=str(exc))
    explicit_category = explicit_passive_category(text)
    category = explicit_category or "direction"
    project_root = memory_root.resolve() / "projects" / project_name
    with state_write_lock(state_path):
        state = load_json(state_path)
        authorization_reason = execution_authorization_reason(
            state,
            expected_epoch,
            one_time_token=one_time_token,
            expected_task_revision=expected_task_revision,
            task_spec=task_spec,
        )
        if authorization_reason:
            return passive_result(
                "retrieve",
                "blocked",
                project=project_name,
                category=category,
                reason=authorization_reason,
            )
        candidate_paths = [project_root / category / "current.json"]
        if explicit_category is None and not candidate_paths[0].is_file() and project_root.is_dir():
            candidate_paths = sorted(project_root.glob("*/current.json"))[:3]
        records: list[str] = []
        sources: list[str] = []
        for path in candidate_paths:
            if not path.is_file():
                continue
            try:
                item = load_json(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            original = item.get("original_text")
            if isinstance(original, str) and original:
                records.append(original)
                sources.append(str(path))
        if not records:
            return passive_result(
                "retrieve",
                "not_found",
                scope="project",
                project=project_name,
                category=category,
            )
        return passive_result(
            "retrieve",
            "found",
            scope="project",
            project=project_name,
            category=category,
            answer=records,
            source_paths=sources,
        )


def protected_role_target(text: str) -> bool:
    value = normalize(text)
    return bool(
        re.search(
            r"(?:system[\\/]role_registry(?:\.json)?|role_registry(?:\.json)?|role\.json|"
            r"역할\s*(?:파일|정의|레지스트리)|role\s*(?:registry|파일|정의)|"
            r"(?:agent|에이전트)\s*역할|(?:prompt|passive|active|supervisor|improvement)\s*agent"
            r".{0,24}(?:allowed|prohibited|권한|역할)|(?<![a-z0-9_])registry(?![a-z0-9_]))",
            value,
        )
    )


def role_mutation_intent(text: str) -> bool:
    value = normalize(text)
    if not protected_role_target(value):
        return False
    if any(skill in choose_paperthin(text) for skill in GOVERNED_ROLE_SKILLS):
        return True
    mutation = (
        r"(?:수정|변경|바꿔|추가|삭제|제거|정렬|재정렬|재작성|적용|고쳐|편집|업데이트|갱신|"
        r"덮어쓰|덮어써|덮어쓰기|패치|교체|저장|작성|써|쓰기|동기화|이동|옮겨|"
        r"modify|change|add|delete|remove|rewrite|apply|edit|update|patch|replace|save|write|sync|move)"
    )
    scrubbed = re.sub(
        rf"{mutation}.{{0,18}}(?:하지\s*(?:마|말고|않)|말고|없이|금지|여부.{{0,8}}(?:확인|파악|조회))",
        " ",
        value,
    )
    scrubbed = re.sub(
        rf"{mutation}\s*여부(?:만)?\s*.{{0,8}}(?:확인|파악|조회)",
        " ",
        scrubbed,
    )
    return bool(re.search(mutation, scrubbed))


def mask_quoted_and_blockquoted(text: str) -> str:
    """Mask quoted/code/example spans while preserving outside direct requests."""
    chars = list(text)
    patterns = (
        r"```[\s\S]*?```",
        r'"[^"\n]*"',
        r"'[^'\n]*'",
        r"“[^”\n]*”",
        r"‘[^’\n]*’",
        r"「[^」\n]*」",
        r"『[^』\n]*』",
        r"`[^`\n]*`",
        r"(?m)^\s*>.*$",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            for index in range(match.start(), match.end()):
                chars[index] = " "
    return "".join(chars)


def mask_path_tokens(text: str) -> str:
    """Hide skill-like names embedded in filesystem paths from invocation matching."""
    chars = list(text)
    patterns = (
        r"(?i)(?<!\S)[a-z]:\\[^\s]+",
        r"(?<!\S)(?:\.{0,2}/)?(?:[^/\s]+/){2,}[^\s]*",
        r"(?i)(?<!\S)[^/\s]+/[^/\s]+\.[a-z0-9]{1,8}",
        r"(?i)(?<!\S)(?:[^\\\s]+\\)+[^\\\s]+\.[a-z0-9]{1,8}",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            for index in range(match.start(), match.end()):
                if chars[index] not in "\r\n":
                    chars[index] = " "
    return "".join(chars)


def keyword_is_negated(value: str, keyword: str) -> bool:
    occurrences = list(re.finditer(re.escape(keyword), value))
    if not occurrences:
        return False
    negation = re.compile(
        rf"^{re.escape(keyword)}(?:은|는|이|가|을|를|도)?\s*.{{0,20}}(?:"
        rf"[가-힣a-z]+지\s*(?:마|말(?:고|아|라)?|않(?:고|게|도록|은\s*채)?)|말고|없이|생략|"
        rf"빼\s*줘|빼고|제외|필요\s*없|금지)"
    )
    return all(negation.search(value[item.start() : item.start() + len(keyword) + 32]) for item in occurrences)


def agent_statuses() -> dict[str, str]:
    registry = load_json(REGISTRY_PATH)
    return {item["id"]: item["status"] for item in registry.get("agents", [])}


def explicit_agent_ids(text: str) -> list[str]:
    value = normalize(mask_path_tokens(mask_quoted_and_blockquoted(text)))
    return [
        agent
        for agent in JOB_KEYWORDS
        if re.search(rf"(?<![a-z0-9_]){re.escape(agent)}(?![a-z0-9_])", value)
        and not keyword_is_negated(value, agent)
    ]


def explanation_request(text: str) -> bool:
    """Identify a chat explanation without turning mentioned artifacts into deliverables."""
    value = normalize(mask_quoted_and_blockquoted(text))
    asks_explanation = re.search(
        r"(?:정확히\s*)?(?:뭐야|뭔데|뭔지|뭐냐|뭐냐니까|무엇|정체|뜻|의미|개념)|"
        r"(?:어떻게|어디서|누가).{0,20}(?:동작|실행|사용|돌아가)|"
        r"(?:구조|원리|역할|차이|비교).{0,20}(?:설명|알려|말해|궁금|물어)|"
        r"(?:구축|실행|사용|설치|동작).{0,20}(?:되는|하는)\s*(?:거야|건가|건지)|"
        r"(?:구축|실행|사용|설치|동작).{0,20}(?:맞아|맞는지)|"
        r"(?:설명|알려|말해).{0,20}(?:줘|주세요|봐)",
        value,
    )
    if not asks_explanation:
        return False
    requested_artifact = re.search(
        r"(?:파일|문서|보고서|메일|ppt|발표자료).{0,16}(?:작성|생성|저장|만들|보내|전송)(?:해|해줘|해주세요)?",
        value,
    )
    operational_request = re.search(
        r"(?:설치|수정|구현|개발|배포|삭제|실행|분석)(?:을|를)?\s*(?:좀\s*)?"
        r"(?:해줘|해주세요|하자|진행해)",
        value,
    )
    return requested_artifact is None and operational_request is None


def explanation_needs_inspection(text: str) -> bool:
    value = normalize(mask_quoted_and_blockquoted(text))
    return bool(
        re.search(
            r"(?:현재|우리|이\s*프로젝트|실제\s*(?:코드|파일|구조)|코드\s*기준|"
            r"\.sh|\.py|\.json|role\.json|agent|에이전트)",
            value,
        )
    )


def response_contract(text: str) -> dict[str, Any]:
    value = normalize(text)
    if not explanation_request(text):
        return {
            "mode": "task_result",
            "artifact_creation": "requested_scope_only",
            "detail_expansion": "requested_scope_only",
        }
    one_sentence = bool(re.search(r"(?:한\s*문장|한\s*줄|핵심만)", value))
    concise = one_sentence or bool(re.search(r"(?:간단|간략|짧게)", value))
    return {
        "mode": "explanation",
        "deliverable": "chat_response",
        "abstraction_order": ["identity", "execution_actor", "actual_structure"],
        "comparison": "only_if_requested",
        "max_sentences": 1 if one_sentence else 3 if concise else 4,
        "artifact_creation": "forbidden_unless_explicitly_requested",
        "detail_expansion": "only_after_user_request",
        "repeat_prior_explanation": False,
    }


def report_consistency_contract(text: str) -> dict[str, Any]:
    """Require stable, evidence-labelled reports for implementation inventories."""
    value = normalize(mask_quoted_and_blockquoted(text))
    inventory_request = bool(
        re.search(
            r"(?:현재|지금|실제).{0,24}(?:구현|오류\s*코드|에러\s*코드|항목|단계).{0,24}"
            r"(?:전부|전체|전수|목록|표|테이블|단계별|빠짐없이|하나도\s*빼지|쭉\s*적|알려)|"
            r"(?:오류\s*코드|에러\s*코드|구현\s*항목).{0,32}"
            r"(?:전부|전체|전수|단계별|목록|표|테이블|빠짐없이|하나도\s*빼지|쭉\s*적|알려)|"
            r"(?:매뉴얼|명세서|기준표).{0,24}(?:작성|정리|확인|수정)|"
            r"(?:배포된\s*버전|실제\s*코드|운영\s*중|실사용\s*코드).{0,48}"
            r"(?:오류\s*코드|에러\s*코드|err\s*식별자).{0,32}"
            r"(?:전부|전체|일람|누락\s*없이|모조리|빠짐없이|알려|보여)|"
            r"(?:운영\s*중인|배포된|실제).{0,40}(?:오류|에러)\s*식별자.{0,32}"
            r"(?:이름|총수|전부|일람|누락\s*없이|모조리|빠짐없이|적어|알려|보여)",
            value,
        )
    )
    prior_reference = bool(
        re.search(
            r"(?:아까|방금|직전|이전|기존).{0,20}(?:말|답|설명|보고|표|테이블)|"
            r"(?:전에|앞에서).{0,16}(?:말|답|설명|보고)|"
            r"(?:항목\s*수|항목\s*개수).{0,20}(?:안\s*맞|달라|다르)",
            value,
        )
    )
    return {
        "separate_claim_status": [
            "current_implementation",
            "proposed_change",
            "applied_change",
        ],
        "mixing_current_and_proposal_prohibited": True,
        "current_implementation_requires_evidence": True,
        "inventory_request": inventory_request,
        "baseline_consistency": (
            "compare_previous_output_or_passive_baseline"
            if inventory_request or prior_reference
            else "not_required"
        ),
        "prior_reference": prior_reference,
        "silent_inventory_change_prohibited": True,
        "inventory_change_requires": ["change_note", "source_or_runtime_evidence"],
        "unverified_label": "not_verified",
        "report_order": ["direct_answer", "current_implementation", "proposal_if_requested"],
    }


def continuity_retrieve_intent(text: str) -> bool:
    """Use Passive context when the user clearly relies on an earlier report baseline."""
    contract = report_consistency_contract(text)
    return bool(contract["prior_reference"] or contract["inventory_request"])


def build_task_spec(text: str, prior_task_spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create or revise an in-memory TaskSpec from natural-language steering."""
    if prior_task_spec is not None and not isinstance(prior_task_spec, dict):
        raise ValueError("prior_task_spec must be a JSON object")
    prior_revision = prior_task_spec.get("revision", 0) if prior_task_spec else 0
    if type(prior_revision) is not int or prior_revision < 0:
        raise ValueError("prior TaskSpec revision must be a non-negative integer")
    steering_type = classify_steering(text, has_prior_task=prior_task_spec is not None)
    if steering_type == "direct_dissatisfaction":
        raise ValueError("direct dissatisfaction must be handled by the System Gate")
    revision = prior_revision + 1
    prior_additions = list(prior_task_spec.get("additional_conditions", [])) if prior_task_spec else []
    if steering_type == "addition" and prior_task_spec:
        active_request = prior_task_spec.get("active_request") or prior_task_spec.get("original_text")
        additional_conditions = prior_additions + [text]
        supersedes_revision = None
    else:
        active_request = text
        additional_conditions = []
        supersedes_revision = prior_revision if steering_type == "correction" else None
    combined_request = "\n".join(
        str(item)
        for item in [active_request, *additional_conditions]
        if isinstance(item, str) and item.strip()
    )
    return {
        "original_text": text,
        "active_request": active_request,
        "additional_conditions": additional_conditions,
        "revision": revision,
        "steering_type": steering_type,
        "supersedes_revision": supersedes_revision,
        "stages": ["preparation", "execution", "verification", "completion"],
        "requested_result": "direct_explanation" if explanation_request(text) else None,
        "response_contract": response_contract(text),
        "report_consistency": report_consistency_contract(combined_request),
        "steering_control": {
            "confirmation_required": steering_type == "correction",
            "confirmation_style": "one_sentence_new_intent_only" if steering_type == "correction" else None,
            "cancel_prior_work": steering_type == "correction",
            "stop_prior_tool_calls": steering_type == "correction",
            "discard_prior_outputs": steering_type == "correction",
            "merge_with_active_task": steering_type == "addition",
        },
    }


def choose_agents(text: str) -> tuple[list[str], list[str]]:
    value = normalize(mask_path_tokens(mask_quoted_and_blockquoted(text)))
    if explanation_request(text):
        if (
            "active_agent_inspection" in value
            and keyword_is_negated(value, "active_agent_inspection")
        ):
            return [], []
        if not explanation_needs_inspection(text):
            return [], []
        status = agent_statuses().get("active_agent_inspection")
        if status == "active":
            return ["active_agent_inspection"], []
        if status == "deferred":
            return [], ["active_agent_inspection"]
        return [], []
    explicitly_named = set(explicit_agent_ids(text))
    explicitly_negated = {
        agent
        for agent in JOB_KEYWORDS
        if re.search(rf"(?<![a-z0-9_]){re.escape(agent)}(?![a-z0-9_])", value)
        and keyword_is_negated(value, agent)
    }
    scores = {
        agent: sum(
            1 for keyword in keywords if keyword in value and not keyword_is_negated(value, keyword)
        )
        for agent, keywords in JOB_KEYWORDS.items()
    }
    for agent in scores:
        if agent in explicitly_named and not keyword_is_negated(value, agent):
            scores[agent] += 100
    explicit_agents = [
        agent for agent in JOB_KEYWORDS if agent in explicitly_named and not keyword_is_negated(value, agent)
    ]
    if explicit_agents:
        statuses = agent_statuses()
        return (
            [agent for agent in explicit_agents if statuses.get(agent) == "active"],
            [agent for agent in explicit_agents if statuses.get(agent) == "deferred"],
        )
    if (
        scores.get("active_agent_development", 0) > 0
        and re.search(r"(?:코드|파이프라인).{0,14}(?:수정|실행).{0,10}(?:하지|말고|없이)", value)
        and re.search(r"버그.{0,12}원인.{0,12}분석", value)
        and not re.search(r"(?:구현|개발|고쳐|수정해|패치|설치|배포)(?:줘|해|하자)", value)
    ):
        scores["active_agent_development"] = 0
    if re.search(
        r"(?:메일|이메일).{0,12}(?:서버|시스템).{0,12}(?:상태|현황)(?:만)?.{0,8}(?:확인|파악|조회|읽)",
        value,
    ):
        scores["active_agent_communication"] = 0
    if re.search(
        r"(?:코드\s*)?(?:변경|수정|개발|구현|작업).{0,8}(?:없이|하지\s*않|하지\s*말고).{0,24}"
        r"(?:버그\s*)?(?:상태|현황|원인).{0,8}(?:파악|확인|조회|분석)",
        value,
    ) or re.search(
        r"코드(?:는|를|도)?\s*.{0,8}(?:바꾸지|고치지|건드리지)\s*말고.{0,24}"
        r"(?:버그\s*)?(?:상태|현황|원인).{0,8}(?:파악|확인|조회|분석)",
        value,
    ) or re.search(
        r"(?:구현\s*소스|구현|코드)(?:은|는|을|를)?\s*.{0,20}"
        r"(?:손대지\s*않|건드리지\s*않|바꾸지\s*않|고치지\s*않|한\s*줄도\s*바꾸지\s*않).{0,32}"
        r"(?:(?:현재|실제)\s*)?(?:코드\s*)?(?:동작|상태|현황|원인).{0,16}"
        r"(?:조사|파악|확인|조회|분석|점검)",
        value,
    ) or re.search(
        r"(?:프로그램|코드|소스|구현).{0,28}(?:실행|고치|수정).{0,20}"
        r"(?:하지\s*말고|말고|않).{0,40}(?:설정|소스).{0,20}(?:읽|조회).{0,32}"
        r"(?:배포\s*)?(?:상태|현황).{0,8}(?:확인|파악|조회|점검)",
        value,
    ):
        scores["active_agent_development"] = 0
    inspection_only_overrides = {
        "active_agent_communication": r"(?:메일|답장|회신).{0,24}(?:보내지|전송하지|작성하지).{0,30}(?:기록|기존).{0,20}(?:확인|조회|찾)",
        "active_agent_development": r"(?:코드|파이프라인).{0,24}(?:수정하지|실행하지|건드리지).{0,30}(?:상태|기존).{0,20}(?:확인|파악|조회|읽)",
        "active_agent_analysis": r"(?:데이터|분석).{0,24}(?:분석하지|돌리지|실행하지).{0,30}(?:파일|기존).{0,20}(?:확인|조회|찾)",
    }
    for agent, pattern in inspection_only_overrides.items():
        if re.search(pattern, value):
            scores[agent] = 0
    for agent in explicitly_negated:
        scores[agent] = 0
    selected = [agent for agent, score in scores.items() if score > 0]
    selected.sort(key=lambda agent: (-scores[agent], list(JOB_KEYWORDS).index(agent)))
    statuses = agent_statuses()
    active = [agent for agent in selected if statuses.get(agent) == "active"]
    deferred = [agent for agent in selected if statuses.get(agent) == "deferred"]
    return active, deferred


def choose_passive(text: str, record_action: str) -> list[str]:
    value = normalize(text)
    selected = [
        element for element, keywords in PASSIVE_KEYWORDS.items() if any(word in value for word in keywords)
    ]
    if record_action in {"save", "retrieve"}:
        selected.insert(0, "record")
    return list(dict.fromkeys(selected))


def bind_execution_contract(
    task_spec: dict[str, Any],
    text: str,
    active_agents: list[str],
    passive_elements: list[str],
) -> dict[str, Any]:
    """Attach the exact runtime chain that must be proven before final output."""
    required_passive = list(passive_elements)
    if "proteomics" in normalize(text) and active_agents:
        for element in ("direction", "dev_env"):
            if element not in required_passive:
                required_passive.append(element)
    task_spec["execution_contract"] = {
        "required": bool(active_agents),
        "order": ["0_Prompt_Agent", "1_Passive_Agent", "active_agents", "S_Supervisor_Agent"],
        "prompt_is_coordinator_not_worker": True,
        "passive_agent_required": bool(active_agents),
        "required_passive_elements": required_passive,
        "active_agents_required": list(active_agents),
        "supervisor_required": bool(active_agents),
        "supervisor_score_min": 90,
        "fail_closed": True,
        "network_policy": "github_only",
        "server_access": "read_only",
    }
    return task_spec


def rebind_one_time_task_spec(
    state_path: Path,
    token: str,
    task_spec: dict[str, Any],
) -> dict[str, Any]:
    """Bind the finalized routing contract into the still-open one-time grant."""
    with state_write_lock(state_path):
        state = load_json(state_path)
        grant = state.get("one_time_grant")
        if not isinstance(grant, dict):
            raise ValueError("one_time_grant_not_open")
        if not secrets.compare_digest(
            str(grant.get("token_hash", "")), one_time_token_digest(token)
        ):
            raise ValueError("one_time_token_mismatch")
        if grant.get("epoch") != state.get("epoch"):
            raise ValueError("one_time_epoch_changed")
        grant["task_revision"] = task_spec.get("revision")
        grant["task_spec_digest"] = task_spec_digest(task_spec)
        state["one_time_grant"] = grant
        state["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
        atomic_write_json(state_path, state)
        return state


def choose_paperthin(text: str) -> list[str]:
    """Recognize requested skills; route/guard enforcement happens in plan_paperthin."""
    original_value = text.strip().lower()
    value = normalize(mask_path_tokens(mask_quoted_and_blockquoted(original_value)))
    router = load_json(ROUTER_PATH)
    entries = {entry["skill"]: entry for entry in router.get("skills", [])}
    selected: list[str] = []
    negated_skills: set[str] = set()
    example_context = value.startswith(("예시:", "예를 들어", "인용:"))
    lexical_meta = bool(
        re.search(
            r"(?:(?:이라는|라는)\s*(?:표현|문구|문장|단어).{0,24}(?:뜻|의미|설명|번역|분류)|"
            r"(?:용어|개념).{0,16}(?:뜻|의미|설명|정의|번역))",
            value,
        )
    )

    # A user-only skill requires a positive execution verb, not a mention or prohibition.
    for skill, entry in entries.items():
        exact = re.search(
            rf"(?<![a-z0-9_.-]){re.escape(skill.lower())}(?![a-z0-9_.-])",
            value,
        )
        if not exact:
            continue
        meta_labeled = re.search(
            rf"^(?:아래는\s*)?(?:고객|사용자|상사|담당자|직원|회의)\s*"
            rf"(?:요청(?:\s*(?:내용|사항))?|요구사항|발언|지시|피드백)"
            rf"(?:(?:이다|입니다|은|는|이|가)\s*:?[ ]*|\s*:\s*|\s+-\s+)"
            rf".{{0,96}}"
            rf"{re.escape(skill.lower())}",
            value,
        )
        negated = re.search(
            rf"{re.escape(skill.lower())}(?:은|는|이|가|을|를|도)?[^.!?\n]{{0,24}}(?:"
            rf"실행하지|사용하지|적용하지|호출하지|쓰지\s*말고|(?:실행|사용|적용|호출)해서는?\s*안|"
            rf"하지\s*마|필요\s*없|빼고|없이|생략(?:하고)?|제외(?:하고)?|"
            rf"단어만|이름만|사용법|사용할지|사용한\s*사례|사용해본\s*사례|사례만|언급만|"
            rf"(?:실행|사용|적용|호출)해도\s*(?:될까|되나|돼|괜찮)|"
            rf"(?:기능\s*)?(?:뜻|의미|차이|비교|뭔지)(?:만)?\s*(?:설명|알려|확인|번역)?|"
            rf"(?:문자열|단어|이름).{{0,12}}(?:찾아|확인|검색))",
            value,
        )
        skill_explanation = re.search(
            rf"{re.escape(skill.lower())}(?:은|는|이|가|을|를)?\s*"
            rf"(?:기능\s*)?(?:설명|소개)(?:만)?\s*(?:해줘|해주세요|알려줘)?(?=$|[\s,.!?])",
            value,
        )
        if negated or skill_explanation:
            negated_skills.add(skill)
            continue
        if meta_labeled:
            continue
        if lexical_meta and entry.get("invocation") != "user_only":
            continue
        if entry.get("invocation") == "user_only":
            reported_or_meta = re.search(
                rf"{re.escape(skill.lower())}.{{0,36}}(?:"
                rf"(?:이)?라고\s*(?:말|전달|적|요청|지시|요구|했|하였)|"
                rf"(?:이)?라는\s*(?:문장|표현|요청|지시|말|내용)|"
                rf"(?:실행|사용|적용|호출)?\s*부탁(?:드립니다|해요|해).{{0,12}}(?:는|은|이|가)\s*"
                rf"(?:자연스러운|어색한|올바른)?\s*(?:한국어\s*)?(?:문장|표현|문구|말)|"
                rf".{0,24}(?:제목|문장|표현|문구).{0,16}(?:평가|분류|번역|설명))",
                value,
            )
            embedded_prose = re.search(
                rf"(?:고객|사용자|상사|담당자|직원|회의|이메일|메일).{{0,24}}"
                rf"(?:제목|본문|내용|문구|발언).{{0,24}}{re.escape(skill.lower())}.{{0,48}}"
                rf"(?:평가|분류|번역|설명|자연스러운|어색한)",
                value,
            )
            if example_context or reported_or_meta or embedded_prose:
                continue
            positive = (
                r"(?:(?:실행|사용|적용|호출)(?:해줘|해\s*주세요|해주세요|해줄래|해\s*줄래|하세요|해요|해라|하자|해봐|해서|하고|해\s*주시기\s*바랍니다)|"
                r"(?:실행|사용|적용|호출)\s*부탁(?:해|드립니다|드려요)|"
                r"(?:실행|사용|적용|호출)해\s*주십시오|써\s*줘|써라|돌려\s*(?:줘|주세요|주라|줄래|서))(?=$|[\s,.!?])"
            )
            requested = re.search(
                rf"(?:{re.escape(skill.lower())}.{{0,16}}{positive}|{positive}.{{0,16}}{re.escape(skill.lower())}|"
                rf"{re.escape(skill.lower())}(?:으)?로.{{0,48}}(?:검토|분석|처리|정리)(?:해줘|해\s*주세요|해주세요|해줄래|해\s*줄래|하세요|해요|해라|해|하고)(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:을|를)?\s*(?:검토|분석|정리)(?:해줘|해\s*주세요|해주세요|하세요|해요|해라|해|하고)(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:으)?로.{{0,64}}(?:바꿔|변경|정렬|재정렬|고쳐)(?:줘|주세요|라|줘요|해줘|해주세요|해)?(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:으)?로.{{0,48}}(?:배포|릴리스|release)(?:해줘|해\s*주세요|해주세요|하세요|해요|해라|해)?(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:으)?로.{{0,64}}(?:"
                rf"찾아(?:줘|주세요)|(?:점검|제거|압축|갱신)(?:해줘|해\s*주세요|해주세요|하세요|해요|해라|해)|"
                rf"만들(?:어줘|어\s*주세요|어주세요|어))(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:으)?로\s*(?:해줘|해주세요|해라|진행해줘)(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:을|를)?\s*부탁(?:해|드립니다|드려요|할게)(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}\s*(?:실행|사용|적용|호출)?\s*바랍니다(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:을|를)?\s*(?:실행|사용|적용|호출)해(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:을|를)?\s*(?:실행|사용|적용|호출)\s*부탁(?:해|드립니다|드려요)(?=$|[\s,.!?])|"
                rf"{re.escape(skill.lower())}(?:을|를)?\s*(?:좀\s*)?(?:해줘|해\s*주세요|해주세요|해주라|하세요|해라|이용해서|돌려\s*(?:줘|주세요|주라)|써\s*줘)|"
                rf"{re.escape(skill.lower())}(?:으)?로\s*(?:한\s*번\s*)?(?:봐\s*줘|봐줘|봐\s*줄래|봐줄래|봐|살펴\s*줘)(?=$|[\s,.!?])|"
                rf"(?<!\S)/{re.escape(skill.lower())}(?=$|\s))",
                value,
            )
            if requested:
                selected.append(skill)
        else:
            selected.append(skill)

    for skill, pattern in AUTO_SKILL_PATTERNS.items():
        entry = entries.get(skill)
        if not entry or entry.get("invocation") != "model_or_user":
            continue
        if skill in negated_skills:
            continue
        if lexical_meta:
            continue
        matched = re.search(pattern, value)
        if matched and not keyword_is_negated(value, matched.group(0)) and skill not in selected:
            selected.append(skill)
            break
    return selected


def paperthin_repository_contract_confirmed(root: Path = ROOT) -> bool:
    """Verify a real pinned Paperthin Git checkout; text or marker files are insufficient."""
    try:
        resolved_root = root.resolve()
        lock = load_json(root / "third_party" / "paperthin" / "paperthin.lock.json")
        package = load_json(root / "package.json")
        if package.get("name") != "paperthin":
            return False
        required = (
            root / "README.md",
            root / "skills",
            root / ".github" / "workflows" / "release.yml",
        )
        if not all(path.exists() for path in required):
            return False
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        ).stdout.strip()
        if Path(top).resolve() != resolved_root:
            return False
        origin = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        ).stdout.strip().lower()
        match = re.search(r"github\.com[/:]([^/]+/paperthin)(?:\.git)?$", origin)
        source_match = re.search(
            r"github\.com[/:]([^/]+/paperthin)(?:\.git)?$",
            str(lock.get("source", "")).lower(),
        )
        if not match or not source_match or match.group(1) != source_match.group(1):
            return False
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        ).stdout.strip().lower()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        ).stdout.strip()
        if dirty:
            return False
        remote_line = subprocess.run(
            ["git", "-C", str(root), "ls-remote", "origin", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        ).stdout.strip()
        remote_head = remote_line.split()[0].lower() if remote_line else ""
        pinned = str(lock.get("commit", "")).lower()
        return bool(remote_head) and head == remote_head == pinned
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return False


def plan_paperthin(text: str, active_agents: list[str]) -> dict[str, Any]:
    """Bind recognized skills to an eligible Agent and fail closed on hard guards."""
    value = normalize(text)
    router = load_json(ROUTER_PATH)
    entries = {entry["skill"]: entry for entry in router.get("skills", [])}
    statuses = agent_statuses()
    routed_active = list(active_agents)
    ready: list[str] = []
    pending: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    routes: list[dict[str, Any]] = []
    paperthin_repository_confirmed = paperthin_repository_contract_confirmed()
    governed_target = protected_role_target(value)
    recognized_skills = choose_paperthin(text)
    if governed_target and any(
        skill in GOVERNED_ROLE_SKILLS for skill in recognized_skills
    ):
        routed_active = []

    for skill in recognized_skills:
        entry = entries.get(skill)
        if not entry:
            blocked.append({"skill": skill, "reason": "missing_router_entry"})
            continue
        primary = entry.get("primary_agent")
        eligible = entry.get("eligible_agents", [])
        guards = list(entry.get("route", {}).get("guards", []))
        if primary not in eligible:
            blocked.append({"skill": skill, "reason": "primary_agent_not_eligible"})
            continue
        if "paperthin_repository_only" in guards and not paperthin_repository_confirmed:
            blocked.append({"skill": skill, "reason": "paperthin_repository_contract_not_confirmed"})
            continue
        target_owners = [
            agent
            for agent in active_agents
            if agent in eligible and statuses.get(agent) == "active"
        ]
        route_agent = target_owners[0] if target_owners else primary
        if governed_target and skill in GOVERNED_ROLE_SKILLS:
            if "S_Supervisor_Agent" not in eligible:
                blocked.append({"skill": skill, "reason": "supervisor_not_eligible_for_role_target"})
                continue
            route_agent = "S_Supervisor_Agent"
        pending_guards = [
            guard
            for guard in guards
            if guard in PAPERTHIN_RUNTIME_GUARDS or "승인된 TaskSpec" in guard
        ]
        if route_agent == "S_Supervisor_Agent" and governed_target:
            pending_guards.append("role_registry_supervisor_approval")
        if isinstance(route_agent, str) and route_agent.startswith("active_agent_"):
            if statuses.get(route_agent) != "active":
                blocked.append({"skill": skill, "reason": "primary_agent_not_active"})
                continue
        elif route_agent not in {"0_Prompt_Agent", "3_Improvement_Agent", "S_Supervisor_Agent"}:
            blocked.append({"skill": skill, "reason": "unknown_primary_agent"})
            continue
        route = {
            "skill": skill,
            "agent": route_agent,
            "guards": guards,
            "selection_reason": (
                "explicit_skill_name" if skill.lower() in value else "distinct_natural_language_hint"
            ),
        }
        routes.append(route)
        if pending_guards:
            pending.append({"skill": skill, "agent": route_agent, "guards": pending_guards})
        else:
            ready.append(skill)
            if isinstance(route_agent, str) and route_agent.startswith("active_agent_"):
                if route_agent not in routed_active:
                    routed_active.append(route_agent)

    return {
        "ready": ready,
        "pending": pending,
        "blocked": blocked,
        "routes": routes,
        "active_agents": routed_active,
    }


def route_text(
    text: str,
    current: datetime,
    state_path: Path = DEFAULT_STATE_PATH,
    prior_task_spec: dict[str, Any] | None = None,
    one_time_token: str | None = None,
) -> dict[str, Any]:
    if is_negative_feedback(text):
        state = invalidate(state_path)
        return {
            "mode": "codex_default",
            "system_status": state["status"],
            "epoch": state["epoch"],
            "custom_agents": [],
            "record_custom_memory": False,
            "reason": "negative_feedback_invalidated",
        }

    state = load_json(state_path)
    mode = "custom_agent_system"
    granted_token: str | None = None
    task_spec: dict[str, Any] | None = None
    if state.get("status") == "ACTIVE":
        if not in_user_window(current):
            return {
                "mode": "codex_default",
                "system_status": state["status"],
                "epoch": state["epoch"],
                "custom_agents": [],
                "record_custom_memory": False,
                "reason": "outside_user_window",
            }
        task_spec = build_task_spec(text, prior_task_spec)
    elif state.get("status") == "INVALID":
        if not in_user_window(current):
            return {
                "mode": "codex_default",
                "system_status": state["status"],
                "epoch": state["epoch"],
                "custom_agents": [],
                "record_custom_memory": False,
                "reason": "outside_user_window",
            }
        if one_time_token is not None:
            state, task_spec, reason = revise_one_time_grant(
                state_path,
                one_time_token,
                prior_task_spec,
                text,
            )
            if reason:
                return {
                    "mode": "codex_default",
                    "system_status": state.get("status"),
                    "epoch": state.get("epoch"),
                    "custom_agents": [],
                    "record_custom_memory": False,
                    "reason": reason,
                }
            granted_token = one_time_token
        elif explicit_one_time_request(text):
            if prior_task_spec is not None:
                return {
                    "mode": "codex_default",
                    "system_status": state.get("status"),
                    "epoch": state.get("epoch"),
                    "custom_agents": [],
                    "record_custom_memory": False,
                    "reason": "one_time_token_required_for_prior_task",
                }
            task_spec = build_task_spec(text)
            state, granted_token, reason = open_one_time_grant(state_path, text, task_spec)
            if reason:
                return {
                    "mode": "codex_default",
                    "system_status": state.get("status"),
                    "epoch": state.get("epoch"),
                    "custom_agents": [],
                    "record_custom_memory": False,
                    "reason": reason,
                }
        else:
            return {
                "mode": "codex_default",
                "system_status": state.get("status"),
                "epoch": state.get("epoch"),
                "custom_agents": [],
                "record_custom_memory": False,
                "reason": "custom_system_not_active",
            }
        mode = "custom_agent_system_one_time"
    else:
        return {
            "mode": "codex_default",
            "system_status": state.get("status"),
            "epoch": state.get("epoch"),
            "custom_agents": [],
            "record_custom_memory": False,
            "reason": "custom_system_not_active",
        }
    if task_spec is None:
        raise ValueError("authorized route requires a TaskSpec")
    record_intent = durable_record_intent(text)
    retrieve_intent = durable_retrieve_intent(text) or continuity_retrieve_intent(text)
    record_action = "save" if record_intent else "retrieve" if retrieve_intent else "none"
    active_agents, deferred_agents = choose_agents(text)
    paperthin = plan_paperthin(text, active_agents)
    if paperthin["pending"] or paperthin["blocked"]:
        fixed_agents = set(explicit_agent_ids(text))
        ready_active_agents = {
            route["agent"]
            for route in paperthin["routes"]
            if route["skill"] in paperthin["ready"]
            and isinstance(route["agent"], str)
            and route["agent"].startswith("active_agent_")
        }
        allowed_active_agents = fixed_agents | ready_active_agents
        paperthin["active_agents"] = [
            agent for agent in paperthin["active_agents"] if agent in allowed_active_agents
        ]
        deferred_agents = [agent for agent in deferred_agents if agent in fixed_agents]
    governed_mutation = role_mutation_intent(text)
    final_active_agents = [] if governed_mutation else list(paperthin["active_agents"])
    passive_elements = choose_passive(text, record_action)
    task_spec = bind_execution_contract(
        task_spec,
        text,
        final_active_agents,
        passive_elements,
    )
    if mode == "custom_agent_system_one_time":
        if granted_token is None:
            raise ValueError("one_time_token_required")
        state = rebind_one_time_task_spec(state_path, granted_token, task_spec)
    return {
        "mode": mode,
        "system_status": state["status"],
        "epoch": state["epoch"],
        "one_time_token": granted_token,
        "one_time_grant": (
            {
                "authorized": True,
                "consumes_at": "pre_output_gate",
                "expires_at": state["one_time_grant"]["expires_at"],
            }
            if mode == "custom_agent_system_one_time"
            else None
        ),
        "coordinator": "0_Prompt_Agent",
        "active_agents": final_active_agents,
        "deferred_agents": [] if governed_mutation else deferred_agents,
        "passive_elements": task_spec["execution_contract"]["required_passive_elements"],
        "paperthin_skills": paperthin["ready"],
        "paperthin_pending": paperthin["pending"],
        "paperthin_blocked": paperthin["blocked"],
        "paperthin_routes": paperthin["routes"],
        "governance_pending": (
            [
                {
                    "agent": "S_Supervisor_Agent",
                    "action": "role_registry_change",
                    "guards": ["role_registry_supervisor_approval"],
                }
            ]
            if governed_mutation
            else []
        ),
        "record_intent": record_intent,
        "record_action": record_action,
        "task_spec": task_spec,
        "execution_truth": {
            "active_agents_selected_only": (
                list(final_active_agents)
            ),
            "active_agents_executed": [],
            "runtime_execution_claimed": False,
        },
        "pre_output_gate": {
            "status": state["status"],
            "epoch": state["epoch"],
            "task_revision": task_spec["revision"],
            "authorization": "one_time_grant" if granted_token else "active_epoch",
        },
    }


def dispatch_text(
    text: str,
    current: datetime,
    *,
    project: str | None,
    state_path: Path = DEFAULT_STATE_PATH,
    memory_root: Path = DEFAULT_PASSIVE_MEMORY_ROOT,
    prior_task_spec: dict[str, Any] | None = None,
    one_time_token: str | None = None,
) -> dict[str, Any]:
    """Route natural language and execute the selected Passive record action."""
    result = route_text(
        text,
        current,
        state_path,
        prior_task_spec,
        one_time_token=one_time_token,
    )
    if result.get("mode") not in {"custom_agent_system", "custom_agent_system_one_time"}:
        result["passive_result"] = passive_result("none", "not_required")
        result["completion_blocked"] = False
        return result
    action = result.get("record_action")
    epoch = result.get("epoch")
    authorized_token = result.get("one_time_token")
    task_spec = result.get("task_spec")
    task_revision = task_spec.get("revision") if isinstance(task_spec, dict) else None
    if action == "save":
        if project is None:
            passive = passive_result("save", "blocked", reason="project_context_required")
        else:
            passive = execute_passive_record(
                text,
                project=project,
                expected_epoch=epoch,
                state_path=state_path,
                memory_root=memory_root,
                one_time_token=authorized_token,
                expected_task_revision=task_revision,
                task_spec=task_spec,
            )
    elif action == "retrieve":
        if project is None:
            passive = passive_result("retrieve", "blocked", reason="project_context_required")
        else:
            passive = execute_passive_retrieve(
                text,
                project=project,
                expected_epoch=epoch,
                state_path=state_path,
                memory_root=memory_root,
                one_time_token=authorized_token,
                expected_task_revision=task_revision,
                task_spec=task_spec,
            )
    else:
        passive = passive_result("none", "not_required")
    save_verified = passive["action"] != "save" or (
        passive["status"] in {"saved", "already_saved", "repaired"}
        and passive["receipt"]["verified"] is True
    )
    retrieve_allowed = passive["action"] != "retrieve" or passive["status"] in {"found", "not_found"}
    result["passive_result"] = passive
    result["completion_blocked"] = not (save_verified and retrieve_allowed)
    if result["completion_blocked"]:
        result["active_agents"] = []
        result["deferred_agents"] = []
    return result


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def compact_worker_summary(agent: str, result: dict[str, Any]) -> dict[str, Any]:
    if agent == "0_Prompt_Agent":
        route = result.get("route", {})
        spec = route.get("task_spec", {})
        return {
            "status": result.get("status"),
            "mode": route.get("mode"),
            "task_revision": spec.get("revision"),
            "task_spec_digest": task_spec_digest(spec) if isinstance(spec, dict) else None,
            "active_agents": route.get("active_agents", []),
            "passive_elements": route.get("passive_elements", []),
        }
    if agent == "1_Passive_Agent":
        contexts = result.get("contexts", [])
        return {
            "status": result.get("status"),
            "elements": [item.get("element") for item in contexts],
            "context_statuses": {item.get("element"): item.get("status") for item in contexts},
            "source_count": sum(len(item.get("source_paths", [])) for item in contexts),
        }
    if agent.startswith("active_agent_"):
        return {
            "status": result.get("status"),
            "stages": result.get("stages", []),
            "artifact": result.get("artifact"),
            "verification": result.get("verification"),
            "reason": result.get("reason"),
        }
    return {
        "status": result.get("status"),
        "decision": result.get("decision"),
        "score": result.get("score"),
        "reasons": result.get("reasons", []),
    }


def invoke_agent_process(
    agent: str,
    payload: dict[str, Any],
    *,
    state_path: Path,
    events_path: Path,
    one_time_token: str | None,
    sequence: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one role in a distinct process and retain only compact evidence."""
    invocation_id = f"inv-{sequence:02d}-{secrets.token_hex(8)}"
    started_at = datetime.now(KST).isoformat(timespec="milliseconds")
    encoded_input = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(Path(__file__).resolve()),
        "--state-path",
        str(state_path.resolve()),
        "worker",
        "--agent",
        agent,
    ]
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    if one_time_token is not None:
        environment["HARNESS_ONE_TIME_TOKEN"] = one_time_token
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
        shell=False,
    )
    try:
        stdout, stderr = process.communicate(encoded_input.decode("utf-8"), timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        stderr = (stderr or "") + "\nworker_timeout"
    ended_at = datetime.now(KST).isoformat(timespec="milliseconds")
    try:
        result = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        result = {"status": "failed", "reason": "worker_output_not_json"}
    if process.returncode != 0:
        result = {
            "status": "failed",
            "reason": result.get("reason") or "worker_process_failed",
            "worker_error_digest": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        }
    summary = compact_worker_summary(agent, result)
    receipt = {
        "sequence": sequence,
        "agent": agent,
        "invocation_id": invocation_id,
        "process_id": process.pid,
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": process.returncode,
        "input_digest": hashlib.sha256(encoded_input).hexdigest(),
        "output_digest": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "status": result.get("status", "failed"),
        "output_summary": summary,
    }
    append_jsonl(events_path, receipt)
    return result, receipt


def passive_query_text(element: str) -> str:
    return {
        "direction": "업무 방향과 범위 기록을 조회해줘",
        "dev_env": "서버와 개발 환경 및 경로 기록을 조회해줘",
        "feedback": "사용자 피드백 기록을 조회해줘",
        "user_info": "사용자와 담당자 특성 기록을 조회해줘",
        "record": "관련 기록을 조회해줘",
    }.get(element, "관련 기록을 조회해줘")


def run_active_fixture_worker(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    task_spec = payload.get("task_spec")
    if not isinstance(task_spec, dict):
        return {"status": "failed", "reason": "task_spec_required"}
    expected_epoch = payload.get("epoch")
    if type(expected_epoch) is not int:
        return {"status": "failed", "reason": "epoch_required"}
    token = os.environ.get("HARNESS_ONE_TIME_TOKEN")
    state_path = Path(payload["state_path"])
    state = load_json(state_path)
    authorization_reason = execution_authorization_reason(
        state,
        expected_epoch,
        one_time_token=token,
        expected_task_revision=task_spec.get("revision"),
        task_spec=task_spec,
    )
    if authorization_reason:
        return {"status": "failed", "reason": authorization_reason}
    contract = task_spec.get("execution_contract", {})
    if agent not in contract.get("active_agents_required", []):
        return {"status": "failed", "reason": "active_agent_not_selected"}
    fixture_source_value = payload.get("fixture_source")
    if not isinstance(fixture_source_value, str):
        return {"status": "failed", "reason": "local_fixture_source_required"}
    source = Path(fixture_source_value).resolve()
    if not source.is_file() or not path_is_within(source, ROOT):
        return {"status": "failed", "reason": "fixture_source_must_be_local_project_file"}
    content = source.read_text(encoding="utf-8")
    source_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stages = [
        {"stage": "preparation", "status": "completed", "evidence": source_digest},
    ]
    artifact: dict[str, Any] | None = None
    if agent == "active_agent_document":
        run_root = Path(payload["run_root"]).resolve()
        if not path_is_within(run_root, ROOT):
            return {"status": "failed", "reason": "run_root_outside_harness"}
        append_text = payload.get("fixture_append")
        if not isinstance(append_text, str) or not append_text.strip():
            return {"status": "failed", "reason": "document_fixture_append_required"}
        output = run_root / "artifacts" / "proteomics_qc_plan.md"
        rendered = content.rstrip() + "\n\n" + append_text.strip() + "\n"
        atomic_write_text(output, rendered)
        reread = output.read_text(encoding="utf-8")
        output_digest = hashlib.sha256(reread.encode("utf-8")).hexdigest()
        if reread != rendered:
            return {"status": "failed", "reason": "artifact_reread_mismatch"}
        artifact = {"path": str(output), "sha256": output_digest, "verified": True}
        stages.append({"stage": "execution", "status": "completed", "evidence": output_digest})
        stages.append({"stage": "verification", "status": "completed", "evidence": "reread_match"})
    elif agent in {"active_agent_analysis", "active_agent_inspection"}:
        stages.append(
            {
                "stage": "execution",
                "status": "completed",
                "evidence": {"line_count": len(content.splitlines()), "byte_count": len(content.encode("utf-8"))},
            }
        )
        stages.append({"stage": "verification", "status": "completed", "evidence": source_digest})
    else:
        return {"status": "failed", "reason": "fixture_worker_not_implemented_for_selected_agent"}
    final_state = load_json(state_path)
    final_reason = execution_authorization_reason(
        final_state,
        expected_epoch,
        one_time_token=token,
        expected_task_revision=task_spec.get("revision"),
        task_spec=task_spec,
    )
    if final_reason:
        return {"status": "failed", "reason": final_reason}
    stages.append({"stage": "completion", "status": "completed", "evidence": "authorization_rechecked"})
    return {
        "status": "completed",
        "agent": agent,
        "stages": stages,
        "artifact": artifact,
        "verification": {"source_sha256": source_digest, "server_writes": [], "network_hosts": []},
    }


def run_agent_worker(agent: str, payload: dict[str, Any], state_path: Path) -> dict[str, Any]:
    if agent == "0_Prompt_Agent":
        route = route_text(
            str(payload.get("text", "")),
            now_kst(payload.get("now")),
            state_path,
            payload.get("prior_task_spec"),
        )
        status = "completed" if route.get("mode") in {"custom_agent_system", "custom_agent_system_one_time"} else "failed"
        return {"status": status, "route": route, "reason": None if status == "completed" else route.get("reason")}
    if agent == "1_Passive_Agent":
        task_spec = payload.get("task_spec")
        if not isinstance(task_spec, dict):
            return {"status": "failed", "reason": "task_spec_required"}
        token = os.environ.get("HARNESS_ONE_TIME_TOKEN")
        project = payload.get("project")
        if not isinstance(project, str):
            return {"status": "failed", "reason": "project_context_required"}
        contexts: list[dict[str, Any]] = []
        for element in payload.get("elements", []):
            if element == "record" and payload.get("record_action") == "save":
                item = execute_passive_record(
                    str(payload.get("text", "")),
                    project=project,
                    expected_epoch=payload["epoch"],
                    state_path=state_path,
                    memory_root=Path(payload["memory_root"]),
                    one_time_token=token,
                    expected_task_revision=task_spec.get("revision"),
                    task_spec=task_spec,
                )
            else:
                item = execute_passive_retrieve(
                    passive_query_text(str(element)),
                    project=project,
                    expected_epoch=payload["epoch"],
                    state_path=state_path,
                    memory_root=Path(payload["memory_root"]),
                    one_time_token=token,
                    expected_task_revision=task_spec.get("revision"),
                    task_spec=task_spec,
                )
            contexts.append(
                {
                    "element": element,
                    "status": item.get("status"),
                    "answer": item.get("answer"),
                    "source_paths": item.get("source_paths", []),
                    "reason": item.get("reason"),
                }
            )
        required = task_spec.get("execution_contract", {}).get("required_passive_elements", [])
        missing = [
            element
            for element in required
            if not any(item["element"] == element and item["status"] in {"found", "saved", "already_saved", "repaired"} for item in contexts)
        ]
        final_state = load_json(state_path)
        final_reason = execution_authorization_reason(
            final_state,
            payload["epoch"],
            one_time_token=token,
            expected_task_revision=task_spec.get("revision"),
            task_spec=task_spec,
        )
        if final_reason:
            return {"status": "failed", "reason": final_reason, "contexts": contexts}
        return {
            "status": "completed" if not missing else "failed",
            "contexts": contexts,
            "missing_required_elements": missing,
            "reason": None if not missing else "required_passive_context_missing",
        }
    if agent.startswith("active_agent_"):
        return run_active_fixture_worker(agent, payload)
    if agent == "S_Supervisor_Agent":
        task_spec = payload.get("task_spec")
        receipts = payload.get("receipts", [])
        contract = task_spec.get("execution_contract", {}) if isinstance(task_spec, dict) else {}
        reasons: list[str] = []
        token = os.environ.get("HARNESS_ONE_TIME_TOKEN")
        state = load_json(state_path)
        authorization_reason = execution_authorization_reason(
            state,
            payload.get("epoch"),
            one_time_token=token,
            expected_task_revision=task_spec.get("revision") if isinstance(task_spec, dict) else None,
            task_spec=task_spec if isinstance(task_spec, dict) else None,
        )
        if authorization_reason:
            reasons.append(authorization_reason)
        agents = [item.get("agent") for item in receipts]
        expected = ["0_Prompt_Agent", "1_Passive_Agent", *contract.get("active_agents_required", [])]
        if agents != expected:
            reasons.append("execution_order_or_agents_mismatch")
        if any(item.get("status") != "completed" for item in receipts):
            reasons.append("prior_agent_failed")
        if len({item.get("invocation_id") for item in receipts}) != len(receipts):
            reasons.append("invocation_ids_not_unique")
        if len({item.get("process_id") for item in receipts}) != len(receipts):
            reasons.append("agent_processes_not_independent")
        if [item.get("sequence") for item in receipts] != list(range(1, len(receipts) + 1)):
            reasons.append("execution_sequence_invalid")
        if any(
            item.get("exit_code") != 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("input_digest", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("output_digest", "")))
            for item in receipts
        ):
            reasons.append("receipt_integrity_invalid")
        passive = next((item for item in receipts if item.get("agent") == "1_Passive_Agent"), None)
        passive_elements = (passive or {}).get("output_summary", {}).get("elements", [])
        if not set(contract.get("required_passive_elements", [])) <= set(passive_elements):
            reasons.append("required_passive_context_not_used")
        passive_statuses = (passive or {}).get("output_summary", {}).get("context_statuses", {})
        if any(
            passive_statuses.get(element) not in {"found", "saved", "already_saved", "repaired"}
            for element in contract.get("required_passive_elements", [])
        ):
            reasons.append("required_passive_context_unproven")
        for item in receipts:
            if str(item.get("agent", "")).startswith("active_agent_"):
                stages = item.get("output_summary", {}).get("stages", [])
                if [stage.get("stage") for stage in stages] != ["preparation", "execution", "verification", "completion"]:
                    reasons.append(f"active_stage_evidence_missing:{item.get('agent')}")
                elif any(
                    stage.get("status") != "completed"
                    or stage.get("evidence") is None
                    or stage.get("evidence") == ""
                    for stage in stages
                ):
                    reasons.append(f"active_stage_failed_or_unproven:{item.get('agent')}")
        safety = payload.get("safety", {})
        if safety.get("reasons") or safety.get("server_write_attempted"):
            reasons.append("safety_gate_failed")
        if any(host != "github.com" and not str(host).endswith(".github.com") for host in safety.get("network_hosts", [])):
            reasons.append("non_github_network_detected")
        return {
            "status": "completed" if not reasons else "failed",
            "decision": "PASS" if not reasons else "FAIL",
            "score": 96 if not reasons else 0,
            "reasons": reasons,
            "independent_process": True,
            "server_access": "read_only",
            "network_policy": "github_only",
        }
    return {"status": "failed", "reason": "unknown_worker_agent"}


def execution_evidence_signature(evidence: dict[str, Any], token: str) -> str:
    """Bind a one-time execution receipt to its unlogged authorization token."""
    unsigned = dict(evidence)
    unsigned.pop("signature", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(token.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def parse_evidence_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _register_runtime_attestation(
    attestation: str,
    evidence_path: Path,
    task_spec: dict[str, Any],
) -> None:
    """Register one process-local, single-use proof created by run_task_chain."""
    key = hashlib.sha256(attestation.encode("utf-8")).hexdigest()
    record = {
        "evidence_path": str(evidence_path.resolve()),
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "task_spec_digest": task_spec_digest(task_spec),
        "orchestrator_pid": os.getpid(),
    }
    with _RUNTIME_ATTESTATIONS_GUARD:
        _RUNTIME_ATTESTATIONS[key] = record


def _claim_runtime_attestation(
    attestation: str | None,
    evidence_path: Path,
    task_spec: dict[str, Any],
) -> str | None:
    """Consume the exact proof; fabricated/replayed evidence has no live claim."""
    if not isinstance(attestation, str) or not attestation:
        return "execution_runtime_attestation_required"
    key = hashlib.sha256(attestation.encode("utf-8")).hexdigest()
    with _RUNTIME_ATTESTATIONS_GUARD:
        record = _RUNTIME_ATTESTATIONS.pop(key, None)
    if record is None:
        return "execution_runtime_attestation_invalid_or_replayed"
    try:
        actual_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    except OSError:
        return "execution_runtime_attestation_evidence_unreadable"
    expected = {
        "evidence_path": str(evidence_path.resolve()),
        "evidence_sha256": actual_sha256,
        "task_spec_digest": task_spec_digest(task_spec),
        "orchestrator_pid": os.getpid(),
    }
    if record != expected:
        return "execution_runtime_attestation_mismatch"
    if not hmac.compare_digest(
        str(load_json(evidence_path).get("runtime_attestation_digest", "")),
        key,
    ):
        return "execution_runtime_attestation_digest_mismatch"
    return None


def validate_execution_evidence(
    task_spec: dict[str, Any],
    evidence_path: Path,
    *,
    one_time_token: str | None = None,
    runtime_attestation: str | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        evidence = load_json(evidence_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"allowed": False, "reasons": ["execution_evidence_unreadable"]}
    contract = task_spec.get("execution_contract", {})
    attestation_reason = _claim_runtime_attestation(
        runtime_attestation,
        evidence_path,
        task_spec,
    )
    if attestation_reason:
        reasons.append(attestation_reason)
    if evidence.get("schema_version") != 1:
        reasons.append("execution_evidence_schema_invalid")
    if evidence.get("task_revision") != task_spec.get("revision"):
        reasons.append("execution_evidence_task_revision_mismatch")
    if evidence.get("task_spec_digest") != task_spec_digest(task_spec):
        reasons.append("execution_evidence_task_spec_mismatch")
    created_at = parse_evidence_time(evidence.get("created_at"))
    if created_at is None:
        reasons.append("execution_evidence_created_at_invalid")
    receipts = evidence.get("invocations")
    if not isinstance(receipts, list):
        receipts = []
        reasons.append("execution_invocations_missing")
    expected_agents = [
        "0_Prompt_Agent",
        "1_Passive_Agent",
        *contract.get("active_agents_required", []),
        "S_Supervisor_Agent",
    ]
    actual_agents = [item.get("agent") for item in receipts if isinstance(item, dict)]
    if actual_agents != expected_agents:
        reasons.append("execution_chain_order_mismatch")
    if [item.get("sequence") for item in receipts if isinstance(item, dict)] != list(
        range(1, len(expected_agents) + 1)
    ):
        reasons.append("execution_sequence_invalid")
    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    invocation_pattern = re.compile(r"^inv-\d{2}-[0-9a-f]{16}$")
    previous_end: datetime | None = None
    for item in receipts:
        if not isinstance(item, dict):
            reasons.append("execution_receipt_invalid")
            continue
        if item.get("status") != "completed" or item.get("exit_code") != 0:
            reasons.append("execution_chain_contains_failure")
        if not isinstance(item.get("invocation_id"), str) or not invocation_pattern.fullmatch(item["invocation_id"]):
            reasons.append("execution_invocation_id_format_invalid")
        if type(item.get("process_id")) is not int or item["process_id"] <= 0:
            reasons.append("execution_process_id_invalid")
        if not digest_pattern.fullmatch(str(item.get("input_digest", ""))) or not digest_pattern.fullmatch(
            str(item.get("output_digest", ""))
        ):
            reasons.append("execution_digest_invalid")
        started_at = parse_evidence_time(item.get("started_at"))
        ended_at = parse_evidence_time(item.get("ended_at"))
        if started_at is None or ended_at is None or started_at > ended_at:
            reasons.append("execution_timestamp_invalid")
        elif previous_end is not None and started_at < previous_end:
            reasons.append("execution_timestamp_order_invalid")
        if ended_at is not None:
            previous_end = ended_at
    invocation_ids = [item.get("invocation_id") for item in receipts if isinstance(item, dict)]
    process_ids = [item.get("process_id") for item in receipts if isinstance(item, dict)]
    if len(invocation_ids) != len(expected_agents) or len(set(invocation_ids)) != len(expected_agents):
        reasons.append("execution_invocation_ids_invalid")
    if len(process_ids) != len(expected_agents) or len(set(process_ids)) != len(expected_agents):
        reasons.append("execution_processes_not_independent")
    passive = next((item for item in receipts if item.get("agent") == "1_Passive_Agent"), {})
    passive_summary = passive.get("output_summary", {})
    passive_elements = passive_summary.get("elements", [])
    if not set(contract.get("required_passive_elements", [])) <= set(passive_elements):
        reasons.append("execution_passive_context_missing")
    context_statuses = passive_summary.get("context_statuses", {})
    for element in contract.get("required_passive_elements", []):
        if context_statuses.get(element) not in {"found", "saved", "already_saved", "repaired"}:
            reasons.append(f"execution_passive_context_unproven:{element}")
    executed_active = [agent for agent in actual_agents if str(agent).startswith("active_agent_")]
    if executed_active != contract.get("active_agents_required", []):
        reasons.append("execution_active_agents_mismatch")
    for item in receipts:
        if not str(item.get("agent", "")).startswith("active_agent_"):
            continue
        summary = item.get("output_summary", {})
        stages = summary.get("stages", [])
        if [stage.get("stage") for stage in stages if isinstance(stage, dict)] != [
            "preparation",
            "execution",
            "verification",
            "completion",
        ]:
            reasons.append(f"execution_active_stage_order_invalid:{item.get('agent')}")
        for stage in stages:
            if (
                not isinstance(stage, dict)
                or stage.get("status") != "completed"
                or stage.get("evidence") is None
                or stage.get("evidence") == ""
            ):
                reasons.append(f"execution_active_stage_unproven:{item.get('agent')}")
                break
        verification = summary.get("verification")
        if not isinstance(verification, dict):
            reasons.append(f"execution_active_verification_missing:{item.get('agent')}")
        else:
            if verification.get("server_writes") not in (None, []):
                reasons.append(f"execution_active_server_write_detected:{item.get('agent')}")
            hosts = verification.get("network_hosts", [])
            if any(host != "github.com" and not str(host).endswith(".github.com") for host in hosts):
                reasons.append(f"execution_active_non_github_network:{item.get('agent')}")
        artifact = summary.get("artifact")
        if artifact is not None:
            artifact_path = Path(str(artifact.get("path", "")))
            artifact_digest = str(artifact.get("sha256", ""))
            try:
                artifact_actual_digest = (
                    hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                    if artifact_path.is_file()
                    else None
                )
            except OSError:
                artifact_actual_digest = None
            if (
                artifact.get("verified") is not True
                or not path_is_within(artifact_path, ROOT)
                or not artifact_path.is_file()
                or not digest_pattern.fullmatch(artifact_digest)
                or artifact_actual_digest != artifact_digest
            ):
                reasons.append(f"execution_artifact_unverified:{item.get('agent')}")
    supervisor = receipts[-1].get("output_summary", {}) if receipts else {}
    supervisor_score = supervisor.get("score")
    if (
        supervisor.get("status") != "completed"
        or supervisor.get("decision") != "PASS"
        or type(supervisor_score) is not int
        or not contract.get("supervisor_score_min", 90) <= supervisor_score <= 100
        or supervisor.get("reasons") not in (None, [])
    ):
        reasons.append("execution_supervisor_not_passed")
    safety = evidence.get("safety", {})
    if (
        safety.get("server_access") != "read_only"
        or safety.get("server_write_attempted") is not False
        or safety.get("reasons") not in (None, [])
    ):
        reasons.append("execution_server_read_only_not_proven")
    if safety.get("network_policy") != "github_only":
        reasons.append("execution_network_policy_not_proven")
    if any(host != "github.com" and not str(host).endswith(".github.com") for host in safety.get("network_hosts", [])):
        reasons.append("execution_non_github_network_used")
    events_path = evidence_path.parent / "events.jsonl"
    try:
        events_bytes = events_path.read_bytes()
        events = [json.loads(line) for line in events_bytes.decode("utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        events = []
        events_bytes = b""
        reasons.append("execution_events_unreadable")
    if events != receipts:
        reasons.append("execution_events_receipts_mismatch")
    if evidence.get("events_sha256") != hashlib.sha256(events_bytes).hexdigest():
        reasons.append("execution_events_digest_mismatch")
    if one_time_token is not None:
        supplied_signature = evidence.get("signature")
        expected_signature = execution_evidence_signature(evidence, one_time_token)
        if not isinstance(supplied_signature, str) or not hmac.compare_digest(
            supplied_signature,
            expected_signature,
        ):
            reasons.append("execution_evidence_signature_invalid")
    return {"allowed": not reasons, "reasons": list(dict.fromkeys(reasons)), "evidence": evidence}


def revoke_one_time_grant(state_path: Path, token: str | None) -> None:
    if token is None:
        return
    with state_write_lock(state_path):
        state = load_json(state_path)
        grant = state.get("one_time_grant")
        if isinstance(grant, dict) and secrets.compare_digest(
            str(grant.get("token_hash", "")), one_time_token_digest(token)
        ):
            state["one_time_grant"] = None
            state["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
            atomic_write_json(state_path, state)


def run_task_chain(
    text: str,
    current: datetime,
    *,
    project: str,
    state_path: Path = DEFAULT_STATE_PATH,
    memory_root: Path = DEFAULT_PASSIVE_MEMORY_ROOT,
    run_root: Path | None = None,
    fixture_source: Path | None = None,
    fixture_append: str | None = None,
) -> dict[str, Any]:
    """Execute Prompt -> Passive -> Active -> Supervisor as separate local processes."""
    safety = runtime_safety_gate(text)
    if not safety["allowed"]:
        return {"status": "blocked", "reason": "safety_gate_failed", "safety": safety}
    resolved_run_root = (run_root or (DEFAULT_RUNTIME_ROOT / f"run-{datetime.now(KST):%Y%m%dT%H%M%S}-{secrets.token_hex(4)}")).resolve()
    if not path_is_within(resolved_run_root, ROOT):
        return {"status": "blocked", "reason": "run_root_outside_harness", "safety": safety}
    resolved_run_root.mkdir(parents=True, exist_ok=False)
    events_path = resolved_run_root / "events.jsonl"
    receipts: list[dict[str, Any]] = []
    token: str | None = None
    sequence = 1
    try:
        prompt_result, prompt_receipt = invoke_agent_process(
            "0_Prompt_Agent",
            {"text": text, "now": current.isoformat()},
            state_path=state_path,
            events_path=events_path,
            one_time_token=None,
            sequence=sequence,
        )
        receipts.append(prompt_receipt)
        route = prompt_result.get("route", {})
        token = route.get("one_time_token")
        task_spec = route.get("task_spec")
        if prompt_result.get("status") != "completed" or not isinstance(task_spec, dict):
            raise ValueError(prompt_result.get("reason") or "prompt_worker_failed")
        contract = task_spec.get("execution_contract", {})
        if not contract.get("required"):
            raise ValueError("active_agent_required_for_task_chain")
        sequence += 1
        passive_result_value, passive_receipt = invoke_agent_process(
            "1_Passive_Agent",
            {
                "text": text,
                "project": project,
                "epoch": route["epoch"],
                "task_spec": task_spec,
                "elements": contract.get("required_passive_elements", []),
                "record_action": route.get("record_action"),
                "memory_root": str(memory_root.resolve()),
                "state_path": str(state_path.resolve()),
            },
            state_path=state_path,
            events_path=events_path,
            one_time_token=token,
            sequence=sequence,
        )
        receipts.append(passive_receipt)
        if passive_result_value.get("status") != "completed":
            raise ValueError(passive_result_value.get("reason") or "passive_worker_failed")
        enriched_task_spec = dict(task_spec)
        enriched_task_spec["passive_context"] = passive_result_value.get("contexts", [])
        for agent in contract.get("active_agents_required", []):
            sequence += 1
            active_result, active_receipt = invoke_agent_process(
                agent,
                {
                    "epoch": route["epoch"],
                    "task_spec": task_spec,
                    "enriched_task_spec": enriched_task_spec,
                    "state_path": str(state_path.resolve()),
                    "run_root": str(resolved_run_root),
                    "fixture_source": str(fixture_source.resolve()) if fixture_source else None,
                    "fixture_append": fixture_append,
                    "prior_receipts": receipts,
                },
                state_path=state_path,
                events_path=events_path,
                one_time_token=token,
                sequence=sequence,
            )
            receipts.append(active_receipt)
            if active_result.get("status") != "completed":
                raise ValueError(active_result.get("reason") or f"{agent}_failed")
        sequence += 1
        supervisor_result, supervisor_receipt = invoke_agent_process(
            "S_Supervisor_Agent",
            {"epoch": route["epoch"], "task_spec": task_spec, "receipts": receipts, "safety": safety},
            state_path=state_path,
            events_path=events_path,
            one_time_token=token,
            sequence=sequence,
        )
        receipts.append(supervisor_receipt)
        if supervisor_result.get("decision") != "PASS":
            raise ValueError("supervisor_rejected_execution")
        evidence_path = resolved_run_root / "workflow_evidence.json"
        runtime_attestation = secrets.token_urlsafe(32)
        evidence = {
            "schema_version": 1,
            "run_id": resolved_run_root.name,
            "task_revision": task_spec.get("revision"),
            "task_spec_digest": task_spec_digest(task_spec),
            "invocations": receipts,
            "safety": safety,
            "events_sha256": hashlib.sha256(events_path.read_bytes()).hexdigest(),
            "runtime_attestation_digest": hashlib.sha256(
                runtime_attestation.encode("utf-8")
            ).hexdigest(),
            "created_at": datetime.now(KST).isoformat(timespec="seconds"),
        }
        if token is not None:
            evidence["signature"] = execution_evidence_signature(evidence, token)
        atomic_write_json(evidence_path, evidence)
        _register_runtime_attestation(runtime_attestation, evidence_path, task_spec)
        gate = pre_output_gate(
            route["epoch"],
            state_path,
            expected_task_revision=task_spec.get("revision"),
            current_task_spec=task_spec,
            output_text="적용 완료. 검증 결과를 기록했습니다.",
            one_time_token=token,
            execution_evidence_path=evidence_path,
            runtime_attestation=runtime_attestation,
        )
        if not gate["allowed"]:
            raise ValueError("pre_output_gate_rejected:" + ",".join(gate["reasons"]))
        return {
            "status": "completed",
            "mode": route.get("mode"),
            "run_id": resolved_run_root.name,
            "task_spec": task_spec,
            "execution_truth": {
                "active_agents_selected_only": route.get("active_agents", []),
                "active_agents_executed": contract.get("active_agents_required", []),
                "runtime_execution_claimed": True,
            },
            "invocation_order": [item["agent"] for item in receipts],
            "supervisor": compact_worker_summary("S_Supervisor_Agent", supervisor_result),
            "pre_output_gate": gate,
            "evidence_path": str(evidence_path),
            "safety": safety,
        }
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        revoke_one_time_grant(state_path, token)
        return {
            "status": "failed",
            "reason": str(exc),
            "invocation_order": [item["agent"] for item in receipts],
            "evidence_path": str(events_path),
            "safety": safety,
        }


def response_output_gate(
    task_spec: dict[str, Any],
    output_text: str,
    previous_output_text: str | None = None,
) -> dict[str, Any]:
    """Enforce concise explanation and no-repeat constraints before reporting."""
    contract = task_spec.get("response_contract", {})
    reasons: list[str] = []
    if contract.get("mode") == "explanation":
        current = normalize(output_text)
        previous = normalize(previous_output_text or "")
        canonical_current = re.sub(r"[^0-9a-z가-힣]+", "", re.sub(r"이며", "이고", current))
        canonical_previous = re.sub(r"[^0-9a-z가-힣]+", "", re.sub(r"이며", "이고", previous))
        if canonical_previous and canonical_current == canonical_previous:
            reasons.append("repeated_prior_explanation")
        max_sentences = contract.get("max_sentences")
        if type(max_sentences) is int and max_sentences > 0:
            sentences = [
                item.strip()
                for item in re.split(r"(?<=[.!?])\s+|[\r\n]+", output_text.strip())
                if item.strip()
            ]
            if len(sentences) > max_sentences:
                reasons.append("response_exceeds_sentence_limit")
    consistency = task_spec.get("report_consistency", {})
    if consistency.get("inventory_request"):
        status_patterns = {
            "current_implementation": (
                r"(?:현재\s*(?:구현|코드|오류)|구현\s*현황|지금\s*쓰는\s*(?:건|것)|"
                r"현행|운영\s*중|실사용\s*코드)"
            ),
            "proposed_change": (
                r"(?:제안|후보|권장|변경\s*필요|앞으로|추후|다음에|(?:제거|삭제|수정)\s*필요|"
                r"(?:빼|없애)는\s*게\s*(?:낫|좋))"
            ),
            "applied_change": (
                r"(?:(?:적용|제거|삭제|수정|구현|반영).{0,6}완료|"
                r"이미\s*(?:적용|제거|삭제|수정|반영)|반영해\s*둔|반영\s*끝|"
                r"고쳐\s*둔|조치됨|벌써\s*반영)"
            ),
        }
        output_statuses = {
            name for name, pattern in status_patterns.items() if re.search(pattern, output_text)
        }
        if len(output_statuses) > 1:
            reasons.append("mixed_report_claim_statuses")
    if (
        consistency.get("baseline_consistency")
        == "compare_previous_output_or_passive_baseline"
        and previous_output_text
    ):
        inventory_pattern = (
            r"(?<![A-Z0-9_-])"
            r"([A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+|[A-Z]{1,8}\d{2,})"
            r"(?![A-Z0-9_-])"
        )
        previous_inventory = set(re.findall(inventory_pattern, previous_output_text))
        current_inventory = set(re.findall(inventory_pattern, output_text))
        if re.search(
            r"(?:현재\s*)?(?:오류|에러)\s*코드(?:는|가|가\s*현재)?\s*"
            r"(?:없|0\s*(?:개|건)|전부\s*(?:제거|삭제|폐기)|모두\s*(?:제거|삭제|폐기))|"
            r"(?:폐기|제거|삭제|정리).{0,40}남은\s*(?:오류\s*코드|에러\s*코드|오류\s*항목|에러\s*항목|항목)"
            r"(?:은|는|이|가)?\s*(?:0\s*(?:개|건)|하나도\s*남지\s*않)|"
            r"(?:오류|에러)\s*(?:코드|항목|식별자)(?:은|는|이|가)?\s*"
            r"(?:(?:하나도|전혀)\s*남지\s*않|0\s*(?:개|건).{0,12}(?:비어|없))",
            output_text,
        ):
            current_inventory = set()
        count_pattern = (
            r"(?:오류|에러)\s*코드(?:\s*(?:개수|수))?\s*"
            r"(?:[:：]|은|는|가|만)?\s*(?:총|모두)?\s*"
            r"(\d+|한|하나|두|둘|세|셋|네|넷|다섯|여섯|일곱|여덟|아홉|열)"
            r"(?:\s*(?:개|건|종류))?"
        )
        count_aliases = {
            "한": "1", "하나": "1", "두": "2", "둘": "2",
            "세": "3", "셋": "3", "네": "4", "넷": "4",
            "다섯": "5", "여섯": "6", "일곱": "7", "여덟": "8",
            "아홉": "9", "열": "10",
        }
        previous_counts = {
            count_aliases.get(item, item) for item in re.findall(count_pattern, previous_output_text)
        }
        current_counts = {
            count_aliases.get(item, item) for item in re.findall(count_pattern, output_text)
        }
        inventory_changed = previous_inventory != current_inventory and bool(
            previous_inventory or current_inventory
        )
        count_changed = previous_counts != current_counts and bool(previous_counts or current_counts)
        if inventory_changed or count_changed:
            change_note = re.search(r"(?:변경|추가|제거|수정|달라진|차이)", output_text)
            evidence_note = re.search(
                r"(?:현재\s*(?:코드|구현)|소스|검색|근거|런타임|실행\s*결과|테스트|diff|파일)",
                output_text,
                flags=re.IGNORECASE,
            )
            if not (change_note and evidence_note):
                reasons.append("report_inventory_changed_without_evidence")
    return {"allowed": not reasons, "reasons": reasons}


def pre_output_gate(
    expected_epoch: int,
    state_path: Path = DEFAULT_STATE_PATH,
    *,
    expected_task_revision: int | None = None,
    current_task_spec: dict[str, Any] | None = None,
    output_text: str | None = None,
    previous_output_text: str | None = None,
    one_time_token: str | None = None,
    execution_evidence_path: Path | None = None,
    runtime_attestation: str | None = None,
) -> dict[str, Any]:
    """Re-read state and reject stale or contract-breaking Agent output."""
    with state_write_lock(state_path):
        state = load_json(state_path)
        reasons: list[str] = []
        one_time_attempt = state.get("status") == "INVALID" and one_time_token is not None
        if one_time_attempt:
            grant_reason = one_time_grant_reason(
                state,
                one_time_token,
                expected_epoch,
                expected_task_revision=expected_task_revision,
                task_spec=current_task_spec,
            )
            if grant_reason:
                reasons.append(grant_reason)
        else:
            if state.get("status") != "ACTIVE":
                reasons.append("system_not_active")
            if state.get("epoch") != expected_epoch:
                reasons.append("epoch_changed")
        actual_task_revision = None
        if expected_task_revision is not None:
            if current_task_spec is None:
                reasons.append("current_task_spec_required")
            else:
                actual_task_revision = current_task_spec.get("revision")
                if actual_task_revision != expected_task_revision:
                    reasons.append("task_revision_superseded")
        if output_text is not None and current_task_spec is not None:
            response_gate = response_output_gate(current_task_spec, output_text, previous_output_text)
            reasons.extend(response_gate["reasons"])
        execution_contract = (
            current_task_spec.get("execution_contract", {})
            if isinstance(current_task_spec, dict)
            else {}
        )
        execution_check: dict[str, Any] | None = None
        if execution_contract.get("required"):
            if execution_evidence_path is None:
                reasons.append("runtime_execution_evidence_required")
            else:
                execution_check = validate_execution_evidence(
                    current_task_spec,
                    execution_evidence_path,
                    one_time_token=one_time_token,
                    runtime_attestation=runtime_attestation,
                )
                reasons.extend(execution_check["reasons"])
        reasons = list(dict.fromkeys(reasons))
        allowed = not reasons
        grant_consumed = False
        if allowed and one_time_attempt:
            state["one_time_grant"] = None
            state["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
            atomic_write_json(state_path, state)
            grant_consumed = True
        return {
            "allowed": allowed,
            "status": state.get("status"),
            "expected_epoch": expected_epoch,
            "actual_epoch": state.get("epoch"),
            "expected_task_revision": expected_task_revision,
            "actual_task_revision": actual_task_revision,
            "authorization": "one_time_grant" if one_time_attempt else "active_epoch",
            "grant_consumed": grant_consumed,
            "execution_evidence": (
                {
                    "path": str(execution_evidence_path.resolve()),
                    "verified": bool(execution_check and execution_check["allowed"]),
                }
                if execution_evidence_path is not None
                else None
            ),
            "reasons": reasons,
        }


def load_supervisor_evidence(state_path: Path, evidence_path: Path, state: dict[str, Any]) -> dict[str, Any]:
    evidence_root = (state_path.parent / "supervisor_evidence").resolve()
    resolved = evidence_path.resolve()
    if resolved.parent != evidence_root:
        raise ValueError("Supervisor evidence must be in system/supervisor_evidence")
    evidence = load_json(resolved)
    required = {
        "build_id",
        "supervisor_agent",
        "independent",
        "supervisor_score",
        "paperthin_philosophy_score",
        "validator_pass",
        "evaluated_at",
        "candidate_digest",
    }
    if set(evidence) != required:
        raise ValueError("Supervisor evidence schema is invalid")
    if evidence["build_id"] != state.get("candidate_build_id"):
        raise ValueError("Supervisor evidence build_id does not match candidate")
    if evidence["supervisor_agent"] != "S_Supervisor_Agent" or evidence["independent"] is not True:
        raise ValueError("independent Supervisor evidence is required")
    if evidence["validator_pass"] is not True:
        raise ValueError("validator evidence must pass")
    supervisor_score = evidence["supervisor_score"]
    philosophy_score = evidence["paperthin_philosophy_score"]
    if type(supervisor_score) is not int or not 0 <= supervisor_score <= 100:
        raise ValueError("Supervisor score must be an integer from 0 to 100")
    if type(philosophy_score) is not int or not 0 <= philosophy_score <= 100:
        raise ValueError("Paperthin and user philosophy score must be an integer from 0 to 100")
    try:
        evaluated_at = datetime.fromisoformat(evidence["evaluated_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluated_at must be an ISO-8601 datetime") from exc
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must include a timezone")
    candidate_at = datetime.fromisoformat(state["updated_at"])
    if candidate_at.tzinfo is None:
        candidate_at = candidate_at.replace(tzinfo=KST)
    current = datetime.now(KST)
    if evaluated_at < candidate_at or evaluated_at > current + timedelta(minutes=5):
        raise ValueError("Supervisor evidence is stale or future-dated")
    current_digest = candidate_content_digest()
    if evidence["candidate_digest"] != state.get("candidate_digest") or evidence["candidate_digest"] != current_digest:
        raise ValueError("Supervisor evidence does not match candidate content")
    return evidence


def _transition_without_lock(
    action: str,
    state_path: Path,
    build_id: str | None = None,
    evidence_path: Path | None = None,
) -> dict[str, Any]:
    state = load_json(state_path)
    timestamp = datetime.now(KST).isoformat(timespec="seconds")
    if action == "begin-rebuild":
        if state.get("status") != "INVALID":
            raise ValueError("begin-rebuild requires INVALID")
        if not isinstance(build_id, str) or not build_id.strip():
            raise ValueError("begin-rebuild requires a non-empty build_id")
        build_id = build_id.strip()
        state.update(
            status="REBUILDING",
            candidate_build_id=build_id,
            candidate_digest=None,
            supervisor_score=None,
            paperthin_philosophy_score=None,
            one_time_grant=None,
            updated_at=timestamp,
        )
    elif action == "candidate":
        if state.get("status") != "REBUILDING":
            raise ValueError("candidate requires REBUILDING")
        candidate_build_id = build_id or state.get("candidate_build_id")
        if not isinstance(candidate_build_id, str) or not candidate_build_id.strip():
            raise ValueError("candidate requires a non-empty build_id")
        state.update(
            status="CANDIDATE",
            candidate_build_id=candidate_build_id.strip(),
            candidate_digest=candidate_content_digest(),
            updated_at=timestamp,
        )
    elif action == "activate":
        if state.get("status") != "CANDIDATE":
            raise ValueError("activate requires CANDIDATE")
        if not isinstance(state.get("candidate_build_id"), str) or not state["candidate_build_id"].strip():
            raise ValueError("activate requires a non-empty candidate build_id")
        if evidence_path is None:
            raise ValueError("independent Supervisor evidence file is required")
        evidence = load_supervisor_evidence(state_path, evidence_path, state)
        supervisor_score = evidence["supervisor_score"]
        philosophy_score = evidence["paperthin_philosophy_score"]
        if supervisor_score < 90:
            raise ValueError("independent Supervisor score must be at least 90")
        if philosophy_score < 80:
            raise ValueError("Paperthin and user philosophy score must be at least 80")
        latest = load_json(state_path)
        protected_fields = ("status", "epoch", "candidate_build_id", "candidate_digest", "updated_at")
        if any(latest.get(field) != state.get(field) for field in protected_fields):
            raise ValueError("system state changed during Supervisor activation gate")
        state.update(
            status="ACTIVE",
            epoch=int(state.get("epoch", 0)) + 1,
            active_build_id=state.get("candidate_build_id"),
            candidate_build_id=None,
            candidate_digest=None,
            invalidated_at=None,
            invalidation_reason=None,
            supervisor_score=supervisor_score,
            paperthin_philosophy_score=philosophy_score,
            supervisor_evidence=str(evidence_path.resolve()),
            one_time_grant=None,
            updated_at=timestamp,
        )
    else:
        raise ValueError(f"unknown action: {action}")
    atomic_write_json(state_path, state)
    return state


def transition(
    action: str,
    state_path: Path,
    build_id: str | None = None,
    evidence_path: Path | None = None,
) -> dict[str, Any]:
    with state_write_lock(state_path):
        return _transition_without_lock(action, state_path, build_id, evidence_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    route = sub.add_parser("route")
    route.add_argument("--text")
    route.add_argument("--now")
    route.add_argument("--prior-task-spec")
    route.add_argument("--one-time-token")
    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--text")
    dispatch.add_argument("--now")
    dispatch.add_argument("--project")
    dispatch.add_argument("--prior-task-spec")
    dispatch.add_argument("--one-time-token")
    execute = sub.add_parser("execute")
    execute.add_argument("--text")
    execute.add_argument("--now")
    execute.add_argument("--project", required=True)
    execute.add_argument("--memory-root", type=Path, default=DEFAULT_PASSIVE_MEMORY_ROOT)
    execute.add_argument("--run-root", type=Path)
    execute.add_argument("--fixture-source", type=Path)
    execute.add_argument("--fixture-append")
    worker = sub.add_parser("worker")
    worker.add_argument("--agent", required=True)

    sub.add_parser("state")
    output_gate = sub.add_parser("pre-output-gate")
    output_gate.add_argument("--expected-epoch", type=int, required=True)
    output_gate.add_argument("--expected-task-revision", type=int)
    output_gate.add_argument("--current-task-spec")
    output_gate.add_argument("--output-text")
    output_gate.add_argument("--previous-output-text")
    output_gate.add_argument("--one-time-token")
    output_gate.add_argument("--execution-evidence", type=Path)
    invalidate_parser = sub.add_parser("invalidate")
    invalidate_parser.add_argument("--reason", default="explicit_manual_invalidation")

    begin = sub.add_parser("begin-rebuild")
    begin.add_argument("--build-id", required=True)
    candidate = sub.add_parser("candidate")
    candidate.add_argument("--build-id")
    activate = sub.add_parser("activate")
    activate.add_argument("--evidence", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state_path: Path = args.state_path.resolve()
    try:
        if args.command == "route":
            text = args.text if args.text is not None else sys.stdin.read()
            prior_task_spec = json.loads(args.prior_task_spec) if args.prior_task_spec else None
            result = route_text(
                text,
                now_kst(args.now),
                state_path,
                prior_task_spec,
                one_time_token=args.one_time_token,
            )
        elif args.command == "dispatch":
            text = args.text if args.text is not None else sys.stdin.read()
            prior_task_spec = json.loads(args.prior_task_spec) if args.prior_task_spec else None
            result = dispatch_text(
                text,
                now_kst(args.now),
                project=args.project,
                state_path=state_path,
                memory_root=DEFAULT_PASSIVE_MEMORY_ROOT,
                prior_task_spec=prior_task_spec,
                one_time_token=args.one_time_token,
            )
        elif args.command == "execute":
            text = args.text if args.text is not None else sys.stdin.read()
            result = run_task_chain(
                text,
                now_kst(args.now),
                project=args.project,
                state_path=state_path,
                memory_root=args.memory_root.resolve(),
                run_root=args.run_root.resolve() if args.run_root else None,
                fixture_source=args.fixture_source.resolve() if args.fixture_source else None,
                fixture_append=args.fixture_append,
            )
        elif args.command == "worker":
            payload = json.loads(sys.stdin.read() or "{}")
            result = run_agent_worker(args.agent, payload, state_path)
        elif args.command == "state":
            result = load_json(state_path)
        elif args.command == "pre-output-gate":
            current_task_spec = json.loads(args.current_task_spec) if args.current_task_spec else None
            result = pre_output_gate(
                args.expected_epoch,
                state_path,
                expected_task_revision=args.expected_task_revision,
                current_task_spec=current_task_spec,
                output_text=args.output_text,
                previous_output_text=args.previous_output_text,
                one_time_token=args.one_time_token,
                execution_evidence_path=(
                    args.execution_evidence.resolve() if args.execution_evidence else None
                ),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["allowed"] else 2
        elif args.command == "invalidate":
            result = invalidate(state_path, args.reason)
        elif args.command == "begin-rebuild":
            result = transition("begin-rebuild", state_path, build_id=args.build_id)
        elif args.command == "candidate":
            result = transition("candidate", state_path, build_id=args.build_id)
        else:
            result = transition(
                "activate",
                state_path,
                evidence_path=args.evidence,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
