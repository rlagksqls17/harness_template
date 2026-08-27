# V1 Agent Harness

이 저장소에서 업무할 때 `Project1/v1/system/immutable_governance.json`, `role_registry.json`, `function_router.json`, 선택된 Agent의 `role/role.json` 순서로 읽는다. 로컬에 `current_task.yaml`이 있으면 현재 revision도 읽는다.

- 사용자의 자연어가 기본 입력이다. YAML은 내부 상태일 뿐 사용자 입력 조건이 아니다.
- 중지, 범위 변경, 방향 수정, 제외 요청은 긴급 steering으로 보고 YAML을 요구하지 않는다.
- `2_Active_Agent`는 폴더 그룹일 뿐 Agent가 아니다. Active Agent는 job 기준 정확히 6개이며 stage는 각 Agent 내부에서 처리한다.
- 역할 정의와 registry는 사용자 승인을 받은 `S_Supervisor_Agent`만 변경한다.
- 기존 결과, 보호 경로, 외부 발송, 서버·캘린더 상태를 근거 없이 바꾸거나 완료로 보고하지 않는다.
- Paperthin skill은 `function_router.json`의 조건이 맞고 해당 `role.json`에 허용된 것만 사용한다. `user_only` 기능은 사용자가 정확한 skill 이름을 직접 지시한 경우에만 실행하고, 다른 skill이 연쇄 호출하지 못한다.
- 선택된 skill이 다른 `model_or_user` 기능을 필요로 하면 그 기능의 eligible Agent로 넘기고 결과를 원 호출 Agent에 반환한다.
- 자동 선택 시 가장 작은 충분한 기능 집합만 쓰고 선택 이유를 current task와 record에 남긴다. skill의 원문보다 Agent 권한과 governance가 우선한다.
- Paperthin 기능은 검증 증거를 대체하지 않는다. release·Git history·PR·외부 상태·role·skill catalog 변경은 각각의 승인 gate를 지킨다.
- 최종 보고는 결과, 의미, 사용자 행동 순으로 짧게 작성하고 세부 내용은 요청 시 제공한다.
