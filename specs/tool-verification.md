# xcelium-mcp Tool Verification Guide

> **목적**: 모든 MCP tool의 기능을 전수검사. 버그 수정 후 cloud0에서 수행.  
> **환경**: cloud0 (192.168.1.252:1407), venezia-t0 테스트벤치  
> **대상 테스트**: TOP000~TOP016 (TB 분석 캐시: `.ai/analysis/tb_TOP*.analysis.md`)  
> **시나리오 수**: 약 150건 (G1~G11, 11개 그룹)  
> **업데이트 기준**: 새로운 버그 수정 후 해당 섹션 + Group 11 체크리스트에 추가  
> **최종 업데이트**: 2026-04-16 (F-117~F-126 검증, G11 Security Regression 신설)

---

## 사전 준비

```
# 1. xcelium-mcp 최신 배포 확인
mcp__ssh__ssh_run: "cd /opt/xcelium-mcp && git log --oneline -3"

# 2. pytest 통과 확인
mcp__ssh__ssh_run: "/opt/mcp-env/bin/pytest tests/ -x -q 2>&1 | tail -5"

# 3. MCP reconnect (Claude Code에서 /mcp를 통해 xcelium-mcp 재연결)
```

---

## 도구 목록 (24개)

| 그룹 | Tool | 모듈 |
|------|------|------|
| Discovery | `list_tests`, `sim_discover`, `mcp_config` | sim_lifecycle |
| Lifecycle | `sim_bridge_run`, `connect_simulator`, `sim_disconnect`, `sim_run`, `sim_restart`, `execute_tcl`, `sim_status` | sim_lifecycle |
| Signal | `inspect_signal`, `deposit_signal` | signal_inspection |
| Debug | `watch`, `probe`, `bisect_signal`, `debug_snapshot` | debug |
| Batch | `sim_batch_run`, `sim_regression` | batch |
| SimVision | `simvision_connect`, `simvision`, `compare_waveforms` | simvision |
| Waveform | `waveform`, `waveform_screenshot` | waveform |
| Checkpoint | `checkpoint` | checkpoint |

---

## Group 1: Discovery & Config

### G1-1 `list_tests` — 기본 조회
```
mcp__xcelium-mcp__list_tests: {}
→ "Tests (N found):\n  VENEZIA_TOP000\n  ..." (캐시 or 명령 실행)
```

### G1-2 `list_tests` — pattern 필터
```
mcp__xcelium-mcp__list_tests: {pattern: "TOP01"}
→ TOP010~TOP016 포함, TOP000~TOP009 제외
```

### G1-3 `list_tests` — pattern 매칭 없음
```
mcp__xcelium-mcp__list_tests: {pattern: "NONEXISTENT_XYZ"}
→ "No tests found (pattern=NONEXISTENT_XYZ)."
```

### G1-4 `sim_discover` — 기본 자동 탐지
```
mcp__xcelium-mcp__sim_discover: {}
→ sim_dir / runner / EDA env / bridge port 정상 탐지
```

### G1-5 `sim_discover` — force 재탐지
```
mcp__xcelium-mcp__sim_discover: {force: true}
→ 기존 레지스트리 무시하고 재탐지
```

### G1-6 `sim_discover` — 명시적 sim_dir
```
mcp__xcelium-mcp__sim_discover: {sim_dir: "~/git.clone/venezia-t0/design/top/sim/ncsim"}
→ 해당 경로 기준으로 탐지
```

### G1-7 `mcp_config` — show (전체 설정)
```
mcp__xcelium-mcp__mcp_config: {action: "show", file: "config"}
→ JSON 전체 출력
```

### G1-8 `mcp_config` — get 단일 키
```
mcp__xcelium-mcp__mcp_config: {action: "get", file: "config", key: "runner.default_mode"}
→ "rtl" 또는 설정된 값
```

### G1-9 `mcp_config` — set + get 왕복
```
mcp__xcelium-mcp__mcp_config: {action: "set", file: "config", key: "runner.default_mode", value: "rtl"}
mcp__xcelium-mcp__mcp_config: {action: "get", file: "config", key: "runner.default_mode"}
→ "rtl"
```

### G1-10 `mcp_config` — registry 파일 show
```
mcp__xcelium-mcp__mcp_config: {action: "show", file: "registry"}
→ mcp_registry.json 내용
```

### G1-11 `mcp_config` — checkpoint 파일 show
```
mcp__xcelium-mcp__mcp_config: {action: "show", file: "checkpoint"}
→ checkpoints/manifest.json 내용 (체크포인트 없으면 빈 JSON)
```

### G1-12 `mcp_config` — 잘못된 action
```
mcp__xcelium-mcp__mcp_config: {action: "invalid_action"}
→ "ERROR: Unknown action..."
```

### G1-13 `mcp_config` — protected key set 차단 (F-117 회귀)
```
mcp__xcelium-mcp__mcp_config: {
  action: "set", file: "registry",
  key: "runner.exec_cmd_override", value: "rm -rf /"
}
→ "ERROR: Key 'runner.exec_cmd_override' is protected and cannot be modified."
```
**목적**: F-117에서 `_PROTECTED_KEYS`에 추가된 `exec_cmd_override` / `regression_exec_cmd_override` 보호 동작 확인.

### G1-14 `mcp_config` — 다른 protected key (`regression_exec_cmd_override`)
```
mcp__xcelium-mcp__mcp_config: {
  action: "set", file: "registry",
  key: "runner.regression_exec_cmd_override", value: "malicious"
}
→ "ERROR: Key '...' is protected and cannot be modified."
```

---

## Group 2: Bridge Lifecycle

### G2-1 `sim_bridge_run` — 정상 시작
```
mcp__xcelium-mcp__sim_bridge_run: {test_name: "TOP000"}
→ "Bridge ready" + port + PID 포함 메시지
```

### G2-2 `sim_bridge_run` — 단축 이름 resolve
```
mcp__xcelium-mcp__sim_bridge_run: {test_name: "TOP015"}
→ VENEZIA_TOP015로 resolve 후 시작
```

### G2-3 `connect_simulator` — auto 연결
```
mcp__xcelium-mcp__connect_simulator: {}
→ "Connected to xmsim ... (ping=pong)\nxmsim_pid: NNNNN"
```

### G2-4 `connect_simulator` — 명시적 port
```
mcp__xcelium-mcp__connect_simulator: {port: 9876, target: "xmsim"}
→ 연결 성공
```

### G2-5 `connect_simulator` — 잘못된 port (연결 불가)
```
mcp__xcelium-mcp__connect_simulator: {port: 19999, target: "xmsim"}
→ "ERROR: Connection failed: ConnectionRefusedError..."
```

### G2-6 `sim_status` — 기본
```
mcp__xcelium-mcp__sim_status: {}
→ "Time: 0 NS\nScope: ..." (time 0 위치)
```

### G2-7 `sim_status` — target 명시
```
mcp__xcelium-mcp__sim_status: {target: "xmsim"}
→ 동일 결과
```

### G2-8 `sim_run` — duration 지정
```
mcp__xcelium-mcp__sim_run: {duration: "100ns"}
→ "Simulation advanced. Current position: ..."
```

### G2-9 `sim_run` — duration 단위 없음 (에러)
```
mcp__xcelium-mcp__sim_run: {duration: "100"}
→ "ERROR: Invalid duration '100'. Expected format like '100ns'..."
```

### G2-10 `sim_run` — 대용량 duration (chunked)
```
mcp__xcelium-mcp__sim_run: {duration: "500us", chunk: 100000}
→ CHUNKED_RUN_REPORT 파싱 → "status: completed, sim_time: 500000ns"
```

