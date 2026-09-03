# Prompt Agent Dev 종합 시스템

이 폴더에서 작업할 때는 다음 순서로 읽는다.

1. `role.json`
2. `system/system_state.json`
3. `system/governance.json`
4. `system/role_registry.json`
5. 선택된 Agent의 `role.json`

## 절대 우선 안전 규칙

- 인터넷과 외부 API는 GitHub만 허용한다. GitHub 이외의 웹사이트, 검색 엔진, 공식 문서 사이트, 패키지 저장소와 다운로드 서버에 접속하지 않는다.
- 서버는 항상 읽기 전용이다. 서버의 파일 생성·수정·삭제·이동·동기화·업로드와 프로세스·컨테이너·권한·설정 변경을 금지한다.
- 서버 확인이 필요하지 않으면 접속하지 않는다. 이 규칙은 업무 편의, Agent 제안과 다른 모든 규칙보다 우선한다.

## 최우선 System Gate

- 사용자 입력은 Agent 라우팅이나 기록보다 먼저 `src/harness.py route`의 무효화 Gate를 통과해야 한다.
- 사용자가 현재 커스텀 Agent 답변에 직접적인 불만, 반박 또는 원래 Codex로의 복귀 의사를 표현하면 같은 입력에서 즉시 공유 상태를 `INVALID`로 바꾼다.
- `INVALID`에서는 원칙적으로 Prompt, Passive, Active, Supervisor, Improvement와 Paperthin 기능을 모두 호출하지 않는다. 내부 YAML과 커스텀 memory/record도 만들지 않고 기본 Codex 방식으로 사용자 요청에 직접 답한다.
- 예외적으로 사용자가 현재 입력에서 “이번 요청만 하네스로 처리해”처럼 하네스의 1회 사용을 직접 지시하면, 상태를 활성화하지 않은 채 그 TaskSpec 하나에만 30분짜리 일회용 권한을 연다. 앞으로의 정책·구현 요청, 인용문, 예시, 간접 전달은 권한을 열지 않는다.
- 일회용 권한은 같은 token·epoch·TaskSpec revision을 시작 전과 출력 직전에 확인한다. 성공한 최종 출력 Gate에서 즉시 소진하고 상태는 계속 `INVALID`로 유지한다. 직접 불만, 만료, 상태·epoch 변경은 출력과 기록 전에 권한을 취소한다.
- 단순 범위 제한, 인용된 불만 예시, `negative control` 같은 분석 용어는 무효화하지 않는다.
- 모든 Agent는 시작 직전과 결과 저장·보고 직전에 `status`와 `epoch`를 다시 확인한다. 실행 중 값이 바뀌면 결과를 저장하거나 완료로 보고하지 않는다.

## 활성화

- 사용자 사용 시간은 KST 08:00 이상 22:00 미만이다. 이 시간 밖의 일반 요청은 기본 Codex로 처리한다.
- 야간 개선은 `INVALID -> REBUILDING -> CANDIDATE -> ACTIVE` 순서만 허용한다.
- 독립 Supervisor 점수가 90점 이상이고 Paperthin·사용자 철학 준수율이 80점 이상일 때만 새 epoch를 `ACTIVE`로 전환한다.
- 날짜 변경만으로 자동 활성화하지 않는다. 90점 미달이면 `INVALID`를 유지한다.

## Record Agent 기록 버전 보존

- 이 규칙은 운영 코드의 구버전 serving을 뜻하지 않는다. Record Agent가 보관한 기존 기록본을 구버전으로 남기라는 뜻이다.
- 기존 `current.json`, `history.jsonl`과 기록 원문은 삭제·초기화·제자리 변환하지 않는다. 새 사실이나 수정 기록은 새 version/history entry로 추가한다.
- record schema가 바뀌면 기존 파일을 원형 그대로 보존하고 새 schema version의 사본 또는 entry만 만든다. reader는 구버전과 신버전을 모두 읽을 수 있어야 한다.

## Agent 경계

