# xcelium-mcp-sim-session-reaper Design Document

> **Summary**: bridge 모드 xmsim 세션의 활동시각을 레지스트리에 기록하고, TTL을 넘게 방치된 세션을
> cron reaper가 자동으로 안전 종료(`__SHUTDOWN__`)한다. 가시성 tool로 TTL 만료 전에도 수동 확인 가능.
>
> **Project**: xcelium-mcp
> **Author**: hoseung.lee
> **Date**: 2026-07-07
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-sim-session-reaper.plan.md](../01-plan/features/xcelium-mcp-sim-session-reaper.plan.md)

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 실제 발생한 host disk 소진 사고(검증 완료 후 수 주 방치된 xmsim)의 재발 방지 |
| **WHO** | xcelium-mcp를 bridge 모드로 사용하는 모든 사용자/agent(`verilog-rtl-debugger` 포함) |
| **RISK** | TTL을 너무 짧게 잡으면 정말 장시간 필요한 세션을 실수로 죽일 위험 — 설정 가능한 TTL + 넉넉한 기본값(48h)으로 완화 |
| **SUCCESS** | 방치된 bridge 세션이 TTL 경과 후 자동으로 안전 종료됨을 실증(pytest + 실배포 검증) |
| **SCOPE** | 레지스트리 활동시각 기록 + cron reaper(자동 종료) + 가시성 tool(수동 확인/종료 지원) |

---

## 1. Overview

### 1.1 Design Goals

- bridge 세션의 "마지막 활동 시각"을 정확하고 저비용으로 추적한다.
- TTL을 넘게 방치된 세션을 사람의 개입 없이 안전하게(SHM 보존) 종료한다.
- TTL 만료 전에도 사람이 방치 세션을 확인·수동 정리할 수 있는 가시성을 제공한다.
- F-A(수퍼바이저)/F-B(idle-culler)/F-C(레지스트리)/F-D(세션 상태 복원)가 이미 검증한 패턴과 인프라를
  그대로 재사용해 회귀 위험을 최소화한다.

### 1.2 Design Principles

- **기존 인프라 우선 재사용** — 새 저장소·새 프로토콜을 만들지 않는다. registry.py(F-C/F-D)에
  필드만 추가하고, idle_culler.py와 동일한 cron 패턴을 그대로 복제한다.
- **보수적 안전장치** — TTL 판정은 연속 2회 확인 후에만 종료를 실행해, 레지스트리 읽기/쓰기
  타이밍 경합으로 인한 오탐(false positive kill)을 방지한다.
- **batch/regression과 완전히 분리** — 이 reaper는 bridge 세션(`bridge_port` 필드가 있는 레지스트리
  엔트리)만 대상으로 하며, `batch_job.json`/`regression_job.json` 기반 작업에는 관여하지 않는다
  (F-E가 이미 그쪽을 보호 중).

---

## 2. Architecture

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal | Option B: Clean | Option C: Pragmatic (선택됨) |
|----------|:-:|:-:|:-:|
| **Approach** | throttle 로직을 `tcl_bridge.py`에 인라인, reaper를 `idle_culler.py`에 함수로 통합 | `activity_tracker.py`(후로틀링 전담) + reaper 패키지화(pluggable shutdown strategy) + 가시성 tool 별도 모듈 | `registry.py`에 필드+헬퍼 추가, reaper는 `idle_culler.py`와 동일 패턴의 신규 `sim_session_reaper.py`, 가시성 tool은 기존 `sim_lifecycle.py`에 추가 |
| **신규 파일** | 0 | 3 | 1 |
| **변경 파일** | 3 | 2 | 3 |
| **복잡도** | 낮음 | 높음 | 중간 |
| **유지보수성** | 낮음(idle_culler 책임 커짐) | 높음 | 높음 |
| **공수** | 낮음 | 높음 | 중간 |
| **리스크** | 낮음(단, idle_culler 회귀 범위 확대) | 낮음(과잉설계) | 낮음(F-D/F-E와 동일 패턴, 이미 검증됨) |

**선택**: Option C — **근거**: 이번 세션에서 F-D/F-E가 이미 "registry.py에 헬퍼 추가 + idle_culler와
같은 cron 패턴의 독립 모듈"이라는 조합을 사용해 100% Match Rate로 검증했다. 같은 조합을 재사용하면
검증된 안전성을 그대로 물려받고, reaper가 idle_culler와 섞이지 않아 각자의 책임(worker 정리 vs
xmsim 정리)이 명확히 분리된다.

### 2.1 Component Diagram

