# METAGENOME_WGS Agent harness

메타지놈 WGS 요청을 자연어 TaskSpec으로 해석하고 권한·revision·증거 gate를 강제하는 작은 사용자 전용 harness다. 서버 파이프라인을 직접 실행하는 daemon이 아니며, `route`나 `dispatch`만으로 외부 Agent가 실행됐다고 주장하지 않는다.

```powershell
python -B src\harness.py state
python -B src\harness.py route --text "메타지놈 WGS 결과를 분석해줘"
python -B src\harness.py dispatch --text "메타지놈 WGS 결과를 분석해줘"
python -B src\harness.py pre-output-gate --expected-epoch 1 --expected-task-revision 1 --current-task-spec task.json --evidence-status completed --execution-evidence evidence.json --output-text "분석 완료"
python -B tools\validate_template.py --strict-runtime
python -B -m unittest discover -s tests -v
```

운영 흐름은 `state -> route/dispatch -> 승인된 실제 작업 -> pre-output-gate`다. 실제 작업 주체는 Codex 또는 사용자가 승인한 실행자이며, harness 출력은 역할 선택·권한 판정·TaskSpec일 뿐이다.

- Active job: communication, development, analysis, inspection, document, schedule (정확히 6개)
- Passive: 필요한 context를 읽기 전용으로 제공하며 작업을 지휘하지 않는다.
- 상태: 로컬 `system/system_state.json`은 Git에서 제외한다. 배포 기본값은 `system/system_state.example.json`의 `CANDIDATE`다.
- 증거: 완료 주장은 현재 Task ID·revision·TaskSpec digest에 묶인 `actual_execution` evidence, 실제 산출물의 경로·크기·SHA-256, 같은 canonical manifest digest의 JSONL ledger entry가 모두 일치해야 한다. worker evidence와 독립 verifier receipt는 `system/trusted_signers.allowed_signers`의 서로 다른 신뢰 키로 서명돼야 한다. 기본 CANDIDATE에는 키가 없어 실제 증거·PASS를 자동 승인하지 않는다. routing-only, artifact-only, dry-run은 완료 증거가 아니다.
- 시간: KST 08:00 이상 22:00 미만만 사용자 harness를 허용한다.
- 보존: 기존 pipeline artifact, memory, record, current task는 runtime 자료이며 배포 commit 대상이 아니다.
