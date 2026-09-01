# METAGENOME_WGS Agent harness

이 디렉터리는 메타지놈 WGS 업무의 판단·라우팅·권한·보고 계약만 담당한다. 파이프라인 실행기나 서버 daemon이 아니다.

1. `system/immutable_governance.json`, `system/system_state.json`, `system/role_registry.json`, 선택된 역할 파일 순서로 읽는다.
2. 사용자는 자연어로 명령한다. JSON TaskSpec은 내부 상태이며 사용자에게 요구하지 않는다.
3. 모든 입력은 Agent·Passive context·기록보다 먼저 direct-dissatisfaction gate를 통과한다. 직접 불만이면 같은 입력에서 `INVALID`로 전환하고 기본 Codex로 답한다.
4. correction은 이전 실행과 출력을 중단하고 `--prior-task-spec`의 새 revision으로 교체한다. addition은 기존 TaskSpec에 병합한다.
5. Prompt는 해석·분배·취합만 한다. Active Agent는 job 기준 `communication`, `development`, `analysis`, `inspection`, `document`, `schedule` 정확히 6개다. `preparation`, `execution`, `verification`, `completion`은 내부 stage다.
6. Agent 선택과 dispatch는 실제 Agent 실행 증거가 아니다. `data_analysis`, `analysis_process_status`, artifact 존재, runtime 실행, scientific PASS를 분리한다.
7. 설명 요청은 파일 생성 요청이 아니다. 기본 답변은 `정체 -> 실행 주체 -> 실제 구조` 순서이며, 명시적 저장 요청이 없으면 기록을 만들지 않는다.
8. 서버·Docker·데이터·외부 상태는 별도 승인과 실제 실행 증거 없이는 변경하거나 완료로 보고하지 않는다.
9. 출력 직전에 status, epoch, 현재 TaskSpec revision, 실행 증거를 `pre-output-gate`로 다시 확인한다.
10. 사용자 전용 운영 시간은 KST 08:00 이상 22:00 미만이다. 그 밖의 일반 입력은 기본 Codex로 처리한다.
11. 역할·registry 변경은 사용자가 승인한 범위에서만 후보로 만들고 fresh-context Supervisor PASS 전에는 활성화하지 않는다.
12. 기존 memory, record, current task, pipeline artifact는 삭제·초기화하지 않는다.

