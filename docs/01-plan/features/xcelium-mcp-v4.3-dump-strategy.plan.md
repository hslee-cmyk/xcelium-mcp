# Plan: xcelium-mcp v4.3 — 3-Tier Dump Strategy (dump_depth + dump_window)

> **Feature**: 기존 sim_mode/extra_args 체계에 dump_depth, dump_window 파라미터 추가 + sdf_file fallback/override 지원
>
> **Date**: 2026-04-06
> **Status**: Draft
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Predecessor**: xcelium-mcp v4.2 (100% complete, code restructure)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | Gate/AMS 시뮬레이션에서 RTL과 동일한 포괄 dump를 사용하면 SHM이 10~100배 폭증. 현재 sim_batch_run은 `sim_mode`/`extra_args`로 Gate/AMS 실행은 가능하지만, dump 전략(probe scope, 시간 구간 제한)을 제어하는 파라미터가 없음 |
| **Solution** | 기존 `sim_mode`+`extra_args` 체계를 유지하면서 `dump_depth`(boundary/all)와 `dump_window`(시간 구간)를 추가. 기존 `probe_strategy` 필드를 확장하여 dump_depth를 반영. TB에 `$sdf_annotate` 없는 환경의 fallback + 명시 시 override 지원 |
| **Function UX Effect** | `sim_batch_run(sim_mode="gate", dump_depth="boundary")` 한 줄로 Gate 최적 dump 실행. AMS는 `dump_window` 추가로 관심 구간만 자동 dump. 기존 사용법은 변경 없음 |
| **Core Value** | Gate/AMS SHM을 RTL 대비 1/10~1/50로 줄여 디버깅 워크플로우를 Gate/AMS까지 확장. 기존 adaptive runner 체계와 완전 호환 |

---

## 1. 배경

### 1.1 현재 워크플로우의 한계

v4.2 `sim_batch_run`은 모든 시뮬레이션에 동일한 dump 전략을 적용한다:

```python
# 현재: sim_mode별 dump 전략 구분 없음
sim_batch_run(test_name="TOP015", dump_signals=["signal1", ...])
# → probe -create {signals} 또는 probe -create top -depth all
# → RTL에서는 문제없지만, Gate/AMS에서는 SHM 폭증
```

| 검증 유형 | 10ms sim SHM (full dump) | 10ms sim wall time | 문제 |
|----------|-------------------------|-------------------|------|
| RTL | ~10MB | ~20초 | 없음 |
| Gate | ~50~500MB | ~200~2000초 | SHM 폭증, 디스크 부족 |
| AMS | ~100MB~수GB | ~2000초~수 시간 | SHM 폭증 + 시뮬 속도 추가 저하 |

### 1.2 3-Tier Dump Strategy 개요

디버깅 워크플로우 문서(`xcelium-mcp-debugging-workflow.plan.md` Phase 1D)에서 정의한 전략:

| Tier | 검증 유형 | 전략 | SHM 비율 |
|------|----------|------|----------|
| Full | RTL | 포괄 집합 | 100% |
| Tier 1: Boundary | Gate, AMS (chip-level) | DUT I/O 경계만 (28개) | ~5~10% |
| Tier 2: Targeted | Gate (FAIL 후) | Tier 1 + 실패 블록 내부 | ~20~30% |
| Tier 3: Windowed | AMS (100ms+) | Tier 1 + probe_control on/off | ~2~5% |

**Block-level 예외**: block-level Gate/AMS는 신호 수가 적어 SHM 폭증 위험이 낮다.
이 경우 `dump_depth="all"` 옵션으로 RTL과 동일한 full dump를 허용한다.

| 검증 수준 | dump_depth 기본값 | 근거 |
|----------|-----------------|------|
| Chip-level (ext_d_top 이상) | `"boundary"` | 수만 개 신호, SHM 폭증 |
| Block-level (단일 모듈) | `"all"` | 수백~수천 개 신호, 관리 가능 |

### 1.3 기존 run_sim 스크립트의 Gate/AMS 지원 현황