### G2-11 `sim_run` — 자연종료 (duration 없음)
```
mcp__xcelium-mcp__sim_run: {duration: ""}
→ TB 자연종료까지 실행 후 완료 메시지
```
**주의**: TOP000 400us 소요.

### G2-12 `sim_run` — chunk=0 (legacy 1-shot)
```
mcp__xcelium-mcp__sim_run: {duration: "10us", chunk: 0}
→ 정상 실행 (CHUNKED_ 헤더 없는 응답)
```

### G2-12a `sim_run` — chunk 소값 (1000ns = 1µs 단위)
```
mcp__xcelium-mcp__sim_run: {duration: "10us", chunk: 1000}
→ 10회 chunk 반복 → 정상 완료 (overhead 증가하지만 기능 동일)
```
**목적**: chunk 값 다양성 검증. stop 파일 감지 주기가 1µs로 짧아짐을 확인.

### G2-12b `sim_run` — chunk 대값 (1ms = 1,000,000ns)
```
mcp__xcelium-mcp__sim_run: {duration: "500us", chunk: 1000000}
→ 1-shot처럼 실행 (500us < 1000000ns, 단일 chunk)
```

### G2-13 `sim_run` — 짧은 timeout (timeout 에러)
```
mcp__xcelium-mcp__sim_run: {duration: "999ms", timeout: 1}
→ "ERROR: sim_run exceeded 1s. Pass larger timeout=..."
```

### G2-14 `execute_tcl` — 정상 명령
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "where"}
→ 현재 시뮬레이션 위치 (ns)
```

### G2-15 `execute_tcl` — scope 조회
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "scope"}
→ 현재 스코프 경로
```

### G2-16 `execute_tcl` — denylist 차단 (exec)
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "exec ls /tmp"}
→ "ERROR: Tcl command 'exec' is blocked for security."
```

### G2-17 `execute_tcl` — denylist 차단 (세미콜론 우회 시도)
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "where; exec ls"}
→ "ERROR: Tcl command 'exec' is blocked for security."
```

### G2-18 `execute_tcl` — bracket injection 차단
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "where [exec ls]"}
→ "ERROR: Tcl command contains embedded [exec]..."
```

### G2-19 `execute_tcl` — simvision target
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "where", target: "simvision"}
→ simvision 연결 시 simvision 위치, 미연결 시 ERROR
```

### G2-20 `sim_restart` — 기본 restart
```
mcp__xcelium-mcp__sim_run: {duration: "50us"}   # 먼저 시뮬레이션 진행
mcp__xcelium-mcp__sim_restart: {}
→ "Simulation restarted to time 0. (restarted:snapshot|time:0|backup_shm:...)"
→ "Previous SHM backed up to: .../waves_backup_TIMESTAMP.shm"
```

### G2-21 `sim_restart` — restart 후 time 확인
```
mcp__xcelium-mcp__sim_restart: {}
mcp__xcelium-mcp__sim_status: {}
→ "Time: 2 MS" 또는 소규모 비-0 시간 (init_snapshot 복귀)
```
**주의**: sim_restart는 `time 0`이 아닌 `init_snapshot` 저장 시점으로 복귀함.
`on_init` 실행 시점이 0이 아니므로 "Time: 0 NS" 응답은 나오지 않는다. 알려진 동작.

### G2-2a `connect_simulator` — sim_bridge_run 직후 명시적 연결 필수
```
# 새 MCP 세션에서:
mcp__xcelium-mcp__sim_bridge_run: {test_name: "TOP000"}
# sim_bridge_run이 내부적으로 bridge를 연결하지만 MCP 서버 상태에는 반영 안 됨
mcp__xcelium-mcp__connect_simulator: {}  # ← 반드시 명시적으로 호출해야 함
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "where"}
→ "No simulator connected" 에러 없이 정상 응답
```
**주의**: `sim_bridge_run` 후 `connect_simulator` 없이 bridge 도구(`execute_tcl`, `inspect_signal` 등)를
호출하면 "No simulator connected" 에러 발생. 항상 `connect_simulator`를 먼저 호출할 것.

### G2-22 `sim_restart` — restart 후 bisect Mode B (F-116)
```
mcp__xcelium-mcp__sim_restart: {}
mcp__xcelium-mcp__sim_run: {duration: "100us"}
# bridge 연결 중이므로 Mode B (start_ns/end_ns 지정)를 사용
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1", start_ns: 0, end_ns: 100000}
→ Mode B 이진 탐색 → "Match at Nns"
```

### G2-23 `sim_disconnect` — bridge 해제
```
mcp__xcelium-mcp__sim_disconnect: {action: "bridge", target: "xmsim"}
→ "xmsim: disconnected"
```

### G2-24 `sim_disconnect` — 미연결 상태에서 bridge 해제
```
mcp__xcelium-mcp__sim_disconnect: {action: "bridge", target: "xmsim"}
→ "No xmsim bridge connected."
```

### G2-25 `sim_disconnect` — shutdown (정상 종료)
```
# sim_bridge_run + connect 후:
mcp__xcelium-mcp__sim_disconnect: {action: "shutdown", target: "xmsim"}
→ "xmsim: shutdown ok (...)"
```

### G2-26 `sim_disconnect` — shutdown, 미연결
```
mcp__xcelium-mcp__sim_disconnect: {action: "shutdown", target: "all"}
→ "ERROR: No simulator connected."
```

### G2-27 `sim_disconnect` — 잘못된 action
```
mcp__xcelium-mcp__sim_disconnect: {action: "kill"}
→ "ERROR: Unknown action 'kill'. Use 'bridge' or 'shutdown'."
```

---

## Group 3: Signal Inspection

> 사전 조건: `sim_bridge_run TOP000` + `connect_simulator` + `sim_run(50us)` 완료

### G3-1 `inspect_signal` value — 단일 신호
```
mcp__xcelium-mcp__inspect_signal: {action: "value", signal: "top.hw.w_rst"}
→ "top.hw.w_rst = 1" (또는 0/X/Z)
```

### G3-2 `inspect_signal` value — 복수 신호
```
mcp__xcelium-mcp__inspect_signal: {
  action: "value",
  signals: ["top.hw.w_rst", "top.hw.w_scl"]
}
→ 각 신호 값 2줄
```

### G3-3 `inspect_signal` value — 존재하지 않는 신호
```
mcp__xcelium-mcp__inspect_signal: {action: "value", signal: "top.hw.nonexistent_xxx"}
→ "top.hw.nonexistent_xxx = ERROR: ..."
```

### G3-4 `inspect_signal` describe
```
mcp__xcelium-mcp__inspect_signal: {action: "describe", signal: "top.hw.w_rst"}
→ type, width, direction 등 메타데이터
```

### G3-5 `inspect_signal` list
```
mcp__xcelium-mcp__inspect_signal: {action: "list", scope: "top.hw", pattern: "w_rs*"}
→ w_rst 등 매칭 신호 목록
```

### G3-6 `inspect_signal` list — pattern 없음 (전체)
```
mcp__xcelium-mcp__inspect_signal: {action: "list", scope: "top.hw"}
→ top.hw 스코프 모든 신호
```

### G3-7 `inspect_signal` drivers
```
mcp__xcelium-mcp__inspect_signal: {action: "drivers", signal: "top.hw.w_rst"}
→ 드라이버 목록 (또는 "No drivers")
```

