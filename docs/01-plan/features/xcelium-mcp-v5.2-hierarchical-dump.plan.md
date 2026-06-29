# Plan: xcelium-mcp v5.2 — Hierarchical Dump Strategy

> **Feature**: `dump_depth="boundary"`의 의미를 "top I/O 경계"에서 "모든 block의 경계 합집합"으로 확장하고, `dump_scopes` 파라미터로 특정 block만 full dump / skip을 override할 수 있게 한다.
>
> **Date**: 2026-04-09
> **Status**: Draft
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Predecessor**: xcelium-mcp v5.0 (code review), v4.3 (dump_depth/dump_window 도입)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | `dump_depth="boundary"`는 top I/O 28개만 dump하므로 내부 block 인터페이스가 전혀 보이지 않고, `dump_depth="all"`은 수만 신호를 dump해 Gate/AMS에서 SHM이 폭증한다. 중간 단계(블록별 경계)가 없어 디버깅 시 "너무 적음 vs 너무 많음"의 간극이 크다. |
| **Solution** | (1) config에 `dump_strategy.block_boundaries` 맵을 추가해 각 block의 경계 신호를 정의. (2) `dump_depth="boundary"`는 `default_block_policy`/`dump_scopes` 기반으로 top 경계 + 선택적 block 경계 포함 (opt-in/opt-out 두 모델). (3) 신규 `dump_scopes` 파라미터로 block별 `all`/`boundary`/`skip` override 지원. (4) 자동 감지 두 Flow: **Flow A** (`sim_bridge_run(auto_boundaries=True)`): bridge 기동 직후 TCL 탐색으로 자동 생성. **Flow B** (`sim_batch_run` lazy discovery): `block_boundaries` 비어있을 때 `netlist_info.{mode}.boundary_json` 런타임 파싱. `sim_discover`는 경로 유효성 검사 + Yosys 명령 안내만 담당 (Phase 2). |
| **Function UX Effect** | `default_block_policy="skip"` (기본, opt-in): `dump_scopes`에 명시한 block만 추가. `{"*": "boundary"}`로 전체 block 경계 opt-in (~400개). `default_block_policy="boundary"` (opt-out): 발견된 모든 block 경계 자동 포함 후 개별 skip 가능. `dump_scopes={"...u_ext_i2cSlave": "all"}` 추가 시 해당 block만 full dump. 기존 `"all"`/`"boundary"` 사용법 backward compat 유지. |
| **Core Value** | Gate/AMS 디버깅 workflow의 해상도를 "chip 경계"에서 "블록 경계"로 상향. 실패 인터페이스 특정 속도 향상 + 불필요한 전체 재실행 감소. SHM은 full 대비 5~10%로 유지. |

---

## 1. 배경

### 1.1 현재 `dump_depth` 한계

v4.3에서 `dump_depth` 파라미터가 추가되었으나, 현재 의미는 다음과 같다:

```python
# v4.3~v5.1 현재 의미
dump_depth="all"       # probe -create top -depth all (수만 신호)
dump_depth="boundary"  # BOUNDARY_SIGNALS 28개 (top I/O only)
```

**문제점:**

| 시나리오 | `"all"` | `"boundary"` |
|----------|:------:|:-----------:|
| RTL 디버깅 | 적절 | — |
| Gate chip-level 초회 실행 | ❌ SHM 폭증 | ✅ 실용적이지만 해상도 낮음 |
| Gate FAIL → 실패 블록 특정 | ❌ 재실행 너무 느림 | ❌ 내부 블록 경계 미가시 |
| Gate 특정 블록 상세 분석 | ❌ 불필요한 신호 과다 | ❌ 해당 블록 내부 전혀 없음 |

### 1.2 실전 문제 사례 (TOP015 Gate 가상 시나리오)

```
1. sim_batch_run(sim_mode="gate", dump_depth="boundary")
   → top.hw.i_scl, io_sda, o_askData 등 28개만 dump
2. FAIL 발생 (o_askData 이상)
3. → 어느 block에서 실패? u_ext_i2cSlave? u_ext_askEncoder?
   → 경계 신호만으로는 특정 불가
4. dump_depth="all" 재실행
   → 50,000+ 신호, SHM 500MB+, wall time 10배 증가
```

**이상적 워크플로우:**

```
1. dump_depth="boundary", dump_scopes={"*": "boundary"}
   또는 config에 default_block_policy="boundary" 설정
   (→ top I/O + 모든 블록 경계 포함 ~400개)
   → i2cSlave.o_config_dur, askEncoder.o_askOut 등이 보임
2. FAIL 발생 → 경계 신호 CSV로 askEncoder 실패 특정
3. dump_scopes={"...u_ext_askEncoder": "all"} 재실행
   → 전체 400개 + askEncoder 내부 ~500개 = ~900개
   → SHM 50MB (전체 대비 1%), 디버깅 충분
```

### 1.3 v4.3 Plan에서 남긴 숙제

`xcelium-mcp-v4.3-dump-strategy.plan.md` §1.2에 이미 언급된 구분:

> **Block-level 예외**: block-level Gate/AMS는 신호 수가 적어 SHM 폭증 위험이 낮다.
> 이 경우 `dump_depth="all"` 옵션으로 RTL과 동일한 full dump를 허용한다.

이 문장은 "block 단위로 다른 dump 전략을 쓰고 싶다"는 요구를 암시했지만, v4.3은 chip-level vs block-level 이분법만 지원했다. v5.2는 이를 **chip 내부에서도 block별로 dump 전략을 조합**할 수 있도록 확장한다.

---

## 2. 요구사항

### 2.1 Functional Requirements

| ID | 요구사항 | 우선순위 |
|----|---------|:-------:|
| **FR-01** | `dump_depth="boundary"`가 `default_block_policy`/`dump_scopes` 기반으로 top I/O + 선택적 block 경계 dump (opt-in: 명시한 block만 / opt-out: 전체 후 skip) | P0 |
| **FR-02** | `dump_scopes={scope: "all"}` — 해당 scope를 `probe -depth all`로 dump | P0 |
| **FR-03** | `dump_scopes={scope: "boundary"}` — 해당 scope만 경계 dump (다른 scope 영향 없음) | P1 |
| **FR-04** | `dump_scopes={scope: "skip"}` — 해당 scope 경계 제외 | P0 |
| **FR-05** | `.mcp_sim_config.json`에 `dump_strategy.block_boundaries` 맵 스키마 추가 | P0 |
| **FR-06** | `.mcp_sim_config.json`에 `dump_strategy.top_boundary` 추가 (하드코딩 BOUNDARY_SIGNALS 이동) | P0 |
| **FR-07** | Block boundary 자동 감지 — **두 가지 대칭 Flow**. **Flow A** (`sim_bridge_run(auto_boundaries=True)`): bridge 기동 직후 TCL `scope -describe -sort kind`로 hierarchy 탐색. probe/SHM 없이 동작, `sim_run` 불필요. **Flow B** (`sim_batch_run` lazy JSON discovery): `block_boundaries`가 비어있고 `default_block_policy` 설정 시, `netlist_info.{mode}.boundary_json`을 읽어 boundary를 런타임에 동적 구성. `write_discovered_boundaries=true`면 결과를 config에 캐시. `sim_discover`는 JSON 경로 등록에만 관여 (boundary 파싱 불필요). | P1 |
| **FR-07a** | `dump_strategy.{mode}.default_block_policy` — `"skip"` (기본, 미등록 block 제외) / `"boundary"` (발견된 모든 block 자동 boundary dump). `block_boundaries`가 비어있어도 동작 가능 | P1 |
| **FR-07b** | `dump_strategy.{mode}.block_filter` — glob 패턴 리스트로 auto-discovery 대상 subtree 제한. 예: `["top.hw.u_ext.*"]` → u_ext 산하만 boundary 대상 | P1 |
| **FR-07c** | `dump_strategy.{mode}.write_discovered_boundaries` — `true`이면 lazy discovery 결과를 `block_boundaries`에 자동 저장 (캐시). `false`이면 매 실행 동적 파싱 | P1 |
| **FR-08** | `dump_scopes` key에 glob 패턴 지원 — `fnmatch` 사용, `*`가 `.` 포함 매칭. 예: `"top.hw.u_ext.u_ext_d_main.*": "skip"` (subtree 전체 제외), `"*": "skip"` (block 전체 제외 = top_boundary만) | P1 |
| **FR-09** | Pre-defined scope groups (`"group:all_i2c": "all"`) | P2 |
| **FR-10** | 기존 `dump_depth="all"`/`"boundary"` 단독 사용 backward compat | P0 |