`cloud0:~/git.clone/venezia-t0/design/top/sim/ncsim/run_sim` 분석 결과, 이미 Gate/AMS를 완전히 지원한다:

| 옵션 | 기능 | setup tcl |
|------|------|-----------|
| (없음) | RTL behavioral sim | `setup_rtl.tcl` |
| `-gate pre` | Pre-synthesis gate (zero delay, no timing check) | `setup_gate.tcl` |
| `-gate post` | Post-layout gate (SDF annotation, tfile 기반) | `setup_gate.tcl` |
| `-gate post -min` | Post gate, min delay | `setup_gate.tcl` |
| `-gate post -max` | Post gate, max delay | `setup_gate.tcl` |
| `-ams` | AMS — Spice + Verilog-A 모델 (block) | `setup_ams_rtl.tcl` / `setup_ams_gate.tcl` |
| `-amsf` | AMS — Spice + Verilog-A 모델 (full chip) | `setup_ams_rtl.tcl` / `setup_ams_gate.tcl` |
| `-amsv` | AMS-V — Verilog real-type 모델 (block) | — |
| `-amsvf` | AMS-V — Verilog real-type 모델 (full chip) | — |
| `-nosdf` | Gate without SDF | `setup_gate.tcl` |
| `-best`/`-worst`/`-typ` | Process corner | opcond 파일 선택 |

**결론**: sim_type 같은 별도 파라미터는 불필요하고, 기존 `sim_mode`로 Gate/AMS를 선택하고 `extra_args`로 추가 옵션(-max, -worst 등)을 전달하는 기존 체계를 그대로 활용한다. SDF override용 `sdf_file`/`sdf_corner`만 추가한다.

### 1.4 관련 문서

- 디버깅 워크플로우: `docs/01-plan/features/xcelium-mcp-debugging-workflow.plan.md` (Phase 1D)
- Boundary 신호 목록: `.ai/analysis/boundary_signals.analysis.md`
- 운용 가이드: `.ai/knowledge/mcp-operations-guide.md` (§2.6, §9)
- v4.2 report: `docs/04-report/features/xcelium-mcp-v4-sim-lifecycle.report.md`

---

## 2. Scope

### 2.1 In Scope

- [ ] `sim_batch_run`/`sim_start`에 `dump_depth` 파라미터 추가 (boundary/all)
- [ ] `sim_batch_run`에 `dump_window` 파라미터 추가 (start_ms/end_ms)
- [ ] `sim_batch_regression`에 dump_depth/dump_window 전파
- [ ] 기존 `probe_strategy` 확장: dump_depth 반영하여 probe setup tcl 자동 분기
- [ ] dump_window 시 probe_control on/off 자동 시퀀싱
- [ ] TB `$sdf_annotate` 없거나 SDF override가 필요한 환경을 위한 `sdf_file`/`sdf_corner` (Tier 1b/1c)
- [ ] `mode_defaults`에 Gate/AMS dump_depth 기본값 반영

### 2.2 Out of Scope