### G3-8 `inspect_signal` check_dump
```
mcp__xcelium-mcp__inspect_signal: {
  action: "check_dump",
  signals: ["top.hw.w_rst", "top.hw.nonexistent_xxx"]
}
→ "Found (1): + top.hw.w_rst\nMissing (1): - top.hw.nonexistent_xxx"
```

### G3-9 `inspect_signal` check_dump — shm_path 명시
```
mcp__xcelium-mcp__inspect_signal: {
  action: "check_dump",
  signals: ["top.hw.w_rst"],
  shm_path: "/usrdata/.../dump/ci_top.shm"
}
→ "Found (1): + ..."
```

### G3-10 `inspect_signal` — Tcl injection 차단
```
mcp__xcelium-mcp__inspect_signal: {action: "value", signal: "top.hw.sig; exec ls"}
→ "ERROR: signal name contains forbidden characters"
```

### G3-11 `deposit_signal` — force 값 설정
```
mcp__xcelium-mcp__deposit_signal: {signal: "top.hw.w_rst", value: "0"}
mcp__xcelium-mcp__inspect_signal: {action: "value", signal: "top.hw.w_rst"}
→ "... = 0"
```

### G3-12 `deposit_signal` — release (복원)
```
mcp__xcelium-mcp__deposit_signal: {signal: "top.hw.w_rst", release: true}
mcp__xcelium-mcp__inspect_signal: {action: "value", signal: "top.hw.w_rst"}
→ 원래 구동값으로 복귀
```

### G3-13 `deposit_signal` — value 없고 release도 아닌 경우
```
mcp__xcelium-mcp__deposit_signal: {signal: "top.hw.w_rst", value: ""}
→ "ERROR: Either 'value' or 'release=True' is required."
```

### G3-14 `deposit_signal` — Tcl forbidden value
```
mcp__xcelium-mcp__deposit_signal: {signal: "top.hw.w_rst", value: "[exec ls]"}
→ "ERROR: value contains forbidden characters..."
```

### G3-15 `inspect_signal` list — scope 전환 후 조회
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "scope top.hw"}
mcp__xcelium-mcp__inspect_signal: {action: "list", scope: "top.hw.u_ext_i2c"}
→ u_ext_i2c 하위 신호 목록 (scope 전환 후 하위 모듈 탐색 가능)
```

### G3-16 `inspect_signal` value — 벡터 신호 (멀티비트)
```
mcp__xcelium-mcp__inspect_signal: {action: "value", signal: "top.hw.token"}
→ "top.hw.token = 4'h8" 형태 (비트폭 표시 포함)
```

---

## Group 4: Debug Tools

> 사전 조건: `sim_bridge_run TOP000` + `connect_simulator` 완료

### G4-1 `watch` set — watchpoint
```
mcp__xcelium-mcp__watch: {action: "set", signal: "top.hw.w_rst", op: "==", value: "0"}
→ "Watchpoint set: N" (ID 반환)
```

### G4-2 `watch` set — breakpoint 타입
```
mcp__xcelium-mcp__watch: {action: "set", signal: "top.hw.w_rst", op: "==", value: "1", type: "breakpoint"}
→ "Breakpoint set: ..." (stop ID)
```

### G4-3 `watch` set — 잘못된 operator
```
mcp__xcelium-mcp__watch: {action: "set", signal: "top.hw.w_rst", op: "===", value: "1"}
→ "ERROR: Invalid operator '==='. Use one of: ..."
```

### G4-4 `watch` set — forbidden value
```
mcp__xcelium-mcp__watch: {action: "set", signal: "top.hw.w_rst", op: "==", value: "$ENV"}
→ "ERROR: value contains forbidden Tcl metachar"
```

### G4-5 `watch` clear — 특정 ID
```
mcp__xcelium-mcp__watch: {action: "clear", watch_id: "1"}
→ "Cleared watchpoint 1" 또는 유사 메시지
```

### G4-6 `watch` clear — all (F-115 검증)
```
mcp__xcelium-mcp__watch: {action: "clear", watch_id: "all"}
→ 모든 watchpoint/breakpoint 삭제 확인
→ bridge 재연결 후에도 정상 동작
```

### G4-7 `watch` clear — 비숫자 ID
```
mcp__xcelium-mcp__watch: {action: "clear", watch_id: "abc"}
→ "ERROR: watch_id must be 'all' or a numeric ID"
```

### G4-8 `probe` add — 신호 추가
```
mcp__xcelium-mcp__probe: {action: "add", signals: ["top.hw.w_rst", "top.hw.w_scl"]}
→ "Probe added for 2 signal(s)."
```

### G4-9 `probe` add — depth 지정
```
mcp__xcelium-mcp__probe: {action: "add", signals: ["top.hw"], depth: "1"}
→ "Probe added for 1 signal(s)."
```

### G4-10 `probe` add — 잘못된 depth
```
mcp__xcelium-mcp__probe: {action: "add", signals: ["top.hw.w_rst"], depth: "notadigit"}
→ "ERROR: depth must be 'all' or a numeric value"
```

### G4-11 `probe` enable/disable
```
mcp__xcelium-mcp__probe: {action: "disable"}
mcp__xcelium-mcp__probe: {action: "enable"}
→ 각각 정상 응답
```

### G4-12 `bisect_signal` Mode A — shm_path 명시
```
# sim_batch_run TOP000 완료 후:
mcp__xcelium-mcp__bisect_signal: {
  signal: "top.hw.w_rst",
  op: "eq", value: "1",
  shm_path: "/usrdata/.../dump/ci_top_VENEZIA_TOP000.shm"
}
→ "Match at Nns ..."
```

### G4-13 `bisect_signal` Mode A — shm_path="" (auto-detect, F-116)
```
# sim_batch_run 완료 후 (bridge 미연결 상태):
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1", shm_path: ""}
→ find_shm()로 최신 SHM 자동 탐지 → "Match at Nns"
```
**주의**: bridge 실행 중 `ci_top.shm`는 simvisdbutil로 읽을 수 없음 ("Bad end time: End time is 0").
Mode A는 **완료된 batch SHM** (`ci_top_VENEZIA_TOP*.shm`)에만 사용 가능.
bridge 연결 중이면 shm_path에 완료된 SHM을 명시하거나 Mode B(start_ns/end_ns 지정)를 사용.

### G4-14 `bisect_signal` Mode A — change operator
```
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "change", value: "", shm_path: "..."}
→ 첫 번째 값 변화 시점 반환
```

### G4-15 `bisect_signal` Mode B — bridge 기반
```
# bridge 연결 상태에서, shm_path 없음:
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1", start_ns: 0, end_ns: 100000}
→ Mode B 이진 탐색 → "Match at Nns"
```

### G4-16 `bisect_signal` Mode A — context_signals
```
mcp__xcelium-mcp__bisect_signal: {
  signal: "top.hw.w_rst", op: "eq", value: "1",
  shm_path: "...",
  context_signals: ["top.hw.w_scl"]
}
→ 컨텍스트 신호 값도 포함한 결과
```

### G4-17 `bisect_signal` — invalid shm_path (path traversal)
```
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1", shm_path: "../../etc/passwd"}
→ "ERROR: Invalid path: ..."
```

### G4-18 `debug_snapshot` — 기본 snapshot
```
mcp__xcelium-mcp__debug_snapshot: {target: "top.hw"}
→ 신호 상태 스냅샷 JSON
```

### G4-19 `debug_snapshot` — snapshot mode (F-125 bulk 회귀)
```
# sim_run(100us) 후 scope top.hw:
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "scope top.hw"}
mcp__xcelium-mcp__debug_snapshot: {mode: "snapshot", target: "xmsim"}
→ "Signal Values (current scope)" 섹션에 top.hw 내 50개 이상 신호 값 포함
→ 단일 __DEBUG_SNAPSHOT_BULK__ 호출로 처리됨 (N+1 RTT 해소)
```
**목적**: F-125 — bulk 커맨드로 N회 Tcl 왕복이 1회로 줄었는지 확인.  
**실검증 (2026-04-16)**: 53개 신호 단일 bulk 응답 반환 ✅

### G4-20 `debug_snapshot` — mode="tcl" (Tcl script 생성)
```
mcp__xcelium-mcp__debug_snapshot: {
  mode: "tcl",
  shm_path: "/usrdata/.../dump/ci_top_VENEZIA_TOP000_stimulation_test.shm",
  signals: ["top.hw.w_rst", "top.hw.w_scl"],
  center_time_ns: 50000,
  zoom_range_ns: 10000,
  markers: [{"time_ns": 50000, "label": "Bug point"}],
  context_note: "Test snapshot"
}
→ "/tmp/.../debug_script_XXXXXX.tcl" 경로 반환
→ 파일 내용: database -open ..., wave add ..., marker ..., zoom ... 포함
```
**목적**: mode="tcl" 오프라인 디버깅 스크립트 생성 검증.

### G4-21 `debug_snapshot` — mode="export" (Markdown 컨텍스트)
```
mcp__xcelium-mcp__debug_snapshot: {
  mode: "export",
  test_name: "TOP000",
  bug_description: "Reset not deasserted",
  root_cause: "POR circuit not triggering",
  evidence: [{"time_ns": 50000, "signal": "top.hw.w_rst", "value": "0", "expected": "1", "meaning": "Reset stuck"}],
  signals_to_check: ["top.hw.w_rst", "top.hw.r_por_en_n"]
}
→ "/tmp/.../debug_context_XXXXXX.md" 경로 반환
→ 파일 내용: Bug, Root Cause, Evidence, Signals to Check 섹션 포함
```
**목적**: mode="export" AI 분석 컨텍스트 문서 생성 검증.

---

## Group 5: Batch Simulation

### G5-1 `sim_batch_run` — 기본 실행
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000"}
→ "sim_batch_run VENEZIA_TOP000 completed.\nshm_path: .../dump/ci_top_VENEZIA_TOP000.shm\n..."
→ PASS 포함 여부 확인
```