### 2.2 Non-Functional Requirements

| ID | 요구사항 |
|----|---------|
| **NFR-01** | 기존 v5.1 regression 테스트 21/21 PASS 유지 |
| **NFR-02** | `dump_scopes` 미지정 시 기존 동작과 완전 동일 |
| **NFR-03** | `block_boundaries` 미정의 시 `"boundary"`는 top I/O만 사용 (v5.1 호환) |
| **NFR-04** | SHM 크기: `boundary` (확장) 모드에서 `all` 대비 ≤ 10% |
| **NFR-05** | Tcl 생성 overhead: block 수 × 신호 수 선형, 10ms 이내 |
| **NFR-06** | 새 파라미터/config는 v5.1 tool 시그니처에 optional로 추가 (v5.1 호출 코드 변경 불요) |

---

## 3. 제안 설계

### 3.1 Config 스키마 확장

**파일**: `.mcp_sim_config.json`

**설계 원칙**: 기존 config의 `runner.mode_defaults`, `runner.setup_tcls`가 이미 mode-keyed 구조를 사용한다. `dump_strategy`와 `netlist_info`도 동일 패턴으로 설계하여 일관성을 유지한다.

```json
{
  "version": 2,
  "runner": {
    "mode_defaults": {
      "rtl":      { "dump_depth": "all", ... },
      "gate":     { "dump_depth": "boundary", ... },
      "ams_rtl":  { "dump_depth": "boundary", ... },
      "ams_gate": { "dump_depth": "boundary", ... }
    },
    "setup_tcls": {
      "rtl":      "scripts/setup_rtl_batch.tcl",
      "gate":     "scripts/setup_gate.tcl",
      "ams_rtl":  "scripts/setup_ams_rtl.tcl",
      "ams_gate": "scripts/setup_ams_gate.tcl"
    }
  },
  "netlist_info": {
    "rtl":  { "boundary_json": "db/vlog/rtl_hier.json" },
    "gate": { "boundary_json": "db/vlog/gate_hier.json" }
  },
  "dump_strategy": {
    "rtl": {
      "top_boundary": [
        "top.hw.i_mainClk", "top.hw.i_rst_n", "top.hw.i_scl", "top.hw.io_sda",
        "top.hw.i_pcmIn", "top.hw.i_pcmSync", "top.hw.o_askData",
        "top.hw.o_askDataInv", "top.hw.o_askRefClk", "top.hw.o_refClk",
        "top.hw.o_refClkInv", "top.hw.o_btCoilShort",
        "top.hw.i_backTel_p", "top.hw.i_backTel_n", "top.hw.o_backTel_pwr_en",
        "top.hw.i_led_ctrl_r", "top.hw.i_led_ctrl_g", "top.hw.i_led_ctrl_b",
        "top.hw.o_led_r", "top.hw.o_led_g", "top.hw.o_led_b",
        "top.hw.i_earpiece_det_n", "top.hw.i_rmClkNum",
        "top.hw.i_deep_slp_en", "top.hw.i_dyn_slp_en",
        "top.hw.o_sync_req", "top.hw.o_stim_trig", "top.hw.o_serial_tp_out"
      ],
      "default_block_policy": "skip",
      "block_filter": ["top.hw.u_ext.*"],
      "write_discovered_boundaries": true,
      "boundary_depth": 3,
      "block_boundaries": {}
    },
    "gate": {
      "top_boundary": [ "..." ],
      "default_block_policy": "skip",
      "block_filter": ["top.hw.u_ext.*"],
      "write_discovered_boundaries": true,
      "boundary_depth": 3,
      "block_boundaries": {}
    }
  }
}
```

**규칙:**
- `dump_strategy.{mode}`: sim_mode별 독립적인 boundary 정의 (RTL과 gate의 신호명/계층이 다를 수 있음)
- `dump_strategy.{mode}.top_boundary`: chip top I/O (기존 `BOUNDARY_SIGNALS` 상수 이동)
- `dump_strategy.{mode}.block_boundaries`: 각 block instance의 I/O port 신호. key = instance path, value = signal list. **비어있어도 됨** — `default_block_policy` + `boundary_json`으로 lazy discovery
- `dump_strategy.{mode}.default_block_policy`: `dump_scopes`에 명시되지 않은 block에 대한 fallback 정책. `"skip"` (기본값 — opt-in 모델: 명시한 block만 dump) / `"boundary"` (opt-out 모델: 전체 dump 후 일부 skip). RTL/gate 모두 동일하게 적용. 두 모델 모두 `dump_scopes` override와 조합 가능
- `dump_strategy.{mode}.block_filter`: glob 패턴 리스트. lazy discovery 시 이 패턴에 매칭되는 block만 포함. 예: `["top.hw.u_ext.*"]`
- `dump_strategy.{mode}.write_discovered_boundaries`: `true`이면 lazy discovery 결과를 `block_boundaries`에 자동 저장 (이후 캐시 재사용). `false`이면 매 실행 동적 파싱
- `dump_strategy.{mode}.boundary_depth`: Flow A (`_boundaries_from_tcl`) / Flow B (`_boundaries_from_json`) 공통 계층 탐색 깊이. 기본값 3. **직접 편집하지 않고 `sim_discover(boundary_depth=N)`으로 설정** — sim_discover가 JSON 유효성 검사 후 이 값을 config에 기록. Flow A/B는 config에서 읽음
- `ams_rtl` → `rtl` 항목 사용, `ams_gate` → `gate` 항목 사용 (AMS는 base mode 위임)

**현재 config 상태 (2026-04-17 확인)**: `rtl_info` 미존재. v5.2에서 `netlist_info`로 처음 추가. migration 불필요.

**Runtime 조회 로직:**

```python
def get_dump_strategy(config: dict, sim_mode: str) -> dict:
    strategy = config.get("dump_strategy", {})
    # AMS는 base mode로 위임
    base_mode = "gate" if "gate" in sim_mode else "rtl"
    # v5.2: mode-keyed
    if base_mode in strategy:
        return strategy[base_mode]
    # v5.1 fallback: flat format (top_boundary/block_boundaries 직접 존재)
    if "top_boundary" in strategy or "block_boundaries" in strategy:
        return strategy
    return {}
```

**`netlist_info.{mode}.boundary_json`**: AI가 Yosys로 사전 생성한 hierarchy JSON 파일 경로 (sim_dir 기준 상대경로). MCP는 이 파일을 읽기만 한다. 파일이 없으면 Offline mode 불가 — bridge 연결 또는 JSON 사전 생성 필요.

### 3.2 Tool API 확장

**변경 대상**: `sim_batch_run`, `sim_regression`, `sim_bridge_run`, `sim_discover`