- Tier 2 블록별 추가 신호 자동 선택 (AI agent가 수동 구성 — 향후 v5에서 자동화)
- dump_window 다중 구간 (v4.3에서는 단일 구간만, 다중 구간은 Bridge mode 수동 제어)
- 기존 sim_mode/extra_args/resolve_sim_params 체계 변경 (확장만)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `dump_depth` 파라미터 추가 — "boundary" 또는 "all". 기존 `mode_defaults`의 `probe_strategy`와 연동. Gate/AMS 기본: "boundary", RTL 기본: "all". Block-level에서 "all" 명시 가능 | High | Pending |
| FR-02 | `dump_window` 파라미터로 probe_control on/off 자동 시퀀싱 | High | Pending |
| FR-03 | dump_depth에 따라 setup tcl에서 probe scope를 자동 조절 (기존 `_generate_setup_tcl` 확장) | High | Pending |
| FR-04 | SDF override — sim_discover가 TB의 `$sdf_annotate` + ifdef 가드 구조를 분석하여 registry에 기록. sdf_file 지정 시: (1) 기존 가드(NODLY 등)로 TB `$sdf_annotate` 비활성화 또는 가드 없으면 TB 자동 패치, (2) tfile 생성 + elab_args에 주입 | High | Pending |
| FR-05 | `sim_batch_regression`에 dump_depth/dump_window 전파 | Medium | Pending |
| FR-06 | dump_depth 결정 가이드 — AI agent가 RTL/TB 분석서(`.ai/analysis/`)의 모듈 계층·신호 수를 참조하여 dump_depth를 결정. mode_defaults는 sim_mode별 안전 기본값(gate/ams→"boundary")만 제공 | Medium | Pending |
| FR-07 | dump_window 구간 외 구간에서 probe disable 시 SHM 미증가 확인 | Medium | Pending |
| FR-08 | UserInputRequired 응답을 AI agent가 사용자에게 자연어로 전달 가능한 형식으로 개선 (현재 JSON → 자연어 프롬프트) | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement |
|----------|----------|-------------|
| 하위 호환 | 기존 sim_batch_run 호출 (dump_depth 미지정) 동작 변경 없음 | 기존 regression 21개 PASS |
| SHM 절감 | Gate Tier 1에서 RTL full 대비 SHM 90% 이상 절감 | SHM 크기 비교 |
| 성능 | dump_window probe_control 전환 오버헤드 < 1초 | wall time 측정 |

---

## 4. 설계 개요

### 4.1 기존 스키마 현황 (v4.2)

v4.2에서 이미 adaptive runner 체계가 존재한다:

```python
# 기존 sim_batch_run 시그니처 (v4.2)
async def sim_batch_run(
    test_name: str,
    dump_signals: list[str] = None,
    from_checkpoint: str = "",
    timeout: int = 600,
    sim_mode: str = "",         # "rtl" | "gate" | "ams_rtl" | "ams_gate"
    extra_args: str = "",       # 1회성 추가 옵션 (예: "-max -worst")
) -> str:
```

```python
# resolve_sim_params() — config extra_args + 1회성 extra_args 합침
mode_defaults = {
    "common":   {"timeout": 120, "probe_strategy": "all",       "extra_args": ""},
    "gate":     {"timeout": 1800, "probe_strategy": "selective"},
    "ams_rtl":  {"timeout": 3600, "probe_strategy": "selective"},
    "ams_gate": {"timeout": 3600, "probe_strategy": "selective"},
}
```

**이미 되는 것**: sim_mode로 Gate/AMS 실행, extra_args로 corner/SDF 옵션 추가, timeout 자동 조절
**안 되는 것**: dump scope 제어 (boundary vs all), 시간 구간 dump, 스크립트 없는 환경 SDF

### 4.2 v4.3 신규 파라미터

```python
# v4.3 sim_batch_run 시그니처 (변경분만)
async def sim_batch_run(
    test_name: str,
    dump_signals: list[str] = None,
    from_checkpoint: str = "",
    timeout: int = 600,
    sim_mode: str = "",              # 기존 유지
    extra_args: str = "",            # 기존 유지
    # === v4.3 신규 ===
    dump_depth: str = None,          # "boundary" | "all" — None이면 sim_mode별 기본값
    dump_window: dict = None,        # {"start_ms": 50, "end_ms": 55}
    sdf_file: str = "",              # SDF override/fallback: SDF 파일 경로 (지정 시 항상 override)
    sdf_corner: str = "max",         # SDF delay 선택: "min" | "max" | "typ"
) -> str:
```

### 4.3 probe 신호 결정 로직

dump_depth와 dump_signals는 독립이 아니라 **합집합**으로 동작한다.

```python
def _resolve_probe_signals(self, dump_signals, dump_depth, sim_mode, mode_defaults):
    # 1. dump_depth 결정 (명시 > mode_defaults)
    effective_depth = dump_depth
    if effective_depth is None:
        mode_cfg = mode_defaults.get(sim_mode, {})
        effective_depth = mode_cfg.get("dump_depth", "all")
    
    # 2. "all"이면 전체 dump — dump_signals 무관
    if effective_depth == "all":
        return ("depth_all", None)           # probe -create top -depth all
    
    # 3. "boundary" 기반 신호 집합
    base_signals = set(BOUNDARY_SIGNALS)     # 28개
    
    # 4. dump_signals가 있으면 합집합 (중복 제거)
    if dump_signals:
        base_signals |= set(dump_signals)
    
    return ("signals", sorted(base_signals))
```