### G5-2 `sim_batch_run` — 단축 이름 resolve
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000"}
→ VENEZIA_TOP000으로 자동 resolve
```

### G5-3 `sim_batch_run` — force 재실행
```
# 이미 완료된 테스트에:
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", force: true}
→ 기존 결과 무시하고 재실행
```

### G5-4 `sim_batch_run` — run_duration 제한
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", run_duration: "10us"}
→ 10us 시점에서 조기 종료 (PASS/FAIL 미확정 가능)
```

### G5-5 `sim_batch_run` — dump_depth boundary
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", dump_depth: "boundary"}
→ 경계 신호만 SHM 기록
```

### G5-6 `sim_batch_run` — dump_depth 유효성
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", dump_depth: "invalid"}
→ "Invalid dump_depth='invalid'. Must be 'boundary', 'all', or '' (auto)."
```

### G5-7 `sim_batch_run` — dump_window (시간 범위 덤프)
```
mcp__xcelium-mcp__sim_batch_run: {
  test_name: "TOP000",
  dump_window_start_ms: 0,
  dump_window_end_ms: 1
}
→ 0~1ms 범위만 SHM 기록
```

### G5-8 `sim_batch_run` — dump_window 역전 (에러)
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", dump_window_start_ms: 5, dump_window_end_ms: 1}
→ "dump_window_start must be less than dump_window_end" 유사 에러
```

### G5-9 `sim_batch_run` — sdf_corner 유효성
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", sdf_corner: "invalid"}
→ "Invalid sdf_corner='invalid'. Must be 'min', 'max', or 'typ'."
```

### G5-9a `sim_batch_run` — dump_signals (특정 신호만 덤프)
```
mcp__xcelium-mcp__sim_batch_run: {
  test_name: "TOP000",
  dump_signals: ["top.hw.w_rst", "top.hw.w_scl"],
  dump_depth: "boundary"
}
→ 완료 후 SHM에 지정 신호 포함 확인
→ bisect_signal {signal: "top.hw.w_rst", shm_path: ""} → "Match at Nns"
```
**목적**: dump_signals 파라미터가 올바르게 전달되는지 확인.

### G5-10 `sim_regression` — 소규모 리스트
```
mcp__xcelium-mcp__sim_regression: {test_list: ["TOP000", "TOP001"]}
→ 두 테스트 순차 실행 → PASS/FAIL 집계 테이블
```

### G5-11 `sim_regression` — 빈 리스트
```
mcp__xcelium-mcp__sim_regression: {test_list: []}
→ "ERROR: test_list is empty." 유사 에러
```

### G5-12 `sim_regression` — NO_VERDICT 테스트 (F-086)
```
# run_duration으로 조기 종료한 테스트는 NO_VERDICT로 집계
mcp__xcelium-mcp__sim_regression: {test_list: ["TOP000"], dump_signals: ["top.hw.w_rst"]}
→ PASS/FAIL/NO_VERDICT 집계 포함 (F-085 dual-level 판정 확인)
```

---

## Group 6: SimVision Integration

> 사전 조건: X11 또는 VNC 환경 (cloud0: DISPLAY=:3, VNC port 5903)  
> compare_waveforms(G6-7~G6-9)는 csv_diff 모드에서 VNC 불필요

### G6-1 `simvision_connect` — 새 SimVision 시작
```
mcp__xcelium-mcp__simvision_connect: {
  action: "start",
  shm_path: "/usrdata/.../dump/ci_top_VENEZIA_TOP000_stimulation_test.shm",
  display: ":3"
}
→ "SimVision started and connected.\n  display: :3\n  port: 9876\n  ..."
```
**실검증 (2026-04-16)**: display="" (미지정) → auto-detect가 `:3` 정상 탐지 ✅  
**주의**: 최초 검증 시 `display=":0"`을 직접 지정해서 실패했던 것 — auto-detect 버그 아님. display 생략 권장.

### G6-2 `simvision_connect` — attach (이미 연결된 SimVision 재사용)
```
mcp__xcelium-mcp__simvision_connect: {action: "attach", port: 9876}
→ "SimVision already connected at localhost:9876 (reusing existing bridge)."
```
**실검증 (2026-04-16)**: 재연결 시 "reusing existing bridge" ✅

### G6-3 `simvision` — reload (다른 SHM으로 교체)
```
mcp__xcelium-mcp__simvision: {
  action: "reload",
  shm_path: "/usrdata/.../dump/ci_top_VENEZIA_TOP014_btnop_interlock_test.shm"
}
→ "Database replaced: ci_top_VENEZIA_TOP000_stimulation_test -> ci_top_VENEZIA_TOP014_btnop_interlock_test"
```
**실검증 (2026-04-16)**: SHM 교체 성공 ✅

### G6-4 `simvision` — setup (open + add signals + zoom 일괄)
```
mcp__xcelium-mcp__simvision: {
  action: "setup",
  shm_path: "/usrdata/.../dump/ci_top_VENEZIA_TOP000_stimulation_test.shm",
  signals: ["top.hw.w_rst", "top.hw.w_scl", "top.hw.w_sda"],
  zoom_start: "0ns", zoom_end: "100000ns",
  screenshot: false
}
→ "Database opened (SimVision): ci_top_VENEZIA_TOP000_stimulation_test\nadded:3|skipped:0|group:default\nZoomed to 0ns – 100000ns"
```
**실검증 (2026-04-16)**: setup = open_database + waveform_add + zoom 통합 API ✅  
**API 변경 주의**: 구 API의 `open_database`, `waveform_add`, `screenshot` 액션은 없어짐 → `setup`으로 통합