```python
sim_batch_run(
    test_name: str,
    # 기존 파라미터
    sim_mode: str = "",
    dump_depth: str = "",
    dump_signals: list[str] | None = None,
    dump_window_start_ms: int = 0,
    dump_window_end_ms: int = 0,
    # v5.2 신규
    dump_scopes: dict[str, str] | None = None,    # scope → strategy 맵
    use_dump_history: bool = False,                # per-test 이력 dump_scopes 재적용
    # ...
)

sim_regression(
    # 기존 파라미터
    sim_mode: str = "",
    dump_depth: str = "",
    dump_signals: list[str] | None = None,
    dump_window_start_ms: int = 0,
    dump_window_end_ms: int = 0,
    # v5.2 신규
    dump_scopes: dict[str, str] | None = None,    # 모든 test에 공통 적용
    use_dump_history: bool = False,                # test별 저장 dump_scopes 재적용
    # ...
)

sim_bridge_run(
    test_name: str,
    sim_mode: str = "",
    # v5.2 신규: bridge 기동 직후 TCL hierarchy 탐색 → dump_strategy.{mode} 저장
    auto_boundaries: bool = False,
    # boundary_depth는 dump_strategy.{mode}.boundary_depth config 항목으로 관리
    # ...
)

sim_discover(
    sim_dir: str = "",
    force: bool = False,
    top_module: str = "",
    # v5.2 신규: boundary 탐색 깊이 설정 → dump_strategy.{mode}.boundary_depth에 기록
    # Flow A(sim_bridge_run)/Flow B(sim_batch_run) 공통 적용. 기본값 3.
    boundary_depth: int = 3,
)
```

**`dump_scopes` 값 체계:**

| Value | 의미 |
|-------|------|
| `"all"` | `probe -create {scope} -depth all` 추가. 해당 scope의 block_boundaries는 제외 (중복 방지) |
| `"boundary"` | 명시적 경계 포함 (default 동작과 동일, 가독성용) |
| `"skip"` | 해당 scope의 block_boundaries 제외 |

**`sim_batch_run` 반환값 — `dump_summary`** (`dump_depth="boundary"` 시만 포함):

```jsonc
// [opt-in] default_block_policy="skip", dump_scopes={"...u_ext_i2cSlave": "boundary"}
// → 명시한 i2cSlave만 포함, 나머지 block은 0
{
  "status": "completed",
  "sim_time": "1ms",
  "dump_summary": {
    "dump_depth": "boundary",
    "sim_mode": "rtl",
    "top_boundary_count": 28,
    "block_boundaries": {
      "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": 4,  // 요청됨
      "top.hw.u_ext.u_ext_d_main.u_ext_askEncoder": 0, // 미요청 → 0
      "top.hw.u_ext.u_ext_d_main.u_ext_pcmInterface": 0 // 미요청 → 0
    },
    "scope_overrides": {
      "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": "boundary"
    },
    "total_signals": 32
  }
}

// [opt-out] default_block_policy="boundary", dump_scopes={"...u_heavy_block": "skip"}
// → 전체 block 포함 후 u_heavy_block만 제외
{
  "status": "completed",
  "sim_time": "1ms",
  "dump_summary": {
    "dump_depth": "boundary",
    "sim_mode": "rtl",
    "top_boundary_count": 28,
    "block_boundaries": {
      "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": 4,  // 포함
      "top.hw.u_ext.u_ext_d_main.u_ext_askEncoder": 2, // 포함
      "top.hw.u_ext.u_ext_d_main.u_ext_pcmInterface": 6, // 포함
      "top.hw.u_ext.u_ext_d_main.u_heavy_block": 0    // skip → 0
    },
    "scope_overrides": {
      "top.hw.u_ext.u_ext_d_main.u_heavy_block": "skip"
    },
    "total_signals": 40
  }
}
```

- `dump_depth="all"` 또는 `dump_signals` 직접 지정 시: `dump_summary` 미포함 (기존 동작 유지)
- `sim_mode`: 어느 mode의 `dump_strategy`가 적용됐는지 명시
- `block_boundaries.{scope}` 값이 `0` → 미포함 block (opt-in에서 미요청이거나 `"skip"` 적용)
- `scope_overrides`: 호출자가 적용한 `dump_scopes` echo (호출자 확인용)
- `total_signals`: 전체 규모 → 다음 run에서 `dump_scopes`로 조정 기준
- `dump_summary`를 `dump_history`에 기록 시 `scope_overrides`는 제외 (부모 `dump_scopes`에 이미 존재)

**`sim_regression` 집계 — `dump_stats`** (`dump_depth="boundary"` 시):

```json
{
  "passed": 5,
  "failed": 1,
  "dump_stats": {
    "per_test": {
      "TOP000": {"total": 40, "top_boundary": 28, "block_count": 3},
      "TOP003": {"total": 85, "top_boundary": 28, "block_count": 8}
    },
    "max": {"test": "TOP003", "total": 85},
    "min": {"test": "TOP000", "total": 40},
    "suggestions": [
      "TOP003 total=85 (max): dump_scopes로 heavy block skip 검토"
    ]
  }
}
```

- 테스트별 편차로 config 이상 감지 (특정 test만 비정상적으로 많으면 block_boundaries 오류 가능)
- `suggestions`: total이 평균의 2배 이상인 test에 대해 `dump_scopes` 조정 제안
- regression 전체 완료 후 summary에 포함 → 다음 실행 전 검토 지점 제공

**`dump_history` — per-test 이력 저장 구조** (`.mcp_sim_config.json`):

```jsonc
// [opt-in] default_block_policy="skip"
{
  "dump_history": {
    // TOP000: dump_scopes 없이 실행 → top_boundary 28개만, 모든 block 0
    "TOP000": {
      "last_dump_summary": {
        "dump_depth": "boundary",
        "sim_mode": "rtl",
        "top_boundary_count": 28,
        "block_boundaries": {
          "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": 0,  // 미요청 → 0
          "top.hw.u_ext.u_ext_d_main.u_ext_askEncoder": 0, // 미요청 → 0
          "top.hw.u_ext.u_ext_d_main.u_ext_pcmInterface": 0 // 미요청 → 0
        },
        "total_signals": 28
      },
      "dump_scopes": {},
      "updated_at": "2026-04-17T12:34:56"
    },
    // TOP003: i2cSlave만 요청 → i2cSlave 4개 포함, 나머지 0
    "TOP003": {
      "last_dump_summary": {
        "dump_depth": "boundary",
        "sim_mode": "rtl",
        "top_boundary_count": 28,
        "block_boundaries": {
          "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": 4,   // 요청됨
          "top.hw.u_ext.u_ext_d_main.u_ext_askEncoder": 0,  // 미요청 → 0
          "top.hw.u_ext.u_ext_d_main.u_ext_pcmInterface": 0 // 미요청 → 0
        },
        "total_signals": 32
      },
      "dump_scopes": {
        "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": "boundary"
      },
      "updated_at": "2026-04-17T12:40:12"
    }
  }
}
```

**필드 역할 분리:**

- `last_dump_summary` → 실행 **결과 관찰** (블록별 실제 dump 신호 수)
  - `block_boundaries.{scope}` 값이 `0` → `dump_scopes`에서 `"skip"` 적용됨을 의미
  - `scope_overrides` 미포함 (중복 방지 — 이미 `dump_scopes`에 존재)
  - `sim_mode` 포함 → 어느 mode의 `dump_strategy`가 적용됐는지 기록
- `dump_scopes` → 실행 **입력 정책** (`use_dump_history=True` 시 재적용할 override)
- `updated_at` → 마지막 갱신 시각

**갱신 정책**: `dump_depth="boundary"` 실행 시 **항상** 갱신 (`use_dump_history` 값과 무관).

**`use_dump_history` 파라미터** (`sim_batch_run`, `sim_regression` 모두):
- `False` (default): `dump_strategy` config + 명시된 `dump_scopes`만 사용. 결과는 `dump_history`에 기록
- `True`: `dump_history.{test}.dump_scopes` 조회 → 저장된 override 자동 적용 후 명시된 `dump_scopes`로 추가 override 가능. 이력 없는 test는 default 동작 + 경고

### 3.3 의미 매트릭스

glob 매칭: `fnmatch` 사용. `*`는 `.`을 포함한 모든 문자를 매칭 → 단일 패턴으로 임의 depth의 subtree를 커버.

`default_block_policy`에 따라 두 가지 운용 모델이 지원된다. S = `dump_strategy[mode]`.