**동작 매트릭스:**

| dump_depth | dump_signals | 결과 |
|-----------|-------------|------|
| `"all"` | 무관 | `probe -create top -depth all` |
| `"boundary"` | 미지정 | BOUNDARY_SIGNALS (28개) |
| `"boundary"` | `["r_loopState", ...]` | BOUNDARY_SIGNALS ∪ dump_signals (중복 제거) |
| 미지정 (RTL) | `["sig1", ...]` | dump_signals만 (기존 동작 유지) |
| 미지정 (Gate) | `["r_loopState", ...]` | BOUNDARY_SIGNALS ∪ dump_signals |
| 미지정 (RTL) | 미지정 | `probe -create top -depth all` (기존 동작) |
| 미지정 (Gate) | 미지정 | BOUNDARY_SIGNALS (28개) |

**mode_defaults 확장** (sim_discover가 자동 설정):

```python
mode_defaults = {
    "common":   {"timeout": 120, "probe_strategy": "all",       "dump_depth": "all"},
    "gate":     {"timeout": 1800, "probe_strategy": "selective", "dump_depth": "boundary"},
    "ams_rtl":  {"timeout": 3600, "probe_strategy": "selective", "dump_depth": "boundary"},
    "ams_gate": {"timeout": 3600, "probe_strategy": "selective", "dump_depth": "boundary"},
}
```

### 4.4 사용 예시 — 기존 체계 활용

```python
# RTL (기존과 완전 동일 — dump_depth 미지정 → "all")
sim_batch_run(test_name="TOP015")

# Chip-level Gate post, max delay, worst corner — boundary dump (기본)
sim_batch_run(test_name="TOP015", sim_mode="gate",
              extra_args="-max -worst")

# Block-level Gate — full dump (신호 수 적어 SHM 관리 가능)
sim_batch_run(test_name="TOP015", sim_mode="gate",
              extra_args="-max -worst", dump_depth="all")

# AMS+Gate full chip, worst corner — boundary dump + 시간 윈도우
sim_batch_run(test_name="TOP015", sim_mode="ams_gate",
              extra_args="-worst",
              dump_window={"start_ms": 50, "end_ms": 55})

# Block-level AMS — full dump + 시간 윈도우
sim_batch_run(test_name="TOP015", sim_mode="ams_rtl",
              dump_depth="all",
              dump_window={"start_ms": 50, "end_ms": 55})

# TB에 $sdf_annotate 없는 환경 — sdf_file + corner 직접 지정
sim_batch_run(test_name="TOP015", sim_mode="gate",
              sdf_file="/path/to/top.sdf", sdf_corner="max",
              dump_depth="boundary")
```

### 4.5 Adaptive Runner: 2-Tier 전략

#### sim_discover — TB $sdf_annotate 분석

TB RTL에서 `$sdf_annotate`와 주변 ifdef 가드 구조를 파싱하여 registry에 기록한다.