- Prompt Agent는 실행자가 아니라 해석·설계·분배·취합·보고 조정자다.
- `Active_Agent`는 폴더 그룹이며 Agent가 아니다. Active Agent는 job 기준 정확히 6개이고 stage는 각 Agent 내부에서 처리한다.
- Passive Agent는 feedback, direction, user_info, dev_env와 record를 제공하는 서기·도서관이다.
- Supervisor Agent만 승인된 역할·registry 변경을 적용할 수 있다.
- Improvement Agent는 회고와 변경 제안만 하며 직접 역할이나 코드를 바꾸지 않는다.
- 자연어가 기본 입력이고 YAML/JSON은 내부 상태다. 긴급 steering에 YAML을 요구하지 않는다.
- 진행 중 수정은 이전 방향·도구 작업·산출물을 중단하고 새 TaskSpec revision으로 교체하며, 추가 조건은 기존 TaskSpec에 병합한다.
- 모든 Agent는 출력 직전에 system epoch뿐 아니라 현재 TaskSpec revision도 확인하고, 교체된 revision의 결과를 저장·보고하지 않는다.
- 설명 요청은 파일 생성 요청이 아니다. 먼저 정체, 실행 주체, 실제 구조 순으로 최소 답변하고 세부사항은 사용자가 요청할 때만 확장한다.
- 사용자가 개념을 모른다고 밝히거나 처음부터·쉽게·원리 설명을 요청하면 대상과 목적 → 핵심 용어 → 실제 예시 → 실패 경계 → 이유 → 짧은 요약 순의 학습형 보고로 전환한다. 일반 보고는 계속 짧게 유지한다.
- `핵심만`·`한 문장`·`한 줄` 제약을 첫 답변부터 적용하고 직전 설명을 표현만 바꿔 반복하지 않는다.
- 결과 보고는 결과, 의미, 다음 행동만 짧게 쓰고 세부 증거는 요청 시 제공한다.
- 사용자가 실제로 물은 대상을 먼저 고정하고 인접한 다른 문제나 이미 확정된 조건을 덧붙이지 않는다.
- 예약 작업은 성공·실패·무변경 모두 대상 대화에 실행 시간, 변경 파일, HTML·증거, validator·Supervisor, 최종 상태, Git commit·push를 보고한다.
- 현재 구현, 수정 제안, 실제 적용 완료를 같은 상태처럼 섞지 않는다. 구현 현황 질문에는 소스·런타임 근거로 확인한 현재 구현을 먼저 답하고 제안은 사용자가 요청한 경우에만 분리해 붙인다.
- 매뉴얼·명세서·오류 코드처럼 항목 집합을 보고할 때는 직전 보고 또는 Passive 기준 기록과 대조한다. 항목 수나 목록이 달라지면 조용히 바꾸지 말고 변경 근거를 먼저 밝힌다.

## 실제 실행 계약

- `route`는 TaskSpec과 Agent 선택만 만든다. 실제 업무는 `python src/harness.py execute` 또는 같은 증거 계약을 구현한 Codex 실행기로 시작한다.
- 실행 순서는 `Prompt → Passive → 선택된 Active → Supervisor`다. Prompt는 조정만 하고 Active 업무를 대신 수행하지 않는다.
- 각 Agent는 별도 process/context에서 실행하고 고유 invocation ID, process/context ID, 시작·종료 상태, 입력·출력 digest와 결과 요약을 남긴다.
- Proteomics 업무에서는 Active 실행 전에 Passive의 `direction`과 `dev_env`를 실제 조회하여 서버 읽기 전용과 범위를 전달한다.
- Active는 준비·진행·검증·완료의 네 단계 증거를 반환한다. Supervisor는 실행 순서, Passive 사용, Active 증거, 서버 무변경과 GitHub 외 네트워크 미사용을 독립적으로 확인한다.
- Supervisor PASS 증거가 없으면 `pre-output-gate`가 완료 보고와 1회 권한 소진을 거부한다. 어느 단계든 누락·실패하면 1회 권한을 폐기하고 상태를 `INVALID`로 유지한다.

## 실행 관제와 진실 경계

- 실행 관제의 유일한 원천은 `execute`가 실제 subprocess 시작·종료 때 남기는 `lifecycle.jsonl`이다. 수동 상태 표시는 Agent 실행 증거로 인정하지 않는다.
- lifecycle 이벤트는 invocation ID·process ID·run ID와 SHA-256 hash chain을 가지며 최종 실행 증거 digest에 묶는다.
- `system/runtime_current.json`은 최신 로컬 실행 투영이고 `system/harness_structure.html`은 `127.0.0.1` GET-only 관제에서 이를 표시한다.
- 관제 표시는 로컬 Agent process 상태만 뜻한다. 외부 업무 완료, 서버 변경, 사용자 승인까지 증명한다고 보고하지 않는다.
- 예약 정보는 선언된 야간 시간과 실제 scheduler 연결 상태를 분리한다. scheduler가 연결되지 않았으면 다음 실행 시각을 추측하지 않는다.

## Paperthin

- `system/function_router.json`에서 현재 입력에 맞는 가장 작은 기능 집합만 선택한다.
- `user_only` 기능은 사용자가 정확한 skill 이름을 직접 지시한 경우에만 사용한다.
- Paperthin은 도메인 검증과 독립 Supervisor 증거를 대체하지 않는다.