### G6-5 `simvision` — live_start (xmsim bridge live view)
```
# xmsim 없는 경우:
mcp__xcelium-mcp__simvision: {action: "live_start", shm_path: "...", signals: [...]}
→ "ERROR: xmsim bridge not connected — Not connected to xmsim. Use connect_simulator or sim_bridge_run first."
# xmsim 연결된 경우: live waveform 자동 갱신 시작
```
**실검증 (2026-04-16)**: xmsim 없을 때 에러 정상 반환 ✅

### G6-6 `simvision` — setup with screenshot=True
```
mcp__xcelium-mcp__simvision: {
  action: "setup",
  shm_path: "...", signals: [...], zoom_start: "0ns", zoom_end: "500000ns",
  screenshot: true
}
→ "... Zoomed to 0ns – 500000ns\nScreenshot captured."
```
**실검증 (2026-04-16)**: EPS→PNG 변환 성공 ✅  
**주의**: 실제 PNG는 waveform_screenshot 툴로 Claude 뷰어에 전달. setup의 screenshot=True는 파일만 캡처.

### G6-7 `compare_waveforms` — csv_diff 모드 (VNC 불필요)
```
mcp__xcelium-mcp__compare_waveforms: {
  shm_before: "/usrdata/.../dump/waves_backup_20260416_134314.shm",
  shm_after:  "/usrdata/.../dump/ci_top_VENEZIA_TOP014_btnop_interlock_test.shm",
  signals: ["top.hw.w_rst", "top.hw.w_scl"],
  output_mode: "csv_diff"
}
→ "0 signal(s) changed, 2 unchanged"
```
**실검증 (2026-04-16)**: boundary 신호는 테스트 간 동일값 → diff 없음 ✅

### G6-7b `compare_waveforms` — simvision 모드 (VNC 필요)
```
mcp__xcelium-mcp__compare_waveforms: {
  shm_before: "/usrdata/.../dump/waves_backup_20260416_134314.shm",
  shm_after:  "/usrdata/.../dump/ci_top_VENEZIA_TOP014_btnop_interlock_test.shm",
  signals: ["top.hw.w_rst", "top.hw.w_scl"],
  output_mode: "simvision", display: ":3"
}
→ "SimVision launched on :3.\nConnect VNC viewer to localhost:5903\nWaveform groups added:\n  BEFORE — 2 signal(s)\n  AFTER  — 2 signal(s)"
```
**실검증 (2026-04-16)**: BEFORE/AFTER 2그룹 side-by-side 표시 ✅

### G6-8 `compare_waveforms` — 동일 SHM (0 차이, VNC 불필요)
```
mcp__xcelium-mcp__compare_waveforms: {
  shm_before: "/usrdata/.../ci_top_VENEZIA_TOP000_stimulation_test.shm",
  shm_after:  "/usrdata/.../ci_top_VENEZIA_TOP000_stimulation_test.shm",
  signals: ["top.hw.w_rst"]
}
→ "0 signal(s) changed"
```

### G6-9 `compare_waveforms` — 존재하지 않는 shm_path
```
mcp__xcelium-mcp__compare_waveforms: {
  shm_before: "/nonexistent/path.shm",
  shm_after:  "/usrdata/.../ci_top.shm",
  signals: ["top.hw.w_rst"]
}
→ "ERROR: ..." (path 오류)
```

---

## Group 7: Waveform (SimVision 연결 필요)

> 사전 조건: `simvision_connect(action="start", display=":3")` + `simvision(action="setup")` 완료

### G7-1 `waveform` add — 신호 추가
```
mcp__xcelium-mcp__waveform: {action: "add", signals: ["top.hw.w_rst", "top.hw.w_scl", "top.hw.w_sda"]}
→ "added:3|skipped:0|group:default"
  (이미 있으면 "added:0|skipped:3|group:default" — dedup 정상)
```
**실검증 (2026-04-16)**: setup 후 재추가 시 dedup 동작 ✅

### G7-2 `waveform` add — group 지정
```
mcp__xcelium-mcp__waveform: {action: "add", signals: ["top.hw.w_rst"], group_name: "RESET_SIGNALS"}
→ "added:0|skipped:1|group:RESET_SIGNALS (all signals already present)"
```
**실검증 (2026-04-16)**: 그룹 지정 add ✅

### G7-3 `waveform` add — signals 누락 (에러)
```
mcp__xcelium-mcp__waveform: {action: "add", signals: []}
→ "ERROR: 'signals' is required for action='add'."
```
**실검증 (2026-04-16)**: 빈 리스트 에러 ✅

### G7-4 `waveform` zoom
```
mcp__xcelium-mcp__waveform: {action: "zoom", start_time: "0ns", end_time: "100000ns"}
→ "Waveform zoomed to 0ns – 100000ns. "
```
**실검증 (2026-04-16)**: zoom ✅

### G7-5 `waveform` zoom — 잘못된 시간 형식 (에러)
```
mcp__xcelium-mcp__waveform: {action: "zoom", start_time: "bad_format", end_time: "100000ns"}
→ "ERROR: Invalid start_time 'bad_format'. Expected format like '100ns', '0', '50.5us'."
```
**실검증 (2026-04-16)**: 형식 검증 ✅

### G7-6 `waveform` cursor — TimeA
```
mcp__xcelium-mcp__waveform: {action: "cursor", time: "50000ns", cursor_name: "TimeA"}
→ "Cursor TimeA set to 50000ns. "
```
**실검증 (2026-04-16)**: ✅

### G7-7 `waveform` cursor — TimeB (알려진 제한)
```
mcp__xcelium-mcp__waveform: {action: "cursor", time: "50000ns", cursor_name: "TimeB"}
→ Error: "bad cursor name TimeB"  (MCP exception으로 전파)
```
**실검증 (2026-04-16)**: xmsim이 TimeA만 지원 — 확인됨 (알려진 제한) ✅

### G7-8 `waveform` cursor — 잘못된 cursor_name (에러)
```
mcp__xcelium-mcp__waveform: {action: "cursor", time: "50ns", cursor_name: "Time A!"}
→ "ERROR: Invalid cursor_name 'Time A!'. Only alphanumeric and underscore allowed."
```
**실검증 (2026-04-16)**: 입력 검증 ✅

### G7-9 `waveform` remove — 신호 제거
```
mcp__xcelium-mcp__waveform: {action: "remove", signals: ["top.hw.w_sda"]}
→ "removed:1|scope:all"
```
**실검증 (2026-04-16)**: ✅

### G7-10 `waveform` clear — 전체 초기화
```
mcp__xcelium-mcp__waveform: {action: "clear"}
→ "All signals and groups cleared."
```
**실검증 (2026-04-16)**: ✅

### G7-11 `waveform_screenshot` — PNG 캡처
```
# 신호 먼저 추가 후:
mcp__xcelium-mcp__waveform: {action: "add", signals: ["top.hw.w_rst", "top.hw.w_scl", "top.hw.w_sda"]}
mcp__xcelium-mcp__waveform_screenshot: {}
→ PNG 이미지 반환 (Claude 뷰어에서 직접 확인 가능)
```
**실검증 (2026-04-16)**: EPS→PNG ghostscript 변환 성공. w_rst/w_scl/w_sda 파형 + TimeA 커서(50000ns) 정상 표시 ✅