**검증 (2026-04-17)**: xmsim `probe -create`는 glob 패턴을 네이티브 지원.
`probe -create "top.hw.u_ext.*" -depth all` → xmsim이 직접 확장 처리. Python에서 glob 확장 불필요.

**opt-in 모델** (`default_block_policy: "skip"`, 기본값 — 명시한 block만 dump):

| dump_depth | dump_scopes | `block_boundaries` 필요? | 결과 |
|------------|-------------|:---:|------|
| `"all"` | — | ❌ | `probe -create top -depth all` (backward compat) |
| `"boundary"` | — | ❌ | `S.top_boundary`만 (v5.1 동일, backward compat) |
| `"boundary"` | `{X: "boundary"}` | ✅ | `S.top_boundary + S.block_boundaries[X]` |
| `"boundary"` | `{X: "all"}` | ❌ | `S.top_boundary + probe -create X -depth all` |
| `"boundary"` | `{"top.hw.u_ext.*": "all"}` | ❌ | `S.top_boundary + probe -create top.hw.u_ext.* -depth all` (xmsim glob) |
| `"boundary"` | `{"top.hw.u_ext.*": "boundary"}` | ✅ | `S.top_boundary + u_ext 산하 모든 block 경계 신호` |
| `"boundary"` | `{"*": "boundary"}` | ✅ | `S.top_boundary + 모든 block 경계` (전체 opt-in) |

**opt-out 모델** (`default_block_policy: "boundary"` — 전체 dump 후 일부 제외):

| dump_depth | dump_scopes | `block_boundaries` 필요? | 결과 |
|------------|-------------|:---:|------|
| `"boundary"` | — | ✅ | `S.top_boundary ∪ ⋃S.block_boundaries.values()` (전체 경계) |
| `"boundary"` | `{X: "skip"}` | ✅ | 전체 경계에서 `S.block_boundaries[X]` 제외 |
| `"boundary"` | `{X: "all"}` | ⚠️ 중복제거용 | 전체 경계에서 X 경계 제거 + `probe -create X -depth all` |
| `"boundary"` | `{"top.hw.u_ext.*": "skip"}` | ✅ | u_ext 산하 전체 제외 (glob 확장은 block_boundaries로) |
| `"boundary"` | `{"*": "skip"}` | ✅ | block 전체 제외 → `S.top_boundary`만 |

### 3.4 Core 로직 변경 (`tcl_preprocessing.py`)

**신규 타입 추가**: `("hierarchical", {"signals": [...], "scope_probes": [...]}, dump_summary)`

```python
def _resolve_probe_signals(
    dump_signals: list[str] | None,
    dump_depth: str,
    dump_scopes: dict[str, str] | None = None,
    dump_strategy: dict | None = None,
    sim_mode: str = "",
) -> tuple[str, dict | list | None, dict | None]:
    """
    Returns:
        ("depth_all", None, None)                      — probe -create top -depth all
        ("signals", [...], None)                       — v5.1 호환 (block_boundaries 없을 때)
        ("hierarchical", {                             — v5.2 신규
            "signals": [...],
            "scope_probes": [{"scope": ..., "depth": "all"}, ...]
        }, dump_summary)                               — dump_summary는 dump_history 기록용
    """
    if dump_depth == "all":
        return ("depth_all", None, None)

    # 1. top_boundary: config에서 로드, fallback은 하드코딩 상수
    strategy = dump_strategy or {}
    top_signals = strategy.get("top_boundary") or BOUNDARY_SIGNALS
    signals: set[str] = set(top_signals)

    # 2. block_boundaries: default_block_policy에 따라 초기 신호 구성
    # "skip" (기본, opt-in): dump_scopes에서 명시한 block만 추가
    # "boundary" (opt-out): block_boundaries 전체 합집합 후 dump_scopes에서 일부 제외
    block_bounds = strategy.get("block_boundaries", {})
    default_policy = strategy.get("default_block_policy", "skip")
    included_scopes: set[str] = set()  # 실제로 signals에 추가된 block 추적 (dump_summary용)
    if default_policy == "boundary":
        for scope, sigs in block_bounds.items():
            signals |= set(sigs)
        included_scopes = set(block_bounds.keys())

    # 3. dump_scopes 처리
    # "all": xmsim이 glob을 네이티브 지원 (검증: 2026-04-17)
    #        → pattern을 그대로 TCL로 전달. block_boundaries 불필요.
    #        → block_boundaries에 해당 scope가 있으면 개별 신호 중복 제거만 수행.
    # "boundary": opt-in 모델에서 block_boundaries 신호를 signals에 추가.
    #             block_boundaries 없으면 lazy discovery 필요 → warn.
    # "skip": matched 신호를 signals에서 제거. block_boundaries 없으면 제거 대상 없음 (무해).
    scope_probes: list[dict] = []
    skipped: set[str] = set()
    for pattern, strategy_type in (dump_scopes or {}).items():
        matched = [s for s in block_bounds if fnmatch(s, pattern)]
        if strategy_type == "all":
            # xmsim native glob → pattern 그대로 scope_probe에 추가
            scope_probes.append({"scope": pattern, "depth": "all"})
            # block_boundaries에 해당 scope 신호가 있으면 flat signals에서 제거 (중복 방지)
            for scope in matched:
                signals -= set(block_bounds[scope])
                included_scopes.discard(scope)
        elif strategy_type == "skip":
            for scope in matched:
                signals -= set(block_bounds[scope])
                skipped.add(scope)
                included_scopes.discard(scope)
        elif strategy_type == "boundary":
            # opt-in: block_boundaries에서 신호 추가
            if matched:
                for scope in matched:
                    signals |= set(block_bounds[scope])
                    included_scopes.add(scope)
            else:
                # lazy discovery 미완료 → 경고 (호출부에서 처리)
                pass
        else:
            raise ValueError(
                f"Invalid dump_scopes value '{strategy_type}' for {pattern}. "
                "Must be 'all', 'boundary', or 'skip'."
            )

    # 4. dump_signals 개별 추가
    if dump_signals:
        signals |= set(dump_signals)

    # 5. Backward compat: block_boundaries 미정의 + dump_scopes 미사용 → v5.1 동작
    if not block_bounds and not scope_probes and not any(
        v == "boundary" for v in (dump_scopes or {}).values()
    ):
        return ("signals", sorted(signals), None)

    # 6. dump_summary 생성 (dump_history 기록 및 반환용)
    # included_scopes에 있는 block만 실제 신호 수, 나머지는 0
    # (opt-in 미요청 block과 skip block 모두 0으로 표시 → 실제 dump 여부를 정확히 반영)
    block_counts = {
        scope: (len(sigs) if scope in included_scopes else 0)
        for scope, sigs in block_bounds.items()
    }
    dump_summary = {
        "dump_depth": "boundary",
        "sim_mode": sim_mode,
        "top_boundary_count": len(top_signals),
        "block_boundaries": block_counts,
        "scope_overrides": dump_scopes or {},
        "total_signals": len(signals) + sum(
            0 for sp in scope_probes  # scope all은 신호 수 미확정 (probe 생성 후에만 알 수 있음)
        ),
    }

    return ("hierarchical", {
        "signals": sorted(signals),
        "scope_probes": scope_probes,
    }, dump_summary)
```

### 3.5 Tcl 생성 확장 (`_replace_probe_lines`)

```python
def _replace_probe_lines(
    content: str,
    probe_type: str,
    probe_info: list | dict | None,
) -> str:
    # ... (기존 filter 로직)

    if probe_type == "depth_all":
        new_probes = [f"probe -create top -depth all{db_opt}"]
    elif probe_type == "signals":
        new_probes = [
            f"probe -create {sig}{db_opt}"
            for sig in (probe_info or [])
            if sig not in existing_signals
        ]
    elif probe_type == "hierarchical":
        new_probes = []
        # 개별 신호
        for sig in probe_info["signals"]:
            if sig not in existing_signals:
                new_probes.append(f"probe -create {sig}{db_opt}")
        # scope 단위 depth all
        for sp in probe_info["scope_probes"]:
            new_probes.append(
                f"probe -create {sp['scope']} -depth {sp['depth']}{db_opt}"
            )
    # ... (기존 insert 로직)
```

