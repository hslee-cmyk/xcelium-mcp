# Phase 1 — 사전 분석 (로컬, 시뮬레이터 불필요)

## 목적

시뮬레이션을 실행하기 전에 캐시된 분석·RTL 분석서·dump scope를 확인해 판별 신호와 실행 전략을 미리 정한다.

## 절차

### 1A. 캐시 참조 + 테스트케이스 분석

1. `.ai/analysis/tb_{env}_{test}.analysis.md` 존재 확인(env = lgc/uvm/dsv/ams)
2. 있으면 → 캐시에서 판별 신호/기대값/시퀀스 참조 → Phase 2로
3. 없으면 → 테스트케이스 파일 읽고 phase-0-discovery.md 형식으로 분석서 작성 후 캐시

### 1B. RTL 분석서 참조

`.ai/analysis/{module}.analysis.md`에서 FSM 전이 테이블, 신호 의존성 맵, 크로스 FSM 신호 타이밍을 확인한다.

**분석서 부재/stale 시 — agent 위임**: `verilog-rtl-debugger` agent(chip-design-skills)가 `verilog-rtl-analyst` agent를 호출해 분석서를 먼저 작성/갱신한다. 분석서 없이 Phase 4의 FSM 전이 대조로 바로 넘어가지 않는다.
> **Fallback**: `verilog-rtl-debugger` agent를 찾을 수 없으면(chip-design-skills에 아직 미배포), Claude가 직접 `.ai/analysis/{module}.analysis.md`를 작성한다 — verilog-rtl skill의 Module Analysis 방법론(FSM 상태/전이 테이블, 신호 의존성, CDC 경로, timing 관계, reset provenance)을 따른다.

### 1C. Dump Scope 확인

```python
# v4.3 이후 — dump_depth 파라미터로 제어
sim_batch_run(test_name="TOP015", dump_depth="all")       # 전체 scope
sim_batch_run(test_name="TOP015", dump_depth="boundary")   # 경계 신호만
sim_batch_run(test_name="TOP015", dump_depth="boundary",
              dump_signals=["top.hw...r_regAddr"])          # 경계 + 추가 신호
```

### 1D. Dump 신호 집합 구성 — 3-Tier + v5.2 Block-level

| Tier | sim_mode | 전략 | dump 대상 |
|------|----------|------|-----------|
| Full | `rtl` | 포괄 집합 | 내부 FSM + 레지스터 + 경계 |
| Tier 1: Boundary | `gate`/`ams_rtl`/`ams_gate` | DUT I/O 경계만 | top I/O + 프로토콜 핸드셰이크 + 클럭/리셋 |
| Tier 2: Targeted | `gate`(FAIL 후) | Tier 1 + 실패 블록 내부 수동 신호 나열 | 실패 인터페이스 내부 |
| **v5.2 dump_scopes** | `gate`(FAIL 후) | **블록 단위 자동/수동 조합** — Tier 2를 대체·일반화 | `dump_scopes={block: "all"/"boundary"/"skip"}` |
| Tier 3: Windowed | `ams_rtl`/`ams_gate`(100ms+) | 시간 구간 제한 | Tier 1 + `dump_window_*_ms` |

**v5.2 신규 (2026-07-02 완료, 94% match rate)**: `dump_depth="boundary"`(top I/O만)와 `"all"`(전체) 사이의 블록 단위 중간 해상도.

```python
# 특정 블록만 전체 dump, 나머지는 boundary
sim_batch_run(test_name="TOP015", dump_depth="boundary",
              dump_scopes={"top.hw.u_ext.u_i2cSlave": "all"}, sim_mode="gate")

# 이전 dump_scopes 재사용
sim_batch_run(test_name="TOP015", use_dump_history=True)

# 자동 감지 — Flow A(Bridge, 런타임)
sim_bridge_run(test_name="TOP015", auto_boundaries=True)
# 자동 감지 — Flow B(Batch, lazy, Yosys JSON)
sim_discover(boundary_depth=3)
```

세밀한 개별 신호 제어가 필요하면 Tier 2(수동 신호 리스트)를, 블록 단위로 충분하면 `dump_scopes`를 우선 검토한다.

### AI agent의 dump_depth 결정 가이드

1. `.ai/analysis/{module}.analysis.md` 읽기 → 모듈 계층/신호 수 파악
2. `.ai/analysis/tb_{env}_{test}.analysis.md` 읽기 → TB가 참조하는 DUT 신호 수 파악
3. block-level(단일 모듈, 신호 수 적음) → `dump_depth="all"` 명시
4. chip-level(신호 수 많음) → `dump_depth="boundary"`(기본값)
5. 판단 불확실 시 → mode_defaults 기본값 그대로 사용

## Tool 예시

```python
sim_batch_run(test_name="TOP015", dump_depth="boundary",
              dump_scopes={"top.hw.u_ext.u_i2cSlave": "all"}, sim_mode="gate")
inspect_signal(action="check_dump", signals=["top.hw...r_regAddr"], shm_path="dump/ci_top_TOP015.shm")
```

## verilog-rtl-debugger agent 위임

| 상황 | 위임 대상 |
|------|----------|
| `.ai/analysis/{module}.analysis.md` 부재/stale | `verilog-rtl-analyst` |

## 다음 단계

Phase 2(시뮬레이션 실행)로 진행.