```
분석 항목:
  1. $sdf_annotate 존재 여부
  2. SDF 비활성화 가드 define — ifdef 스택 추적으로 자동 탐지 (하드코딩 없음)
  3. scope별 SDF 엔트리 — 각 $sdf_annotate의 scope, 적용 조건(ifdef), SDF 파일 경로를 구조화

registry 기록 예시 (venezia-t0 기준):
  has_sdf_annotate: true
  sdf_guard_define: "NODLY"                    # $sdf_annotate 비활성화 가드
  sdf_entries: [
    {scope: "`DTOP",      conditions: {PRE: true},                file: "d_top.sdf"},
    {scope: "`VTG_TOP",   conditions: {PRE: true},                file: "ci_vtg_top.sdf"},
    {scope: "`ALAMO_TOP", conditions: {BEST: true, AMS: true},    file: "chiptop.bst.sdf.gz"},
    {scope: "`DTOP",      conditions: {BEST: true, AMS: false},   file: "d_top.bst.sdf.gz"},
    {scope: "`VTG_TOP",   conditions: {BEST: true},               file: "ci_vtg_top.bst.sdf.gz"},
    {scope: "`ALAMO_TOP", conditions: {WORST: true, AMS: true},   file: "chiptop.wst.sdf.gz"},
    {scope: "`DTOP",      conditions: {WORST: true, AMS: false},  file: "d_top.wst.sdf.gz"},
    {scope: "`VTG_TOP",   conditions: {WORST: true},              file: "ci_vtg_top.wst.sdf.gz"},
    {scope: "`DTOP",      conditions: {},                         file: "d_top.typ.sdf.gz"},
    {scope: "`VTG_TOP",   conditions: {},                         file: "ci_vtg_top.typ.sdf.gz"},
  ]
  # conditions가 비어있으면 default (TYP)
  # corner defines, AMS 분기는 sdf_entries의 conditions에서 도출 가능
```

#### Tier 1/2 전략

```
[Tier 1: 스크립트 기반] — run_sim 스크립트 있음

  1a. sdf_file 미지정 + has_sdf_annotate: true
      → TB + 스크립트가 SDF 처리, corner는 extra_args로 전달 ("-max -worst")
      → 추가 조치 불필요

  1b. sdf_file 미지정 + has_sdf_annotate: false
      → tfile 자동 생성 (기존 SDF 경로 없으므로 sdf_file 필요)
      → 사실상 1c와 동일 경로

  1c. sdf_file 지정 — SDF override
      → Step 1: TB의 기존 $sdf_annotate 비활성화
         sdf_guard_define 있음 (예: NODLY)
           → extra_args에 -define NODLY 추가 (TB RTL 수정 불필요)
         sdf_guard_define 없음
           → TB RTL에 `ifndef MCP_SDF_OVERRIDE` 가드 자동 삽입 (패치)
           → extra_args에 -define MCP_SDF_OVERRIDE 추가
      → Step 2: 사용자 지정 SDF 적용
         tfile 자동 생성 (/tmp/mcp_sdf_tfile):
           COMPILED_SDF_FILE "{sdf_file}" SCOPE top {MAXIMUM|MINIMUM|TYPICAL} ;
         스크립트 패치: elab_args에 추가
           -delay_mode path -sdf_verbose -timescale 1ns/1fs -tfile /tmp/mcp_sdf_tfile

[Tier 2: 스크립트 없음] — UserInputRequired

  → 기존 패턴: 사용자에게 run command 질의 (_ask_user_runner)
  → 현재 이슈: JSON 형식 응답으로 사용자 입력 불가 → FR-08에서 개선

[공통] — xcelium-mcp가 항상 담당

  dump_depth에 따른 probe setup tcl 생성/주입 (MCP_INPUT_TCL)
  dump_window에 따른 probe_control on/off 시퀀싱
```

**전략 결정 로직:**

```python
def _resolve_runner_strategy(self, runner, sdf_file, sdf_corner, registry):
    if not runner.get("script"):
        return "user_input"              # Tier 2: 스크립트 없음 → 사용자 질의
    if sdf_file:
        return "script_override_sdf"     # Tier 1c: sdf_file override
    has_sdf = registry.get("has_sdf_annotate", False)
    if has_sdf:
        return "script"                  # Tier 1a: TB가 SDF 처리
    else:
        return "script_override_sdf"     # Tier 1b: $sdf_annotate 없음 → tfile 필요
```

### 4.6 probe setup 분기

**우선순위**: `_resolve_probe_signals()` (§4.3) 결과에 따라 분기.

```
결과 ("depth_all", None):
  → probe -create top -depth all -shm

결과 ("signals", [sig1, sig2, ...]):
  → probe -create {각 signal} -shm
  → boundary + dump_signals 합집합, 중복 제거 완료 상태