### 3.6 Block Boundary 자동 감지 (Phase 2)

**검증된 사실 (2026-04-17)**: `scope -describe {scope} -sort kind` 및 `value {path}` 명령은 SHM database가 닫힌 상태에서도, **`sim_run` 없이 bridge 연결 직후에도** 정상 동작한다. xmsim이 compile 시점에 hierarchy를 내부 DB에 유지하기 때문. port 이름 탐색은 시뮬레이션 진행을 필요로 하지 않는다.

이 사실로 인해 chicken-and-egg 문제가 해소된다:

```
기존 꼬임:
  sim_discover() → sim_bridge_run() → sim_run() → sim_discover(auto_block_boundaries)
                                                    ↑ 두 번 호출, sim_run까지 필요

해소된 흐름:
  sim_discover() → sim_bridge_run(auto_boundaries=True) → sim_batch_run(dump_depth="boundary")
  (테스트명 알면)  (bridge 기동 + 즉시 탐색, sim_run 불필요)
```

#### Flow A — TCL 기반: `sim_bridge_run(auto_boundaries=True)`

bridge 기동과 boundary 탐색을 한 번에 처리. TCL `scope -describe`는 `sim_run` 없이 연결 직후 동작. **sim_mode에 무관하게 동작** (rtl/gate 모두).

**워크플로우:**

```
① [선택] sim_discover()                          # 테스트 이름 모를 때만
② sim_bridge_run("TOP000", sim_mode="rtl",
                  auto_boundaries=True)           # bridge 기동 + 즉시 TCL 탐색
   → scope -show / scope -describe 실행
   → dump_strategy.{base_mode}.block_boundaries 저장
③ sim_batch_run(dump_depth="boundary")
```

**구현**: `sim_lifecycle.py` `sim_bridge_run` — bridge TCP 연결 성공 후 `_boundaries_from_tcl(bridge, base_mode, ...)` 호출 → `mcp_config set dump_strategy.{base_mode}.block_boundaries`.

`sim_discover`는 bridge에 접근하지 않는다. TCL 탐색은 `sim_bridge_run` 전용.

#### Flow B — JSON 기반: `sim_batch_run` lazy discovery

`sim_batch_run(dump_depth="boundary")` 실행 시 `block_boundaries`가 비어있으면 `netlist_info.{mode}.boundary_json`을 읽어 **런타임에 동적으로** boundary를 구성한다. **bridge 불필요, sim_mode에 무관하게 동작** (rtl/gate 모두).

JSON 생성은 **MCP 외부 작업**. boundary JSON 경로는 사용자가 `mcp_config set`으로 **수동 1회 등록**. `sim_discover`는 등록된 경로의 유효성을 검사하고 미등록 시 Yosys 명령을 안내한다. boundary 파싱은 `sim_batch_run`이 담당.

**`sim_discover` 출력 예시 (netlist_info 상태 보고):**

```
netlist_info:
  rtl: db/vlog/rtl_hier.json ✅ (Yosys JSON, 42 modules)
  gate: 미등록
        → Yosys 실행 후 등록:
          yosys -p "read_verilog -f gate.f; hierarchy -top top; write_json gate_hier.json"
          mcp_config set netlist_info.gate.boundary_json "gate_hier.json"
```

유효성 검사 기준: 파일 존재 여부 + Yosys JSON 스키마 확인 (`modules[*].ports`, `modules[*].cells` 존재).

**워크플로우 — RTL:**

```
① AI가 filelist 탐색 후 Yosys 실행 (MCP 외부):
   yosys -p "read_verilog -sv -f <rtl_filelist.f>; hierarchy -top top; write_json rtl_hier.json"
② mcp_config set netlist_info.rtl.boundary_json "rtl_hier.json"
③ sim_discover  → rtl_hier.json 유효성 확인 ✅
④ sim_bridge_run("TOP000", sim_mode="rtl") + sim_batch_run(dump_depth="boundary")
   → block_boundaries 비어있음 → _boundaries_from_json() 자동 호출
   → write_discovered_boundaries=true이면 block_boundaries에 캐시
```

**워크플로우 — Gate:**

```
① AI가 gate netlist 경로 파악 후 Yosys 실행 (MCP 외부):
   yosys -p "read_verilog -f <gate_filelist.f>; hierarchy -top top; write_json gate_hier.json"
② mcp_config set netlist_info.gate.boundary_json "gate_hier.json"
   mcp_config set dump_strategy.gate.default_block_policy "boundary"
   mcp_config set dump_strategy.gate.block_filter '["top.hw.u_ext.*"]'
③ sim_discover  → gate_hier.json 유효성 확인 ✅
④ sim_batch_run("TOP000", sim_mode="gate", dump_depth="boundary")
   → block_boundaries 비어있음 + default_block_policy="boundary"
   → _boundaries_from_json() 자동 호출 + block_filter 적용
   → write_discovered_boundaries=true이면 block_boundaries에 자동 저장
```

**구현**: `batch_runner.py` `_run_batch_single` — `_resolve_probe_signals` 호출 전 `block_boundaries`가 비어있고 `default_block_policy` 설정 시 `_boundaries_from_json()` 호출. `block_filter` 적용 후 신호 구성. `write_discovered_boundaries=true`이면 결과를 config에 저장.

#### 역할 분리 요약

| | `sim_bridge_run(auto_boundaries=True)` | `sim_batch_run` lazy discovery |
|--|----------------------------------------|-------------------------------|
| **방법** | TCL `scope -describe` | JSON 파일 읽기 |
| **sim_mode** | rtl / gate 모두 | rtl / gate 모두 |
| **bridge** | ✅ 필수 (기동 주체) | ❌ 없음 |
| **저장** | `dump_strategy.{mode}` (즉시) | `dump_strategy.{mode}` (캐시, 선택) |
| **실행 시점** | bridge 기동 시 1회 | block_boundaries 비어있을 때마다 |
| **JSON 출처** | — | Yosys / 기타 도구 |
| **config 추가** | `auto_boundaries` 파라미터 | `default_block_policy`, `block_filter`, `write_discovered_boundaries` |

대칭적 설계 — TCL 탐색은 `sim_bridge_run` 전용, JSON 소비는 `sim_batch_run` 전용.
`sim_discover`는 boundary 파싱 역할 없음 — 등록된 `netlist_info.{mode}.boundary_json` 경로의 유효성 검사 + 미등록 시 Yosys 명령 안내만 담당.

#### 내부 함수 분리

```python
# sim_bridge_run(auto_boundaries=True) 전용
async def _boundaries_from_tcl(bridge, top_module: str, depth: int) -> dict[str, list[str]]:
    """TCL scope -describe로 hierarchy port 추출. bridge 연결 필수. rtl/gate 모두."""
    ...

# sim_batch_run lazy discovery 전용
def _boundaries_from_json(json_path: Path, top_module: str, depth: int,
                           block_filter: list[str] | None = None) -> dict[str, list[str]]:
    """AI 사전 생성 JSON에서 module port 추출. bridge 불필요. block_filter glob 적용."""
    ...
```

`_auto_detect_boundaries` 통합 함수 불필요 — 각 tool이 직접 해당 함수 호출.

#### TCL 탐색 상세 (`_boundaries_from_tcl`)

**`scope -describe` 출력 형식** (실제 검증):

```
u_ext_i2cSlave......instance of module ext_i2cSlave   ← child instance (재귀 대상)
i_scl...............input net (wire/tri) logic = Pu1   ← input port  ✓
io_sda..............inout net (wire/tri) logic = Pu1   ← inout port  ✓
o_config_dur........output net (wire/tri) logic = St0  ← output port ✓
w_config_dur........net (wire/tri) logic [7:0] = 8'h00 ← internal wire (제외)
```