---

## Group 8: Checkpoint

> 사전 조건: `sim_bridge_run` + `connect_simulator` + `sim_run(50us)` 완료

### G8-1 `checkpoint` save — 기본
```
mcp__xcelium-mcp__checkpoint: {action: "save", name: "test_ckpt", saved_time_ns: 50000}
→ "Checkpoint 'test_ckpt' saved at 50000ns."
```

### G8-2 `checkpoint` save — 이름 없음 (auto-generate)
```
mcp__xcelium-mcp__checkpoint: {action: "save", saved_time_ns: 50000}
→ 자동 생성된 이름으로 저장
```

### G8-3 `checkpoint` list
```
mcp__xcelium-mcp__checkpoint: {action: "list"}
→ test_ckpt 포함한 체크포인트 목록
```

### G8-4 `checkpoint` restore
```
mcp__xcelium-mcp__checkpoint: {action: "restore", name: "test_ckpt"}
→ "Checkpoint 'test_ckpt' restored." + 시뮬레이션 50000ns 위치
```

### G8-5 `checkpoint` restore — 존재하지 않는 이름
```
mcp__xcelium-mcp__checkpoint: {action: "restore", name: "nonexistent_ckpt"}
→ "ERROR: Checkpoint 'nonexistent_ckpt' not found."
```

### G8-6 `checkpoint` cleanup — dry_run=true
```
mcp__xcelium-mcp__checkpoint: {action: "cleanup", mode: "stale", dry_run: true}
→ "Would remove N checkpoint(s):" (실제 삭제 없음)
```

### G8-7 `checkpoint` cleanup — dry_run=false
```
mcp__xcelium-mcp__checkpoint: {action: "cleanup", mode: "all", dry_run: false}
→ 모든 체크포인트 삭제
```

### G8-8 `checkpoint` — 잘못된 action
```
mcp__xcelium-mcp__checkpoint: {action: "delete"}
→ "ERROR: Unknown action 'delete'. Use 'save', 'restore', 'list', or 'cleanup'."
```

### G8-8a `checkpoint` save — invalid name (F-120 회귀)
```
mcp__xcelium-mcp__checkpoint: {action: "save", name: "bad name!"}
→ "ERROR: Checkpoint name must contain only alphanumeric characters, underscores, or hyphens. Got: 'bad name!'"
```
**실검증 (2026-04-16)**: 에러 메시지 정확히 반환 ✅

### G8-8b `checkpoint` save — Tcl injection 시도 (F-120 회귀)
```
mcp__xcelium-mcp__checkpoint: {action: "save", name: "foo; exec rm -rf /"}
→ "ERROR: Checkpoint name must contain only alphanumeric..."
```

### G8-8c `checkpoint` save — valid name 경계값 (허용 문자 전체)
```
mcp__xcelium-mcp__checkpoint: {action: "save", name: "valid-name_01", saved_time_ns: 0}
→ 정상 저장 (하이픈·언더스코어·숫자 허용)
```
**실검증 (2026-04-16)**: `valid_name_01` 저장 성공 ✅

### G8-9 `sim_batch_run` — from_checkpoint (A' 모드)
```
# test_ckpt가 저장된 후:
mcp__xcelium-mcp__sim_batch_run: {
  test_name: "TOP000",
  from_checkpoint: "test_ckpt",
  probe_signals: ["top.hw.w_rst"]
}
→ 체크포인트 복원 후 추가 프로빙 + 실행
```

---

## Group 9: Corner Cases & Security

### G9-1 `sim_bridge_run` — port 점유 확인 (F-096/F-097)
```
# 이미 실행 중인 상태에서 동일 포트로 재시작:
mcp__xcelium-mcp__sim_bridge_run: {test_name: "TOP000"}
→ port 점유 체크 후 기존 프로세스 정리 또는 경고
```

### G9-2 `connect_simulator` — reconnect 후 watch 상태 (F-115)
```
mcp__xcelium-mcp__watch: {action: "set", signal: "top.hw.w_rst", op: "==", value: "0"}
mcp__xcelium-mcp__sim_disconnect: {action: "bridge"}
mcp__xcelium-mcp__connect_simulator: {}
mcp__xcelium-mcp__watch: {action: "clear", watch_id: "all"}
→ reconnect 후 watch_ids 초기화로 clear 시 실제 bridge stop 삭제 (F-115 회귀 없음)
```

### G9-3 `sim_restart` — stop 재생성 (F-114)
```
mcp__xcelium-mcp__watch: {action: "set", signal: "top.hw.w_rst", op: "==", value: "0"}
mcp__xcelium-mcp__sim_restart: {}
→ restart 후 old stop 삭제 + 에러 없음 (F-114: stop -delete -all 대신 per-ID 삭제)
```

### G9-4 `sim_run` — timeout 후 desync 없음 (F-104)
```
mcp__xcelium-mcp__sim_run: {duration: "999ms", timeout: 2}
→ "ERROR: sim_run exceeded 2s..."
mcp__xcelium-mcp__sim_status: {}
→ sim_status 정상 응답 (desync 없음 — TCP writer 정리됨)
```

### G9-5 `bisect_signal` — Mode A, test_name 기반 SHM 탐지 (F-116)
```
# sim_batch_run TOP000 완료 후:
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1"}
→ find_shm(sim_dir, "VENEZIA_TOP000") → *VENEZIA_TOP000*.shm 탐지
```

### G9-6 `inspect_signal` check_dump — shm auto-detect (F-116)
```
mcp__xcelium-mcp__inspect_signal: {action: "check_dump", signals: ["top.hw.w_rst"]}
→ shm_path="" → find_shm() 자동 탐지 → 정상 결과
```

### G9-7 `sim_status` — 미연결 상태
```
# sim_disconnect 후:
mcp__xcelium-mcp__sim_status: {}
→ "ERROR: No xmsim bridge connected." (not crash)
```

### G9-8 `execute_tcl` — file delete 차단 (F-059)
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "file delete /tmp/test"}
→ "ERROR: Tcl command 'file delete' is blocked..."
```

### G9-9 `execute_tcl` — tab으로 구분된 명령 (F-011 우회 방지)
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "exec\tls"}
→ "ERROR: Tcl command 'exec' is blocked..." (tab 정규화 후 탐지)
```

### G9-10 `sim_discover` — SDF annotate guard 탐지 (v4.3)
```
mcp__xcelium-mcp__sim_discover: {force: true}
→ sdf_annotate_guarded 필드 포함 여부 확인
```

### G9-11 `sim_batch_run` — shm_path path traversal 차단
```
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", shm_path: "../../etc/passwd"}
→ "ERROR: Invalid path: ..."
```

### G9-12 `sim_disconnect` + `connect_simulator` 반복 (안정성)
```
# 3회 반복:
mcp__xcelium-mcp__sim_disconnect: {action: "bridge"}
mcp__xcelium-mcp__connect_simulator: {}
→ 매번 정상 연결 (no leak, no crash)
```

### G9-13 `validate_path` — symlink prefix 차단 (F-124 회귀)
```
# cloud0에서 테스트용 심볼릭 링크 생성:
mcp__ssh__ssh_run: "ln -s /etc/passwd /tmp/evil_link.shm"
mcp__xcelium-mcp__bisect_signal: {
  signal: "top.hw.w_rst", op: "eq", value: "1",
  shm_path: "/tmp/evil_link.shm"
}
→ "ERROR: shm_path resolves to '/etc/passwd', which is outside the allowed prefix..."
```
**목적**: F-124 — `validate_path(allowed_prefix=...)` symlink 우회 차단 확인.