```
┌──────────────────┐   execute()/execute_safe()   ┌──────────────────┐
│  tools/*.py       │ ───────────────────────────▶ │  tcl_bridge.py    │
│  (MCP tool 호출)  │                               │  TclBridge         │
└──────────────────┘                               └────────┬───────────┘
                                                              │ (throttled)
                                                              ▼
                                                     ┌──────────────────┐
                                                     │  registry.py      │
                                                     │  environments[sim_dir]
                                                     │  .last_activity    │
                                                     └────────┬───────────┘
                                                              │ (주기적 순회)
                                                              ▼
                                        cron (30~60분)  sim_session_reaper.py
                                                              │ TTL 초과 세션 발견
                                                              ▼
                                                  TclBridge(직접 연결) → __SHUTDOWN__
                                                              │
                                                              ▼
                                                  registry.py 항목 정리

┌──────────────────┐
│ tools/sim_lifecycle.py │  list_active_sessions()  ──▶  registry.py 읽기 전용 조회
└──────────────────┘
```

### 2.2 Data Flow

```
bridge 명령 실행 → (스로틀 통과 시) registry.last_activity 갱신
                                         │
cron(sim_session_reaper) 주기 실행 ──────┘
     → TTL 비교 → 초과 & 연속 2회 확인 → __SHUTDOWN__ 전송 → registry 항목 삭제
                → 미초과 → 아무 것도 안 함
     → 포트 접속 실패(고아 엔트리) → registry 항목만 삭제
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `sim_session_reaper.py` | `registry.py`, `tcl_bridge.py` | TTL 판정 + 안전 종료 |
| `tcl_bridge.py` (execute 훅) | `registry.py` | 활동시각 갱신(스로틀링) |
| `tools/sim_lifecycle.py` (가시성 tool) | `registry.py` | 세션 목록 조회(읽기 전용) |

---

## 3. Data Model

### 3.1 레지스트리 스키마 확장

```jsonc
// mcp_registry.json (기존 F-C/F-D 스키마에 필드 2개 추가)
{
  "projects": {
    "<project_root>": {
      "environments": {
        "<resolved_sim_dir>": {
          "bridge_port": 9876,                 // F-C(기존)
          "current_test_name": "TOP015",       // F-D(기존)
          "current_tb_source": { ... },        // F-D(기존)
          "last_activity": 1783500000,         // F-2(신규) — epoch 초, bridge 명령 실행 시 갱신
          "ttl_miss_count": 0                  // F-2(신규) — 연속 TTL-초과 감지 횟수(오탐 방지용)
        }
      }
    }
  }
}
```

- `last_activity`가 없는(레거시) 엔트리는 reaper가 "활동 기록 없음 = 방금 생긴 것으로 간주"하고
  최초 1회는 건드리지 않는다(레지스트리 마이그레이션 이슈로 기존 세션이 즉시 죽는 것을 방지).
- `ttl_miss_count`는 reaper 실행마다 갱신 — TTL 초과 감지 시 +1, 미초과 시 0으로 리셋. 2 이상일 때만
  실제 종료를 실행한다(§4.2).

---

## 4. Core Logic

### 4.1 활동시각 갱신 (registry.py + tcl_bridge.py)

```python
# registry.py — 신규 헬퍼
_ACTIVITY_THROTTLE_SEC = 60  # 최소 이 간격 이상 지나야 실제 디스크 쓰기 발생

async def touch_activity(sim_dir: str) -> None:
    """bridge 명령 실행 시 호출. 스로틀링으로 과도한 I/O 방지."""
    ...
```

```python
# tcl_bridge.py — TclBridge.execute()/execute_safe() 진입부에서 호출
# (sim_dir을 아는 컨텍스트에서만 — bridge 인스턴스 생성 시 sim_dir을 이미 보관하고 있음, F-C 참조)
await touch_activity(self._sim_dir)
```

- 스로틀링 기준: 마지막 기록 이후 `_ACTIVITY_THROTTLE_SEC`(기본 60초) 미만이면 갱신을 스킵 — 매
  `sim_run`/`inspect_signal` 호출마다 디스크 쓰기가 발생하지 않도록 한다.
- 실패(디스크 쓰기 오류 등)는 `OSError`를 삼키고 무시 — 활동 기록 실패가 실제 tool 호출을 막아서는
  안 된다(F-D의 기존 `try/except OSError: pass` 관례와 동일).

### 4.2 reaper 알고리즘 (`sim_session_reaper.py`, idle_culler.py와 동일 패턴)

```python
DEFAULT_TTL_HOURS = 48
TTL_ENV_VAR = "XCELIUM_MCP_SIM_TTL_HOURS"
MIN_MISS_COUNT_TO_KILL = 2  # 연속 2회 확인 후에만 종료