**탐색 순서:**

1. `scope -show` → child instance 목록 + module 이름
2. 각 child scope에 `scope -describe {scope} -sort kind` 실행
3. `"input "` / `"output "` / `"inout "` prefix 라인만 추출 → `{scope}.{port_name}` 생성
4. `boundary_depth`까지 재귀 (child instance가 있으면 한 단계 더)
5. 결과를 `dump_strategy.{base_mode}.block_boundaries`에 기록 → `mcp_sim_config.json` 저장

**`_parse_describe_output()` 파서:**

```python
def _parse_describe_output(scope: str, output: str) -> list[str]:
    ports = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("input ", "output ", "inout ")):
            name = stripped.split(".")[0].strip()
            ports.append(f"{scope}.{name}")
    return ports
```

**제약:**

- `"instance of module"` 라인 → child scope 재귀 대상, port 목록 제외
- SystemVerilog interface/struct port는 Phase 2 제외 (Phase 3)
- 배열 port(`[7:0]`) → full-bus path 그대로 포함
- SHM database 열려있어도 탐색 무관 (probe 없이 동작 검증됨)

---

## 4. 적용 범위

### 4.1 영향 파일

| 파일 | 변경 범위 | LOC 예상 |
|------|----------|---------|
| `src/xcelium_mcp/tcl_preprocessing.py` | `_resolve_probe_signals` → `(probe_type, probe_info, dump_summary)` 반환으로 확장. `_replace_probe_lines`, `BOUNDARY_SIGNALS` 로드 | +130 |
| `src/xcelium_mcp/batch_runner.py` | `_run_batch_single`: `dump_scopes` + `use_dump_history` 전달, `dump_summary` 반환, `dump_history` 갱신. `_run_batch_regression`: 테스트별 `dump_summary` 수집 → `dump_stats` 집계 + `suggestions` 생성 | +70 |
| `src/xcelium_mcp/tools/batch.py` | `sim_batch_run`/`sim_regression`에 `dump_scopes`, `use_dump_history` 파라미터 추가. 반환에 `dump_summary`/`dump_stats` 포함 | +40 |
| `src/xcelium_mcp/tools/sim_lifecycle.py` | `sim_bridge_run`: `auto_boundaries`/`boundary_depth` 추가, bridge 연결 직후 `_boundaries_from_tcl` 호출. `sim_discover`: `auto_block_boundaries`/`boundary_depth`/`sim_mode` 추가, `_boundaries_from_json` 직접 호출 (bridge 접근 없음) | +50 |
| `src/xcelium_mcp/env_detection.py` | `_parse_describe_output`, `_boundaries_from_tcl`, `_boundaries_from_json` 신규 (Phase 2). `_auto_detect_boundaries` 통합 함수 불필요 — 각 tool이 직접 호출 | +120 |
| `src/xcelium_mcp/registry.py` | config schema 주석 업데이트 | +10 |
| `.mcp_sim_config.json` (cloud0) | `dump_strategy` 섹션 추가 | 수동 작성 or sim_discover |
| `tests/test_hierarchical_dump.py` | 유닛 + 통합 테스트 | +200 |
| `.ai/analysis/boundary_signals.analysis.md` | `dump_strategy` 이관 설명 추가 | +20 |

**총 예상**: ~620 LOC 추가, Phase 1 MVP는 ~250 LOC (glob + dump_summary + dump_history 포함).

### 4.2 영향 대상 Tool (25개 중)

| Tool | 영향 | 변경 |
|------|------|------|
| `sim_batch_run` | O | `dump_scopes`, `use_dump_history` 추가. 반환에 `dump_summary` 포함 (`dump_depth="boundary"` 시). `dump_history` 갱신 |
| `sim_regression` | O | `dump_scopes`, `use_dump_history` 추가. 반환에 `dump_stats` 포함. `dump_history` 갱신 |
| `sim_bridge_run` | O | `auto_boundaries` 추가 → 연결 직후 config의 `boundary_depth` 읽어 TCL 탐색 → `dump_strategy.{mode}` 저장 (Flow A) |
| `sim_discover` | O | `boundary_depth: int = 3` 파라미터 추가 → JSON 유효성 검사 후 `dump_strategy.{mode}.boundary_depth` config에 기록. 미등록 시 Yosys 명령 안내 출력 |
| `mcp_config` | X | — (dump_strategy/netlist_info/dump_history 조회·수정은 dot-notation으로 기존 동작) |
| 나머지 20개 | X | — |

---

## 5. 단계별 구현 Roadmap

### Phase 1: MVP (Manual block_boundaries, ~180 LOC)

| Item | 설명 | 완료 기준 |
|------|------|----------|
| P1-1 | `tcl_preprocessing.py`: `_resolve_probe_signals` 확장 | `hierarchical` 타입 반환, unit test PASS |
| P1-2 | `tcl_preprocessing.py`: `_replace_probe_lines` hierarchical 처리 | Tcl output에 scope_probes 포함 |
| P1-3 | `tools/batch.py`: `dump_scopes`, `use_dump_history` 파라미터 추가 + validation. 반환에 `dump_summary`/`dump_stats` 포함 | MCP schema에 노출 |
| P1-4 | `batch_runner.py`: `dump_scopes`/`use_dump_history` 전달. `_run_batch_single`: `dump_summary` 반환 + `dump_history` 갱신. `_run_batch_regression`: `dump_stats` 집계 + `suggestions` 생성 | `_run_batch_single/_regression`에 연결 |
| P1-5 | `registry.py`: `dump_strategy` 로드 logic | `load_sim_config` 반환값에 포함 |
| P1-6 | `.mcp_sim_config.json` (cloud0): 수동 작성 — I2C/askEncoder/pcmInterface 3개 block | TOP015 테스트용 |
| P1-7 | `tcl_preprocessing.py`: `dump_scopes` glob 매칭 — `fnmatch`로 `block_boundaries` key 필터링 | exact key와 동일 코드 경로, `*`가 `.` 포함 매칭 확인 |
| P1-8 | Unit test: `test_resolve_probe_signals_hierarchical` | 10 case (empty, all, boundary, scopes, glob subtree, glob all-skip) |
| P1-9 | Integration test: TOP015 Gate mode, dump_scopes 4조합 | SHM 크기 검증 |

**완료 조건**: TOP015 regression PASS + dump_scopes 시나리오 4개 수동 검증

### Phase 2: Auto-detection (~150 LOC)

두 Flow는 대칭적 독립 구현. 서로 fallback 관계 없음.

**Flow A 구현 포인트** (`sim_bridge_run`): bridge TCP 연결 성공 후 `_boundaries_from_tcl(bridge, base_mode, ...)` 직접 호출. `mcp_config set dump_strategy.{base_mode}.block_boundaries`로 저장.

**Flow B 구현 포인트** (`batch_runner.py`): `_run_batch_single` 진입 시 `block_boundaries`가 비어있고 `default_block_policy` 설정된 경우 `_boundaries_from_json()` 호출. `block_filter` glob 적용 후 신호 구성. `write_discovered_boundaries=true`면 config 저장.

```python
# tools/sim_lifecycle.py — sim_bridge_run 확장 (Flow A: TCL 전용)
# bridge TCP 연결 성공 직후 추가:
if auto_boundaries:
    base_mode = "gate" if "gate" in sim_mode else "rtl"
    strategy = get_dump_strategy(config, sim_mode)
    depth = strategy.get("boundary_depth", 3)
    boundaries = await _boundaries_from_tcl(bridge, top_module or "top", depth)
    await config_action("set", "config", f"dump_strategy.{base_mode}.block_boundaries",
                        json.dumps(boundaries))
```