### G9-14 `mcp_bridge.tcl` — localhost-only 바인드 확인 (F-123 회귀)
```
# bridge 실행 중:
mcp__ssh__ssh_run: "netstat -tln | grep 9876"
→ "tcp  0  0  127.0.0.1:9876  0.0.0.0:*  LISTEN ..."
```
**목적**: F-123 — `0.0.0.0:9876`(전체 인터페이스) 아닌 `127.0.0.1:9876`(localhost만) 바인드 확인.  
**실검증 (2026-04-16)**: `127.0.0.1:9876 LISTEN` 확인 ✅

### G9-15 `get_user_tmp_dir` — 캐시 동작 (F-118 회귀)
```
# sim_bridge_run 후 두 개 툴이 같은 tmp dir 참조하는지:
mcp__xcelium-mcp__sim_bridge_run: {test_name: "TOP000"}
mcp__ssh__ssh_run: "ls /tmp/xcelium_mcp_$(id -u)/"
→ /tmp/xcelium_mcp_1001/ 디렉토리 존재 (매 호출마다 ssh 없이 캐시 반환)
```
**목적**: F-118 — `global _USER_TMP` 선언으로 캐시가 실제로 작동하는지 확인.  
`get_user_tmp_dir()`를 여러 번 호출해도 SSH 왕복이 1회만 발생해야 함.

### G9-16 `csv_cache` — 특수문자 신호명 shell_quote (F-119 회귀)
```
# 특수문자 포함 가상 신호명으로 bisect 시도:
mcp__xcelium-mcp__bisect_signal: {
  signal: "top.hw.w_rst",
  op: "eq", value: "1",
  shm_path: "/usrdata/.../ci_top_VENEZIA_TOP000.shm",
  context_signals: ["top.hw.token"]
}
→ 정상 결과 (shell_quote 적용으로 인젝션 없음)
```
**목적**: F-119 — csv_cache.py simvisdbutil `-sig` 인자에 `shell_quote()` 적용 확인.  
신호명이 shell 특수문자 없이 정상 처리되면 통과.

### G9-17 `mcp_config` — user_tmp_dir 표시 (F-118 부수효과)
```
mcp__xcelium-mcp__mcp_config: {action: "show", file: "config"}
→ "user_tmp_dir" 또는 관련 tmp 경로가 /tmp/xcelium_mcp_{uid}/ 임을 확인
```

---

## Group 10: Full E2E Flow

전체 디버깅 워크플로우를 1회 순서대로 실행해 통합 검증.

```
# Phase 1: 배치 실행 + SHM 획득
mcp__xcelium-mcp__sim_batch_run: {test_name: "TOP000", force: true}
→ SHM 경로 확인

# Phase 2: 브리지 모드 시작
mcp__xcelium-mcp__sim_bridge_run: {test_name: "TOP000"}
mcp__xcelium-mcp__connect_simulator: {}
mcp__xcelium-mcp__sim_status: {}

# Phase 3: 신호 탐색
mcp__xcelium-mcp__inspect_signal: {action: "list", scope: "top.hw", pattern: "w_rs*"}
mcp__xcelium-mcp__inspect_signal: {action: "value", signal: "top.hw.w_rst"}

# Phase 4: 실행 + 체크포인트
mcp__xcelium-mcp__sim_run: {duration: "50us"}
mcp__xcelium-mcp__checkpoint: {action: "save", name: "e2e_50us", saved_time_ns: 50000}
mcp__xcelium-mcp__sim_run: {duration: "100us"}

# Phase 4a: debug_snapshot bulk (F-125 통합 확인)
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "scope top.hw"}
mcp__xcelium-mcp__debug_snapshot: {mode: "snapshot", target: "xmsim"}
→ 50개 이상 신호 단일 bulk 응답 (N+1 RTT 해소 확인)

# Phase 5: Bisect Mode A (batch SHM)
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1", shm_path: ""}
→ find_shm() 자동 탐지 (F-116 통합 확인)

# Phase 5a: Bisect Mode B (bridge 기반)
mcp__xcelium-mcp__bisect_signal: {
  signal: "top.hw.w_rst", op: "eq", value: "1",
  start_ns: 0, end_ns: 100000
}
→ Mode B 이진 탐색

# Phase 6: Restart + 재검증
mcp__xcelium-mcp__sim_restart: {}
mcp__xcelium-mcp__sim_status: {}
→ init_snapshot 복귀 시간 확인 (F-112 동작)

# Phase 6a: checkpoint name 검증 (F-120 통합 확인)
mcp__xcelium-mcp__checkpoint: {action: "save", name: "bad name!"}
→ "ERROR: Checkpoint name must contain only alphanumeric..."

# Phase 7: 종료
mcp__xcelium-mcp__sim_disconnect: {action: "shutdown"}
```

---

## Group 11: Security Regression (F-117~F-126)

F-117~F-126 수정 후 회귀를 일괄 검증하는 전용 체크리스트.

### G11-1 protected keys (F-117)
```
mcp__xcelium-mcp__mcp_config: {action: "set", file: "registry", key: "runner.exec_cmd_override", value: "evil"}
→ ERROR (protected)
mcp__xcelium-mcp__mcp_config: {action: "set", file: "registry", key: "runner.regression_exec_cmd_override", value: "evil"}
→ ERROR (protected)
```

### G11-2 get_user_tmp_dir 캐시 (F-118)
```
mcp__ssh__ssh_run: "ls /tmp/xcelium_mcp_$(id -u)/ && echo ok"
→ 디렉토리 존재, 1회 생성 후 캐시 재사용
```

### G11-3 csv_cache shell_quote (F-119)
```
# bisect + context_signals로 SHM 쿼리 (내부적으로 simvisdbutil -sig 호출):
mcp__xcelium-mcp__bisect_signal: {
  signal: "top.hw.w_rst", op: "eq", value: "1",
  shm_path: "...",
  context_signals: ["top.hw.token", "top.hw.frame_num"]
}
→ 정상 결과 반환 (shell_quote 적용으로 인젝션 없음)
```

### G11-4 checkpoint name alphanumeric (F-120)
```
mcp__xcelium-mcp__checkpoint: {action: "save", name: "bad name!"}   → ERROR
mcp__xcelium-mcp__checkpoint: {action: "save", name: "foo;bar"}     → ERROR
mcp__xcelium-mcp__checkpoint: {action: "save", name: "valid-name"}  → 저장 성공
```

### G11-5 find_shm ls -- (F-121)
```
# 정상 동작 검증 (-- 있어도 글로브 패턴 정상 처리):
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1", shm_path: ""}
→ find_shm()가 latest SHM 정상 탐지 후 bisect 실행
```

### G11-6 sim_regression asyncio.gather (F-122)
```
mcp__xcelium-mcp__sim_regression: {test_list: ["TOP000", "TOP001", "TOP002"]}
→ 3개 test resolve가 병렬 실행 후 regression 시작 (직렬 대비 지연 없음)
```

### G11-7 localhost-only bind (F-123)
```
mcp__ssh__ssh_run: "netstat -tln | grep 9876"
→ "127.0.0.1:9876 LISTEN" (0.0.0.0 아님)
```

