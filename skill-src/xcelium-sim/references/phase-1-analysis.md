# Phase 1 — 사전 분석 (로컬, 시뮬레이터 불필요)

## 목적

시뮬레이션을 실행하기 전에 캐시된 분석·RTL 분석서·dump scope를 확인해 판별 신호와 실행 전략을 미리 정한다.

## 절차

### 1A. 캐시 참조 + 테스트케이스 분석

1. `.ai/analysis/tb_{env}_{test}.analysis.md` 존재 확인(env = lgc/uvm/dsv/ams)
2. 있으면 → 캐시에서 판별 신호/기대값/시퀀스 참조 → Phase 2로
3. 없으면 → 테스트케이스 파일 읽고 phase-0-discovery.md 형식으로 분석서 작성 후 캐시

3번(분석서 부재)의 기본 수행자는 Claude 자신이다 — phase-0-discovery.md §0A/0B 절차를 그대로 따라 직접 작성·캐시한다. 분석서 없이 Phase 4의 판별로 바로 넘어가지 않는다 — 1B(RTL 분석서 부재 시)와 동일 원칙.
> **선택적 위임(있으면)**: 로컬에 `verilog-tb-analyst` agent(chip-design-skills가 install.py로 배포 — 신설 예정)가 설치돼 있으면 Task로 위임할 수 있다 — 필수 경로는 아니다.

### 1B. RTL 분석서 참조

`.ai/analysis/{module}.analysis.md`에서 FSM 전이 테이블, 신호 의존성 맵, 크로스 FSM 신호 타이밍을 확인한다.

**분석서 부재/stale 시 기본 수행자는 Claude 자신이다**: `~/.claude/skills/verilog-rtl/SKILL.md` §12(Module Analysis, 필수 포함 항목/작성 규칙/갱신 규칙)를 **직접 Read**해 그 형식(FSM 상태/전이 테이블, 신호 의존성, CDC 경로, timing 관계, reset provenance)대로 `.ai/analysis/{module}.analysis.md`를 작성한다 — `verilog-rtl` skill이 세션에 자동 트리거되길 기다리지 말고 이 경로를 능동적으로 Read할 것. 분석서 없이 Phase 4의 FSM 전이 대조로 바로 넘어가지 않는다.
>
> **DUT-TB 인터페이스가 걸린 항목(신호 의존성 맵의 "상위 호출 패턴"/CDC 경계가 TB 쪽 클럭·구동원과 맞물리는 경우)은 `verilog-rtl` skill만으로 정확히 해석되지 않는다** — 이때는 아래 2개도 **같은 방식으로 직접 Read**한다(전체 skill이 아니라 필요 절만, `verilog-tb-analyst.plan.md` FR-11/FR-12와 동일한 근거·동일한 절 한정):
> - `~/.claude/skills/chip-verification/SKILL.md` §듀얼탑 아키텍처 + `references/interface-mapping.md` — DUT 포트가 hdl_top의 Interface/Virtual Interface로 어떻게 매핑되는지 확인할 때
> - `~/.claude/skills/uvm-verification/SKILL.md` §UVM 계층 구조 + `references/component-templates.md`/`sequence-patterns.md` — env prefix가 `uvm`인 테스트에서 agent/driver/monitor가 어떤 신호를 구동/샘플링하는지 확인할 때
>
> 두 skill 모두 RTL 합성·CDC 설계 규칙이나 AMS 아날로그 모델 절은 이 목적과 무관하므로 로드하지 않는다 — 필요한 구조 절만 좁게 Read.
> **선택적 위임(있으면)**: 로컬에 `verilog-rtl-debugger`/`verilog-rtl-analyst` agent(둘 다 chip-design-skills가 install.py로 배포 — chip-design-skills 자체가 호출하는 게 아님)가 설치돼 있으면, `verilog-rtl-debugger`가 `verilog-rtl-analyst`를 Task로 호출해 분석서를 작성/갱신하도록 위임할 수 있다 — 필수 경로는 아니다.

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

## Agent 위임

| 상황 | 위임 대상 |
|------|----------|
| `.ai/analysis/tb_{env}_*.analysis.md`(TB 공유 컴포넌트/테스트케이스) 부재/stale | `verilog-tb-analyst` |
| `.ai/analysis/{module}.analysis.md`(RTL) 부재/stale | `verilog-rtl-analyst` |

## 다음 단계

Phase 2(시뮬레이션 실행)로 진행.