```python
# batch_runner.py — _run_batch_single 내 lazy discovery (Flow B: JSON 전용)
strategy = get_dump_strategy(config, sim_mode)
if not strategy.get("block_boundaries") and strategy.get("default_block_policy"):
    base_mode = "gate" if "gate" in sim_mode else "rtl"
    json_rel = config.get("netlist_info", {}).get(base_mode, {}).get("boundary_json", "")
    if json_rel and (sim_dir / json_rel).exists():
        boundaries = _boundaries_from_json(
            sim_dir / json_rel,
            top_module or "top",
            boundary_depth=strategy.get("boundary_depth", 3),
            block_filter=strategy.get("block_filter"),
        )
        strategy["block_boundaries"] = boundaries
        if strategy.get("write_discovered_boundaries"):
            await config_action("set", "config",
                                f"dump_strategy.{base_mode}.block_boundaries",
                                json.dumps(boundaries))
    else:
        warn(f"netlist_info.{base_mode}.boundary_json 미설정 — top_boundary만 사용")
```

| Item | 설명 |
|------|------|
| P2-1 | `env_detection.py`: `_parse_describe_output(scope, output)` — `scope -describe -sort kind` 출력 파서. `input`/`output`/`inout` prefix 라인 → `{scope}.{port_name}` 리스트 반환 |
| P2-2 | `env_detection.py`: `_boundaries_from_tcl(bridge, top_scope, depth)` — TCL bridge로 재귀 탐색. `scope -show` → child instances → `scope -describe -sort kind` → P2-1 파서 적용. `sim_bridge_run` 전용. bridge 연결 필수 |
| P2-3 | `env_detection.py`: `_boundaries_from_json(json_path, top_module, depth, block_filter)` — AI가 Yosys로 사전 생성한 `boundary_json`에서 module port 추출. `sim_batch_run` lazy discovery 전용. bridge 불필요. `block_filter` glob 적용. flatten된 module은 cells 미포함으로 자연 제외 |
| P2-4 | `tools/sim_lifecycle.py`: `sim_bridge_run`에 `auto_boundaries` 추가, 연결 직후 `strategy.get("boundary_depth", 3)`으로 config에서 탐색 깊이 읽어 `_boundaries_from_tcl` 호출 (Flow A). `sim_discover`에 `boundary_depth` 파라미터 추가 — JSON 유효성 검사 후 `dump_strategy.{mode}.boundary_depth`에 기록 |
| P2-5 | `batch_runner.py`: `_run_batch_single` 내 lazy discovery 로직 — `block_boundaries` 비어있을 때 `_boundaries_from_json` 자동 호출 (Flow B) |
| P2-6 | `registry.py`: `get_dump_strategy`에 `default_block_policy`, `block_filter`, `write_discovered_boundaries`, `boundary_depth` 로드 로직 추가. `tools/sim_lifecycle.py` `sim_discover`: `boundary_depth` 파라미터 수신 후 `config_action("set", ..., "dump_strategy.{mode}.boundary_depth", boundary_depth)` 호출 |
| P2-7 | Test Flow A: `sim_bridge_run("TOP000", auto_boundaries=True)` → `dump_strategy.rtl.block_boundaries` 저장 확인, ~20개 block 감지 |
| P2-8 | Test Flow B: Yosys JSON 준비 → `sim_batch_run("TOP000", sim_mode="gate", dump_depth="boundary")` → lazy discovery 동작, `block_filter` 적용, `write_discovered_boundaries` 캐시 확인 |

**완료 조건**: Flow A/B 각각 1회 실행으로 수동 `block_boundaries` 작성 불필요

### Phase 3: Advanced (optional, ~50 LOC)

| Item | 설명 |
|------|------|
| P3-1 | Pre-defined groups (`"group:all_i2c": "all"`) |
| P3-2 | `dump_strategy.scope_groups` config 섹션 |

---

## 6. 검증 계획

### 6.1 Unit Tests (`test_hierarchical_dump.py`)

```python
def test_dump_depth_all_backward_compat():
    """dump_depth='all' → 기존 동작, dump_summary=None"""
    probe_type, probe_info, summary = _resolve_probe_signals([], "all")
    assert probe_type == "depth_all"
    assert probe_info is None
    assert summary is None

def test_boundary_with_block_boundaries():
    """opt-out 모델: default_block_policy='boundary' → top + all block boundaries 합집합"""
    strategy = {
        "top_boundary": ["top.i_clk"],
        "default_block_policy": "boundary",
        "block_boundaries": {
            "top.u_blk": ["top.u_blk.i_a", "top.u_blk.o_b"],
        },
    }
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary", dump_strategy=strategy, sim_mode="rtl"
    )
    assert probe_type == "hierarchical"
    assert set(probe_info["signals"]) == {"top.i_clk", "top.u_blk.i_a", "top.u_blk.o_b"}
    assert probe_info["scope_probes"] == []
    assert summary["top_boundary_count"] == 1
    assert summary["block_boundaries"] == {"top.u_blk": 2}
    assert summary["total_signals"] == 3

def test_dump_scopes_all_override():
    """특정 block을 all로 override → 해당 block boundary 제거 + scope_probe 추가"""
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.u_blk": "all"},
        dump_strategy=strategy,
    )
    assert "top.u_blk.i_a" not in probe_info["signals"]
    assert probe_info["scope_probes"] == [{"scope": "top.u_blk", "depth": "all"}]
    assert summary["block_boundaries"]["top.u_blk"] == 2  # 원래 개수 유지

def test_dump_scopes_skip():
    """skip → 해당 block boundary만 제외, count=0"""
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.u_blk": "skip"},
        dump_strategy=strategy,
    )
    assert "top.u_blk.i_a" not in probe_info["signals"]
    assert probe_info["scope_probes"] == []
    assert summary["block_boundaries"]["top.u_blk"] == 0  # skip → 0

def test_glob_subtree_skip():
    """glob: top.u_ext.* → u_ext 산하 모든 block 제외"""
    ...

def test_mixed_all_and_skip():
    """여러 block 조합"""
    ...

def test_no_block_boundaries_fallback():
    """block_boundaries 미정의 → v5.1 동작 (signals 타입, summary=None)"""
    probe_type, probe_info, summary = _resolve_probe_signals([], "boundary", dump_strategy={})
    assert probe_type == "signals"
    assert summary is None

def test_invalid_dump_scopes_value():
    """잘못된 값 → ValueError"""
    with pytest.raises(ValueError):
        _resolve_probe_signals([], "boundary", dump_scopes={"top": "invalid"})

def test_dump_history_written_on_boundary_run():
    """dump_depth='boundary' 실행 → dump_history에 last_dump_summary + dump_scopes 기록"""
    ...

def test_use_dump_history_applies_saved_scopes():
    """use_dump_history=True → dump_history.{test}.dump_scopes 자동 적용"""
    ...
```

### 6.2 Integration Tests (cloud0)

```python
# Test 1: boundary 확장 의미 검증 (opt-out 모델)
# 전제: config dump_strategy.gate.default_block_policy="boundary" 사전 설정
result = sim_batch_run(test_name="TOP015", dump_depth="boundary", sim_mode="gate")
# 기대: SHM에 top I/O + 모든 block 경계 포함
inspect_signal(action="check_dump", signals=["top.hw.i_scl", "...u_ext_i2cSlave.o_config_dur"])
# → Found: 2 / Missing: 0

# Test 2: 특정 block만 all
result = sim_batch_run(
    test_name="TOP015",
    dump_depth="boundary",
    dump_scopes={"top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": "all"},
    sim_mode="gate",
)
# 기대: i2cSlave 내부 상세 신호 존재
inspect_signal(action="check_dump",
    signals=["top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_loopState"])
# → Found: 1

# Test 3: skip — clock block 제외 (exact key)
result = sim_batch_run(
    test_name="TOP015",
    dump_depth="boundary",
    dump_scopes={"top.hw.u_ext.u_ext_clk": "skip"},
    sim_mode="rtl",
)
# 기대: clock block 경계 신호 제외됨

# Test 4: glob subtree skip — u_ext_d_main 산하 전체 제외
result = sim_batch_run(
    test_name="TOP015",
    dump_depth="boundary",
    dump_scopes={"top.hw.u_ext.u_ext_d_main.*": "skip"},
    sim_mode="gate",
)
# 기대: i2cSlave, askEncoder, pcmInterface 등 u_ext_d_main 산하 경계 신호 모두 제외
# top_boundary + u_ext_d_main 이외 block 경계만 남음

# Test 5: glob all-skip — top_boundary만 dump
result = sim_batch_run(
    test_name="TOP015",
    dump_depth="boundary",
    dump_scopes={"*": "skip"},
    sim_mode="gate",
)
# 기대: block_boundaries 전체 제외 → top I/O 28개만 dump (v5.1과 동일 신호 집합)
```