def _effective_ttl_seconds() -> int:
    hours = float(os.environ.get(TTL_ENV_VAR, DEFAULT_TTL_HOURS))
    return int(hours * 3600)

async def reap_idle_sessions() -> list[str]:
    """레지스트리를 순회해 TTL 초과 bridge 세션을 안전 종료. 종료된 sim_dir 목록 반환."""
    registry = await _load_registry_sync_async()
    ttl = _effective_ttl_seconds()
    now = time.time()
    reaped = []
    for project_root, proj in registry.get("projects", {}).items():
        for sim_dir, env in proj.get("environments", {}).items():
            port = env.get("bridge_port")
            if not port:
                continue  # bridge 세션이 아님 (또는 F-C 엔트리 없음)
            last_activity = env.get("last_activity")
            if last_activity is None:
                continue  # 레거시 엔트리, 최초 1회는 건드리지 않음(활동 기록 시작 대기)
            if now - last_activity <= ttl:
                env["ttl_miss_count"] = 0
                continue
            env["ttl_miss_count"] = env.get("ttl_miss_count", 0) + 1
            if env["ttl_miss_count"] < MIN_MISS_COUNT_TO_KILL:
                continue  # 아직 연속 확인 횟수 부족 — 이번 라운드는 건드리지 않음
            # TTL 초과 + 연속 확인 완료 → 안전 종료 시도
            await _shutdown_and_cleanup(project_root, sim_dir, port, registry)
            reaped.append(sim_dir)
    await _save_registry_sync_async(registry)
    return reaped

async def _shutdown_and_cleanup(project_root, sim_dir, port, registry) -> None:
    try:
        bridge = TclBridge(host="localhost", port=port)
        await bridge.connect()
        await bridge.execute_safe("__SHUTDOWN__")
    except (ConnectionError, asyncio.TimeoutError, OSError):
        pass  # 포트가 이미 죽어있음(고아 엔트리) — 정리만 진행
    finally:
        del registry["projects"][project_root]["environments"][sim_dir]
```

- `MIN_MISS_COUNT_TO_KILL=2` + cron 실행 주기(30~60분, §7)를 곱하면 **최소 30~60분의 유예**가
  생겨, reaper 실행 시점과 사용자의 실제 명령 사이의 타이밍 경합(예: 방금 명령을 보냈는데 아직
  `last_activity`가 디스크에 반영되지 않은 순간)으로 인한 오탐을 흡수한다.
- batch/regression job(`bridge_port` 필드가 애초에 없음)은 이 루프에서 자연히 스킵된다 — F-E와
  간섭 없음.

### 4.3 가시성 tool (`tools/sim_lifecycle.py`)

```python
@mcp.tool()
async def list_active_sessions() -> str:
    """모든 project의 bridge 세션(sim_dir, port, test_name, 마지막 활동, TTL까지 남은 시간)을 조회.

    idle-reaper가 자동으로 정리하기 전에, 방치된 세션을 사람이 직접 확인하고
    sim_disconnect(action="shutdown")로 수동 정리할 수 있게 한다.
    """
    ...  # registry.py 읽기 전용 순회, TTL 설정값과 대조해 "남은 시간" 계산 후 표 형태로 반환