```

### 4.7 dump_window 내부 시퀀싱

dump_window={"start_ms": 50, "end_ms": 55} 일 때:

**[Batch mode] — setup tcl에 probe 스케줄 주입 (bridge turnaround 0)**

`_generate_setup_tcl()`이 dump_window를 받아 tcl에 run/probe 시퀀스를 포함한다.
bridge 통신 불필요, 시뮬레이터가 자체 실행.

```tcl
# _generate_setup_tcl() 출력 예시
database -open dump.shm -shm
probe -create top.hw.i_mainClk -shm
# ... boundary signals
probe -disable
run 50ms
probe -enable
run 5ms
probe -disable
run
```

**[Bridge mode] — probe on/off 최소화 (bridge turnaround 2)**

setup tcl에서 `probe -disable`로 시작, bridge 호출은 probe 전환 2회만.

```python
async def _run_with_dump_window(self, dump_window, ...):
    start_ms = dump_window["start_ms"]
    end_ms = dump_window["end_ms"]

    # settling — probe 이미 off (setup tcl에서 disable)
    await self._run_sim(f"{start_ms}ms")

    # 관심 구간 — probe on
    await self._bridge_cmd("probe -enable")
    await self._run_sim(f"{end_ms - start_ms}ms")

    # 나머지 — probe off
    await self._bridge_cmd("probe -disable")
    await self._run_sim("run")              # $finish까지 실행
```

### 4.8 수정 대상 파일

| 파일 | 모듈 | 변경 |
|------|------|------|
| `xcelium_mcp/batch_runner.py` | resolve_sim_params | dump_depth를 params에 포함, mode_defaults에서 기본값 반영 |
| `xcelium_mcp/batch_runner.py` | _run_batch_single | dump_window 지정 시 probe_control 시퀀싱 |
| `xcelium_mcp/batch_runner.py` | _generate_setup_tcl | dump_depth별 probe scope 분기 |
| `xcelium_mcp/batch_runner.py` | _run_batch_regression | dump_depth/dump_window 전파 |
| `xcelium_mcp/sim_runner.py` | sim_discover | mode_defaults에 dump_depth 기본값 + TB $sdf_annotate 가드 구조 분석 → has_sdf_annotate, sdf_guard_define 등 기록 |
| `xcelium_mcp/sim_runner.py` | `_analyze_sdf_annotate()` | **신규** — TB RTL에서 $sdf_annotate + ifdef 가드 파싱 |
| `xcelium_mcp/sim_runner.py` | `_patch_sdf_override()` | **신규** — (1) 기존 가드로 비활성화 또는 TB 패치, (2) tfile 생성, (3) elab_args 주입 |
| `xcelium_mcp/sim_runner.py` | _start_bridge | script_override_sdf 전략 시 _patch_sdf_override 호출 + 스크립트 실행 |
| `xcelium_mcp/tools/batch.py` | MCP tool 등록 | dump_depth/dump_window/sdf_file/sdf_corner 파라미터 추가 |
| `tests/test_sim_batch.py` | 테스트 | dump_depth/dump_window/sdf_file/sdf_corner 단위 테스트 |

### 4.9 setup tcl probe 생성 예시

```tcl
# dump_depth="all" (RTL 기본, block-level Gate/AMS)
probe -create top -depth all -shm

# dump_depth="boundary" (chip-level Gate/AMS)
probe -create top.hw.i_mainClk -shm
probe -create top.hw.i_rst_n -shm
probe -create top.hw.i_scl -shm
probe -create top.hw.io_sda -shm
# ... (boundary_signals 목록 — boundary_signals.analysis.md 참조)

