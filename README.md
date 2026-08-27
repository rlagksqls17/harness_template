# harness_template

사용자 전용 업무 Agent 시스템의 최소 V1입니다. Codex가 이 저장소의 역할 계약을 읽고 필요한 Agent를 작업 단위로 호출하는 구조이며, 별도 서버나 상시 실행 데몬은 아직 만들지 않았습니다.

## V1 범위

- 실제 Agent 10개: Prompt 1, Passive 1, Active 6, Improvement 1, Supervisor 1
- Active Agent는 `communication`, `development`, `analysis`, `inspection`, `document`, `schedule`의 정확히 6개
- `stage`는 Agent를 늘리지 않고 각 Active Agent 내부에서 `preparation → execution → verification → completion`으로 처리
- 일반 입력은 자연어이며 YAML은 내부 작업 상태로만 사용
- 입력 형식과 질문량은 업무 복잡도·위험도에 따라 필요한 만큼만 늘림
- Paperthin 28개 전체 기능을 Agent별 허용 목록과 명령 의도에 따라 선택
- 긴급 중지·범위 변경·방향 수정은 YAML 없이 즉시 반영
- 사용자 승인 없이 역할 구조를 바꿀 수 없고, 역할 수정 권한은 Supervisor에게만 있음
- 실제 memory, record, 현재 작업 YAML과 `reference/`는 로컬 전용이며 Git에 올리지 않음

## 사용

Codex 작업공간을 `Project1/v1`로 열고 자연어로 업무를 요청합니다. 처음부터 YAML을 작성할 필요는 없습니다. Prompt Agent가 필요한 정보만 구조화하고, 미확정 사항이 결과를 바꿀 때만 짧게 질문합니다.

결과 보고는 다음 세 가지만 먼저 보여줍니다.

1. 무엇이 되었는가
2. 사용자에게 어떤 의미인가
3. 사용자가 지금 할 일이 있는가

세부 과정과 증거는 record에 남기고 사용자가 요청할 때 펼칩니다.

## 검증

```powershell
python Project1/v1/tools/validate_system.py
```

이 명령은 선언형 V1 구조와 권한 계약을 검사합니다. `STATIC_SCAFFOLD_PASS`는 Agent 실행 엔진의 동작 검증이 아니라 현재 스캐폴드 계약의 정적 검증 통과를 뜻합니다.

## Paperthin 기능 선택

Paperthin 28개 전체를 프로젝트 내부에 copy 방식으로 고정했습니다. Prompt Agent는 [`function_router.json`](Project1/v1/system/function_router.json)에서 사용자 명령, 현재 job, 산출물, stage를 대조해 가장 작은 충분한 기능 집합만 선택합니다.

- `model_or_user`: 조건이 맞으면 자동 선택 가능
- `user_only`: 사용자가 기능명을 직접 지시한 경우만 선택 가능
- `re0-upgrade`, release, Git history, PR, 외부 상태 변경은 기능이 설치돼 있어도 별도 승인 경계를 통과해야 함
- Paperthin 자체 저장소 전용 기능은 일반 업무 프로젝트에서 작동한다고 간주하지 않음

이 skill들은 행동 규칙을 보조하며, 도메인 테스트나 독립 검증을 대신하지 않습니다.

예를 들어 `오랜만인데 지금 뭐가 바뀌었어?`는 Inspection Agent의 `catchup`, `이 파일들 보고 내가 하려는 일을 먼저 제안해줘`는 Prompt Agent의 `aim`으로 연결할 수 있습니다. 반면 `/debloat 이 보고서`처럼 `user_only` 기능은 이름을 직접 적어야 하고, `/re0-release`는 이름을 적었더라도 Paperthin 저장소 여부와 publish 승인을 다시 확인합니다.