### 6.3 Regression Tests

- 기존 v5.1 regression 21/21 PASS 유지 (NFR-01)
- `dump_scopes` 미지정 + `block_boundaries` 미정의 → v5.1 완전 동일 (NFR-02, NFR-03)
- SHM 크기 측정: TOP015 Gate mode
  - `"all"`: ~500MB (기준)
  - `"boundary"` (v5.2 확장): ~30MB 예상 (~6%)
  - `"boundary" + dump_scopes={1 block: "all"}`: ~80MB 예상 (~16%)

### 6.4 Security (v5.0 review 원칙 유지)

- `dump_scopes` key/value 모두 `sanitize_signal_name` 적용
- scope path 검증 (exact key): `[A-Za-z0-9_.]+` 허용
- glob 패턴 (Phase 1 FR-08): `*` 1종만 허용. `?`, `[`, `]`, shell metacharacter 차단. `fnmatch`는 OS shell을 호출하지 않으므로 injection 위험 없음

---

## 7. Risks and Mitigations

| Risk | 영향 | 대응 |
|------|:---:|------|
| `block_boundaries` 수동 작성 부담 | Medium | Phase 2 auto-detection으로 완화 |
| 기존 hardcoded `BOUNDARY_SIGNALS`와 config 불일치 | Low | config 미정의 시 fallback |
| `"hierarchical"` 타입 반환으로 기존 caller 오동작 | High | block_boundaries 미정의 시 `"signals"` 반환 (backward compat) |
| Gate-level netlist 직접 파싱 복잡도 (Phase 2 Offline) | Medium | Online TCL mode가 primary — gate/AMS 모두 대응. Offline은 Yosys 위임으로 파싱 복잡도 회피. Yosys 미설치 시 RTL 전용으로 제한 + 경고 |
| Tcl `probe -create {scope} -depth all` + 개별 신호 혼합 에러 | Medium | xmsim 실제 동작 사전 검증 (P1-2) |
| 대규모 `signals` set → Tcl 파일 크기 증가 | Low | 1000 신호 기준 Tcl 파일 ~50KB, 무시 가능 |

---

## 8. 성공 기준

| Metric | Target |
|--------|--------|
| Unit test coverage | ≥ 90% for `_resolve_probe_signals` |
| Integration test | 3 시나리오 (boundary 확장, scopes all, scopes skip) PASS |
| v5.1 regression | 21/21 PASS 유지 |
| SHM 크기 감소율 | `"boundary"` v5.2 / `"all"` ≤ 10% |
| Backward compat | 기존 `dump_depth="boundary"` 호출 시 block_boundaries 유무에 관계없이 동작 |
| Security | `dump_scopes` injection 차단 (기존 sanitize_signal_name 재사용) |

---

## 9. Related Documents

- **디버깅 워크플로우**: `xcelium-mcp-debugging-workflow.plan.md` §1C, §1D (dump_depth 설명 업데이트 필요)
- **v4.3 dump strategy**: `xcelium-mcp-v4.3-dump-strategy.plan.md` §1.2 (block-level 예외 언급)
- **v5.0 code review**: `xcelium-mcp-v5-code-review.report.md` (security 원칙 재사용)
- **Boundary signal list**: `.ai/analysis/boundary_signals.analysis.md` (dump_strategy로 이관 필요)

---

## 10. Open Questions

1. **v5.2 릴리스 단위**: Phase 1만으로 v5.2 릴리스? Phase 2까지 포함? (권장: Phase 1 MVP → v5.2.0, Phase 2 → v5.2.1)
2. **`top_boundary` migration**: 하드코딩 상수를 config로 옮긴 후, `BOUNDARY_SIGNALS` 상수는 deprecated로 둘지 제거할지
3. **`dump_scopes` key 정규화**: trailing slash, 절대/상대 경로 처리 규칙
4. **기본 block_boundaries**: venezia-fpga에서 몇 개 block까지 포함? (초안: ~10개 핵심 block)

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-04-09 | Initial draft. Problem + Solution + API 설계 + 3-phase roadmap |
| 0.2 | 2026-04-17 | FR-07 두 모드 확장 (Online TCL / Offline JSON). 3.6절 전면 개정 — probe-free 검증 기반. Phase 2 Option A 클로저 패턴 확정. `dump_strategy`/`netlist_info` mode-keyed 구조 도입. pyverilog 제거. MCP는 Yosys 직접 호출 안 함 — AI가 사전 생성한 `boundary_json` 소비. `sim_discover`에 `sim_mode` 파라미터 추가 |
| 0.3 | 2026-04-17 | FR-07 Flow A/B 독립 구현으로 재설계 (fallback 관계 제거). `sim_regression` 시그니처 추가. `use_dump_history` 파라미터 도입 (`sim_batch_run`/`sim_regression`). `dump_summary` 반환 스키마 (`sim_mode` 필드 추가, `scope_overrides` 포함). `dump_stats` 집계 구조 및 `suggestions` 생성 로직. `dump_history` per-test 이력 구조 (`last_dump_summary` + `dump_scopes` + `updated_at`). `_resolve_probe_signals` 3-tuple 반환으로 확장. `sim_discover` bridge 접근 제거 (JSON 전용). Phase 2 코드 스니펫 두 Flow 독립 분리. 문서 전체 blank line 포맷팅 개선 |
| 0.4 | 2026-04-17 | Option C 도입 (`default_block_policy`, `block_filter`, `write_discovered_boundaries`). Flow B를 `sim_discover`에서 `sim_batch_run` lazy discovery로 이동. `sim_discover`는 JSON 경로 유효성 검사 + Yosys 안내만 담당. RTL/gate 대칭 config 구조 (`netlist_info` + `dump_strategy` 동일 키). `sim_discover` API `auto_block_boundaries` 파라미터 제거 |
| 0.5 | 2026-04-17 | xmsim native glob 검증 (`probe -create "top.hw.u_ext.*" -depth all` 동작 확인). `_resolve_probe_signals` opt-in 기본값 (`default_block_policy: "skip"`). scope_probes "all" 타입은 block_bounds 매칭 없어도 pattern 직접 전달. 문서 전체 일관성 업데이트 (Executive Summary, FR-01, 1.2 워크플로우, 코드 스니펫, 유닛·통합 테스트) |
| 0.4 | 2026-04-17 | Flow B를 `sim_discover` → `sim_batch_run` lazy discovery로 이전 (대칭적 설계). FR-07a/b/c 추가 (`default_block_policy`, `block_filter`, `write_discovered_boundaries`). `sim_discover` boundary 파라미터 전면 제거. config gate 예시: `block_boundaries: {}` 빈 상태에서 동작. `_boundaries_from_json`에 `block_filter` 파라미터 추가. Phase 2 roadmap 재작성 (P2-5~P2-8) |
| 0.5 | 2026-04-17 | xmsim `probe -create` glob 네이티브 지원 검증 (TOP000 bridge 실험). `dump_scopes: {glob: "all"}` 처리 단순화 — Python glob 확장 불필요, pattern 그대로 TCL 전달. `_resolve_probe_signals` "all" 분기 재작성. 3.3 의미 매트릭스에 `block_boundaries` 필요 여부 컬럼 추가. opt-in/opt-out 모델 표 개정 |