# dump_window 지정 시 — probe 초기 off
probe -disable   # dump_window.start_ms까지 dump 없음
```

---

## 5. Success Criteria

### 5.1 Definition of Done

- [ ] dump_depth 미지정 시 기존 동작 완전 동일 (하위 호환)
- [ ] sim_mode="gate" + dump_depth="boundary"로 Gate sim 실행, SHM 절감 확인
- [ ] dump_window 지정 시 관심 구간만 SHM에 기록 확인
- [ ] sdf_file 지정 시 Gate sim 실행 성공 ($sdf_annotate 유무 무관 — sdf_file 명시 시 항상 override)
- [ ] sim_batch_regression에 dump_depth/dump_window 전파 동작
- [ ] 기존 regression 21개 테스트 PASS 유지

### 5.2 검증 방법

| 검증 항목 | 방법 | 기대 결과 |
|----------|------|----------|
| 하위 호환 | 기존 21개 regression | 전수 PASS |
| Gate Tier 1 SHM 절감 | TOP015 RTL full vs Gate boundary SHM 비교 | 90%+ 절감 |
| dump_window 정확성 | AMS sim 후 CSV 추출, window 외 구간 데이터 없음 확인 | window 외 시간 데이터 0 |
| sdf_file override | sdf_file 지정 Gate sim ($sdf_annotate 있는 환경에서 기존 가드로 비활성화 + tfile override) | PASS |
| sdf_file fallback | $sdf_annotate 없는 환경에서 sdf_file + sdf_corner 지정 Gate sim | PASS |

---

## 6. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| probe -disable 후 -enable 시 신호 누락 | High | Low | Xcelium 문서 확인, 단위 테스트로 검증 |
| Gate sim에서 boundary 신호 이름이 RTL과 다름 (넷리스트 변환) | High | Medium | Gate 넷리스트에서 실제 신호명 확인, 매핑 테이블 필요 시 추가 |
| sdf_file fallback에서 elab 옵션이 프로젝트마다 다름 | Medium | High | 기본 옵션 제공 + mcp_sim_config에서 override 가능하도록 설계 |
| dump_window 다중 구간 요구 | Low | Medium | v4.3은 단일 구간만. 다중 구간은 Bridge mode 수동 제어로 안내 |
| sdf_file override 시 기존 TB $sdf_annotate와 중복 실행 | High | Low | 기존 가드(NODLY 등) 우선 활용, 가드 없으면 TB에 `ifndef MCP_SDF_OVERRIDE` 자동 삽입 |
| TB RTL 자동 패치(`ifndef` 삽입) 시 구문 오류 | Medium | Low | 패치 전 백업, 패치 후 컴파일 확인 |

---

## 7. 구현 순서

```
Phase 1: dump_depth 파라미터 + probe setup 분기        (1.5시간)
  ├─ FR-01: resolve_sim_params에 dump_depth 추가
  ├─ FR-03: _generate_setup_tcl() dump_depth별 probe 분기
  ├─ FR-06: mode_defaults에 dump_depth 기본값
  └─ FR-05: sim_batch_regression 전파

Phase 2: dump_window 자동 시퀀싱                      (1.5시간)
  ├─ FR-02: _run_with_dump_window() 구현
  └─ FR-07: probe disable 구간 SHM 미증가 검증

Phase 3: SDF override (Tier 1b/1c)                    (1시간)
  └─ FR-04: $sdf_annotate 가드 분석 + tfile 생성 + override/fallback

Phase 4: MCP 스키마 + 테스트 + 검증                    (1시간)
  ├─ tools/batch.py 스키마에 신규 파라미터 추가
  ├─ 단위 테스트 작성
  └─ regression 21개 PASS 확인
```

**예상 총 소요**: ~5시간

---

## 8. Next Steps

1. [ ] Design 문서 작성 (`xcelium-mcp-v4.3-dump-strategy.design.md`)
2. [ ] xcelium-mcp 프로젝트에서 sim_batch.py 현재 구조 확인
3. [ ] Gate 넷리스트 신호명 매핑 필요 여부 확인 (cloud0에서 Gate sim 환경 확인)
4. [ ] 구현 시작

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-06 | 초안 — 3-Tier Dump Strategy |  hoseung.lee |
| 0.2 | 2026-04-06 | 기존 sim_mode/extra_args 스키마 활용으로 재설계. run_sim_args 삭제, sim_type 삭제. dump_depth + dump_window + sdf_file fallback. AMS 옵션 정정 (-amsv=real-type, -ams=spice+verilog-a) | hoseung.lee |