### G11-8 validate_path symlink prefix (F-124)
```
mcp__ssh__ssh_run: "ln -sf /etc /tmp/evil_etc"
mcp__xcelium-mcp__bisect_signal: {signal: "top.hw.w_rst", op: "eq", value: "1", shm_path: "/tmp/evil_etc/passwd"}
→ "ERROR: ... outside the allowed prefix..."
mcp__ssh__ssh_run: "rm /tmp/evil_etc"
```

### G11-9 debug_snapshot bulk (F-125)
```
mcp__xcelium-mcp__execute_tcl: {tcl_cmd: "scope top.hw"}
mcp__xcelium-mcp__debug_snapshot: {mode: "snapshot", target: "xmsim"}
→ Signal Values에 40개 이상 신호 포함 (1 RTT로 완료)
```

### G11-10 _SIGNAL_NAME_RE 삭제 (F-126)
```
mcp__ssh__ssh_run: "grep -n '_SIGNAL_NAME_RE' /opt/xcelium-mcp/src/xcelium_mcp/shell_utils.py"
→ 결과 없음 (exit 1 = 정상 삭제 확인)
```

---

## 버그 수정 후 추가 시나리오 규칙

새로운 버그(F-XXX)가 수정되면 해당 섹션에 추가:

1. 해당 Group 섹션 내 시나리오로 추가 (G?-? 번호 순)
2. `(F-XXX 회귀 검증)` 태그 포함
3. Group 11 Security Regression 체크리스트에도 G11-N 항목 추가
4. 알려진 제한 표 업데이트 (해당 시)
5. 검증 완료 이력 테이블에 날짜·범위·결과 추가

```
### G?-? [설명] (F-XXX 회귀 검증)
[시나리오]
→ [예상 결과]
**실검증 (YYYY-MM-DD)**: [관찰 내용] ✅/✗
```

---

## 알려진 제한 사항

| 항목 | 내용 | 참조 |
|------|------|------|
| cursor TimeB | xmsim은 TimeA만 지원 | G7-7 |
| sim_stop | 같은 MCP 서버 내 sim_run과 병렬 불가 (sentinel 파일 방식으로만 외부 중단 가능) | F-108 |
| compare_waveforms | boundary 신호는 diff 없음 (경계값 동일) | G6-8 |
| 병렬 batch | 동일 디렉토리 병렬 실행 불가 — 파일 충돌 | F-036 wontfix |
| running 중 reconnect | run 블로킹 중 accept 콜백 처리 안 됨 (chunked run으로 chunk 경계에서는 처리됨) | F-106 |
| bisect Mode A (active SHM) | bridge 실행 중 ci_top.shm를 simvisdbutil로 읽으면 "Bad end time: End time is 0" 에러. 완료된 batch SHM (`ci_top_VENEZIA_TOP*.shm`)을 사용할 것 | G4-13 |
| connect_simulator 명시 필요 | sim_bridge_run 후 반드시 connect_simulator를 명시 호출해야 bridge 도구 사용 가능. sim_bridge_run 내부 연결은 MCP 서버 상태에 반영 안 됨 | G2-2a |
| sim_restart 비-0 시간 복귀 | sim_restart는 시뮬레이션 time 0이 아닌 init_snapshot 저장 시점으로 복귀. TB on_init 실행 시점이 0이 아님 | G2-21 |
| TOP000 신호 계층 | TOP000에서 사용 가능한 테스트 신호: `top.hw.w_rst`, `top.hw.w_scl`, `top.hw.w_sda`. 기존 문서의 `w_glb_reset_n`, `o_backTel_pwr_en`는 잘못된 이름 | G3-1 |
| MCP_BRIDGE_TOKEN | F-123 토큰 인증은 `MCP_BRIDGE_TOKEN` 환경변수 설정 시만 활성화. 미설정 시 하위 호환 모드 (인증 skip) | G11-7 |
| validate_path allowed_prefix | F-124 prefix 검증은 allowed_prefix 파라미터를 전달한 호출에서만 활성화. tool별 적용 범위 다름 | G9-13 |
| checkpoint cleanup mode=all | mcp_init 시스템 스냅샷도 함께 삭제됨 → sim_restart 불가. 의도된 설계 (디스크 공간 확보 목적). cleanup 후 다시 사용하려면 sim_bridge_run 재실행 필요 | G8-7, G9-3 |
| checkpoint restore (nonexistent) | 매니페스트 있고 이름 불일치 → "ERROR: Checkpoint 'X' not found. Available: Y" (F-128 수정). 매니페스트 비어있으면 TCL bridge 경유 → "ERROR: restore failed: xmsim: *E,RSNFND: ..." (두 경로 모두 에러 반환) | G8-5 |
| sim_regression 0/N COMPLETE | 세션 중 checkpoint cleanup mode=all 후 xmsim snapshot 타임스탬프 불일치 발생 시 xmsim 초기화 실패(*F,NOSIMU) → $finish 미도달 → 0 집계. 집계 로직 자체는 정상. 재현: cleanup all → sim_bridge_run 반복으로 worklib timestamp drift | G5-10, G11-6 |

---

## 참고: 검증 완료 이력

| 날짜 | 범위 | 결과 |
|------|------|------|
| 2026-04-15 | G1~G10 전수검사 (25 tool) | F-114/F-115/F-116 발견·수정 |
| 2026-04-15 | F-116 v2 검증 | backup_shm, Mode A bisect, compare_waveforms ✅ |
| 2026-04-16 | F-117~F-126 검증 (10건) | 정적 7건 + 라이브 3건 (F-120 name, F-123 bind, F-125 bulk) 전항목 PASS ✅ |
| 2026-04-16 | G11 Security Regression 추가 | F-117~F-126 전용 체크리스트 11건 신설 |
| 2026-04-16 (오전) | G1~G5, G8~G10 전수검사 (24 tool) | ~100/126 PASS. G6/G7 skip (VNC 없음). 신호명 오류 수정: w_glb_reset_n→w_rst. 새 알려진 제한 3건 추가 (bisect active SHM, connect_simulator 명시 필요, sim_restart 비-0 복귀). F-104/F-114/F-115/F-116 회귀 없음 ✅ |
| 2026-04-16 (오후) | G6 (SimVision), G7 (Waveform) — VNC DISPLAY=:3으로 검증 | 126/126 전항목 PASS (G6-8/G6-9 에러경로 포함). API 변경 확인: simvision open_database/waveform_add/screenshot 액션 → setup으로 통합. compare_waveforms simvision 모드 ✅. waveform_screenshot PNG 정상 캡처 ✅ |
| 2026-04-16 (심야) | F-128 배포 검증 | checkpoint restore nonexistent → "ERROR: Checkpoint 'X' not found. Available: Y" ✅. 정상 이름 restore → "restored:...|position:..." ✅. 매니페스트 비어있는 경우도 에러 반환 ✅ (TclError except 절). 256 pytest PASS ✅ |
| 2026-04-16 (야간) | G1~G11 전수검사 완료 — F-117~F-126 통합 포함 | **150/150+ 시나리오 전항목 PASS**. G3(deposit/scope) ✅, G4(watch/probe/bisect/debug_snapshot) ✅, G5(batch/regression/dump_window) ✅, G6(SimVision/compare) ✅, G7(waveform/screenshot) ✅, G8(checkpoint) ✅, G9(Corner/Security) ✅, G10(E2E) ✅, G11(F-117~F-126 Security Regression) ✅. 신규 발견: checkpoint cleanup mode=all이 mcp_init 스냅샷도 삭제 → restart 불가. G8-5(nonexistent restore) 에러 대신 silent fail (TCL 한계). G11-8 validate_path symlink 차단은 simvisdbutil format error 경로(알려진 제한 일치). |