```

- 읽기 전용 — 레지스트리를 변경하지 않는다.
- 각 세션에 대해 `sim_dir`, `port`, `test_name`, `last_activity`(사람이 읽을 수 있는 형식),
  `ttl_remaining`(예: "36h 12m" 또는 "TTL 초과, 다음 reaper 실행 시 종료 예정")을 출력.

---

## 5. Error Handling

| 상황 | 처리 |
|------|------|
| `last_activity` 갱신 중 디스크 오류 | 무시(`OSError` catch) — tool 호출 자체는 정상 진행 |
| reaper 실행 중 레지스트리 파일 손상(JSON 파싱 실패) | 해당 라운드는 아무 것도 하지 않고 조용히 종료(idle_culler의 기존 관례와 동일 — 다음 라운드에 재시도) |
| reaper의 `__SHUTDOWN__` 전송 후 응답 없음/타임아웃 | 정상 처리 경로로 간주 — 어차피 레지스트리 항목은 정리(포트가 죽어있었을 가능성이 높음) |
| TTL 환경변수 값이 숫자가 아님 | `float()` 변환 실패 시 `DEFAULT_TTL_HOURS`로 폴백 |

---

## 6. Security Considerations

- reaper는 `localhost`의 이미 알려진 포트에만 접속한다 — 외부 노출 없음(기존 xcelium-mcp bridge
  전체가 localhost 전용이라는 전제와 동일, F-A Design 참조).
- `__SHUTDOWN__`은 기존에 이미 노출된 meta command이며 새 권한을 추가하지 않는다.

---

## 7. Test Plan

### 7.1 Test Scope

| # | 테스트 | 검증 |
|---|--------|------|
| T-1 | bridge 명령 실행 시 `last_activity` 갱신 | pytest — throttle 미적용 시 즉시 갱신 확인 |
| T-2 | throttle 윈도우 내 재호출 시 갱신 스킵 | pytest — 연속 호출 후 타임스탬프 불변 확인 |
| T-3 | TTL 초과 + `ttl_miss_count` 2회 도달 시 `__SHUTDOWN__` 전송 + 레지스트리 정리 | pytest(TclBridge 모킹) |
| T-4 | TTL 초과 1회차(아직 2회 미달) → 종료하지 않고 `ttl_miss_count`만 증가 | pytest |
| T-5 | TTL 미초과 → `ttl_miss_count` 0으로 리셋, 아무 것도 안 함 | pytest |
| T-6 | `last_activity` 없는 레거시 엔트리 → 이번 라운드는 건드리지 않음 | pytest |
| T-7 | 포트가 이미 죽은 고아 엔트리 → 접속 실패해도 크래시 없이 레지스트리만 정리 | pytest |
| T-8 | `bridge_port` 없는(batch/regression) 엔트리 → reaper가 완전히 무시 | pytest |
| T-9 | `list_active_sessions`가 세션 목록 + TTL 잔여시간을 정확히 반환 | pytest |
| T-10 | TTL 환경변수 미설정/잘못된 값 → 기본값(48h) 폴백 | pytest |
| T-11 | 실배포: cloud0에 cron 등록, 짧은 TTL(예: 2분)로 재현해 실제 방치 xmsim이 자동 종료됨을 확인 | 실측(SSH) |
| T-12 | 회귀 | 기존 pytest 스위트 전체 통과 |

### 7.2 Seed / Fixture 요구사항

- pytest는 `MockTclServer`(기존 테스트 인프라, `tests/conftest.py` 등)를 재사용해 reaper의
  `TclBridge` 접속을 모킹한다 — 실제 xmsim 없이 검증 가능.
- 시간 의존 테스트(T-1~T-6)는 `time.time()`을 모킹하거나 `last_activity`를 과거 timestamp로 직접
  주입하는 방식으로 OS 무관하게 작성한다.

---

## 8. Implementation Guide

### 8.1 File Structure

```
src/xcelium_mcp/
├── registry.py                  # 수정: last_activity/ttl_miss_count 필드 + touch_activity() 헬퍼
├── tcl_bridge.py                # 수정: execute()/execute_safe() 진입부에 touch_activity() 훅
├── sim_session_reaper.py        # 신규: TTL 순회 + 안전 종료 (idle_culler.py와 동일 패턴)
└── tools/sim_lifecycle.py       # 수정: list_active_sessions() tool 추가
deploy/
└── crontab.example              # 수정: sim_session_reaper cron 항목 추가(30~60분 간격)
```

### 8.2 Implementation Order

1. [ ] `registry.py`: `last_activity`/`ttl_miss_count` 필드 정의 + `touch_activity(sim_dir)` 헬퍼
2. [ ] `tcl_bridge.py`: 활동시각 갱신 훅 삽입(스로틀링 포함) + 단위 테스트(T-1, T-2)
3. [ ] `sim_session_reaper.py`: TTL 판정 알고리즘 + `__SHUTDOWN__` 전송 + 단위 테스트(T-3~T-8, T-10)
4. [ ] `tools/sim_lifecycle.py`: `list_active_sessions()` tool + 단위 테스트(T-9)
5. [ ] `deploy/crontab.example`: reaper cron 항목 추가
6. [ ] 회귀 테스트 전체 실행(T-12)
7. [ ] cloud0 실배포 + 짧은 TTL로 실측 검증(T-11)

### 8.3 Session Guide

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|-------------|:---------------:|
| registry + tcl_bridge 훅 | `module-1` | last_activity 기록 인프라 | 15-20 |
| reaper 본체 | `module-2` | TTL 판정 + 안전 종료 | 20-25 |
| 가시성 tool + 배포 | `module-3` | list_active_sessions + crontab + 실배포 검증 | 15-20 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-07 | 초안 — Checkpoint 3: Option C(기존 F-C/F-D/F-B 패턴 재사용) 선택. 연속 2회 확인 후 종료(`MIN_MISS_COUNT_TO_KILL=2`)로 타이밍 경합 오탐 방지 안전장치 추가. | hoseung.lee |
