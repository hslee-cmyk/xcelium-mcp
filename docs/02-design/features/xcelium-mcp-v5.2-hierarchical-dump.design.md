# Design: xcelium-mcp v5.2 — Hierarchical Dump Strategy

> **Feature**: `xcelium-mcp-v5.2-hierarchical-dump`
>
> **Date**: 2026-07-01
> **Status**: Draft
> **Plan**: `docs/01-plan/features/xcelium-mcp-v5.2-hierarchical-dump.plan.md`
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)

---

## Context Anchor

| | |
|--|--|
| **WHY** | `dump_depth="boundary"`가 top I/O 28개만 dump → Gate 내부 block 실패 특정 불가. `"all"`은 SHM 폭증. 중간 해상도(block 경계 합집합)가 없다. |
| **WHO** | Gate/AMS 디버깅 엔지니어. 첫 실행 후 FAIL 원인 block을 빠르게 특정해야 하는 상황. |
| **RISK** | backward compat 파괴 시 기존 21 regression 모두 영향. `"hierarchical"` 타입 도입이 기존 caller를 오동작시킬 수 있음. |
| **SUCCESS** | v5.1 regression 21/21 PASS 유지. boundary 확장 시 SHM ≤ full 10%. dump_scopes로 block별 전략 조합. |
| **SCOPE** | Phase 1 MVP(수동 block_boundaries + dump_scopes + dump_summary + dump_history). Phase 2 auto-detection(Flow A TCL, Flow B JSON). Phase 3 optional. |

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | `dump_depth="boundary"`는 top I/O 28개만 dump해 Gate/AMS block 실패 특정이 불가하고, `"all"`은 SHM 폭증. 중간 단계가 없음. |
| **Solution** | `dump_scopes` 파라미터 + config `dump_strategy.{mode}` 섹션으로 block별 `all`/`boundary`/`skip` 조합. `_resolve_probe_signals`를 3-tuple 반환으로 확장. opt-in/opt-out 두 모델 지원. Phase 2에서 TCL(Flow A)/JSON(Flow B) 자동 감지. |
| **Function UX Effect** | `dump_scopes={"*": "boundary"}` 또는 `default_block_policy="boundary"`로 전체 block 경계 포함. `dump_scopes={"...u_ext_i2cSlave": "all"}`로 특정 block full dump. 기존 호출 변경 불필요. |
| **Core Value** | Gate/AMS 디버깅 해상도를 chip 경계 → block 경계로 상향. SHM full 대비 ≤10%로 유지하면서 실패 block 특정 속도 향상. |

---

## 1. 파일 구조 및 변경 범위

```
src/xcelium_mcp/
├── tcl_preprocessing.py       [수정] _resolve_probe_signals 3-tuple 확장
│                                      _replace_probe_lines hierarchical 타입 추가
│                                      get_dump_strategy() helper 추가
│                                      BOUNDARY_SIGNALS → fallback 전용 (config 우선)
│                                      _preprocess_setup_tcl 시그니처 확장
├── sim_env_detection.py       [수정-Phase2] _boundaries_from_tcl, _boundaries_from_json,
│                                            _parse_describe_output 추가
├── batch_runner.py            [수정] _run_batch_single: dump_scopes/use_dump_history 전달,
│                                      dump_summary 반환, dump_history 갱신
│                                      _run_batch_regression: dump_stats 집계 + suggestions
├── registry.py                [수정] save_sim_config() 추가, config 캐시 무효화
├── tools/
│   ├── batch.py               [수정] sim_batch_run/sim_regression: dump_scopes, use_dump_history 파라미터
│   │                                  dump_summary/dump_stats 반환
│   └── sim_lifecycle.py       [수정-Phase2] sim_bridge_run: auto_boundaries 파라미터
│                                             sim_discover: boundary_depth 파라미터
tests/
└── test_hierarchical_dump.py  [신규] Phase 1 unit tests (10 case) + integration skeleton
```

**신규 파일**: `tests/test_hierarchical_dump.py` 1개만 추가.  
**수정 파일**: 기존 6개 파일 (Phase 1: 4개, Phase 2: 2개 추가).

---

## 2. Config 스키마 (`.mcp_sim_config.json`)

### 2.1 신규 섹션

```json
{
  "version": 2,
  "netlist_info": {
    "rtl":  { "boundary_json": "db/vlog/rtl_hier.json" },
    "gate": { "boundary_json": "db/vlog/gate_hier.json" }
  },
  "dump_strategy": {
    "rtl": {
      "top_boundary": ["top.hw.i_mainClk", "top.hw.i_rst_n", "..."],
      "default_block_policy": "skip",
      "block_filter": ["top.hw.u_ext.*"],
      "write_discovered_boundaries": true,
      "boundary_depth": 3,
      "block_boundaries": {}
    },
    "gate": {
      "top_boundary": ["top.hw.i_mainClk", "..."],
      "default_block_policy": "skip",
      "block_filter": ["top.hw.u_ext.*"],
      "write_discovered_boundaries": true,
      "boundary_depth": 3,
      "block_boundaries": {
        "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": [
          "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.i_scl",
          "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.io_sda",
          "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.o_config_dur"
        ]
      }
    }
  },
  "dump_history": {
    "TOP000": {
      "last_dump_summary": { "dump_depth": "boundary", "sim_mode": "rtl", "total_signals": 28 },
      "dump_scopes": {},
      "updated_at": "2026-07-01T12:00:00"
    }
  }
}
```

### 2.2 `get_dump_strategy()` helper (`tcl_preprocessing.py`)

```python
def get_dump_strategy(config: dict, sim_mode: str) -> dict:
    strategy = config.get("dump_strategy", {})
    base_mode = "gate" if "gate" in sim_mode else "rtl"
    if base_mode in strategy:
        return strategy[base_mode]
    # v5.1 flat format fallback
    if "top_boundary" in strategy or "block_boundaries" in strategy:
        return strategy
    return {}
```

**규칙:**
- `ams_rtl` → `rtl`, `ams_gate` → `gate` 위임
- config 없으면 `{}` 반환 → 기존 `BOUNDARY_SIGNALS` fallback
- `boundary_depth`: Flow A/B 공통 계층 탐색 깊이 (기본 3)

---

## 3. `_resolve_probe_signals` 확장 (`tcl_preprocessing.py`)

### 3.1 시그니처 변경

```python
# v5.1 (현재)
def _resolve_probe_signals(
    dump_signals: list[str] | None,
    dump_depth: str,
) -> tuple[str, list[str] | None]:

# v5.2 (변경)
def _resolve_probe_signals(
    dump_signals: list[str] | None,
    dump_depth: str,
    dump_scopes: dict[str, str] | None = None,
    dump_strategy: dict | None = None,
    sim_mode: str = "",
) -> tuple[str, dict | list | None, dict | None]:
```

### 3.2 반환 타입 3종

| 반환 타입 | 조건 | 2번째 값 | 3번째 값 |
|-----------|------|----------|----------|
| `"depth_all"` | `dump_depth="all"` | `None` | `None` |
| `"signals"` | block_boundaries 미정의 + dump_scopes 없음 | `list[str]` | `None` |
| `"hierarchical"` | block_boundaries 존재 or dump_scopes 있음 | `{"signals": [...], "scope_probes": [...]}` | `dump_summary dict` |

**backward compat**: `block_boundaries` 미정의 + `dump_scopes` 없음 → `"signals"` 반환 (v5.1 동일).

### 3.3 핵심 로직

```python
from fnmatch import fnmatch

def _resolve_probe_signals(
    dump_signals, dump_depth,
    dump_scopes=None, dump_strategy=None, sim_mode="",
):
    if dump_depth == "all":
        return ("depth_all", None, None)

    strategy = dump_strategy or {}
    top_signals = strategy.get("top_boundary") or BOUNDARY_SIGNALS
    signals: set[str] = set(top_signals)
    block_bounds: dict = strategy.get("block_boundaries", {})
    default_policy = strategy.get("default_block_policy", "skip")
    included_scopes: set[str] = set()

    # opt-out: 전체 block 기본 포함
    if default_policy == "boundary":
        for scope, sigs in block_bounds.items():
            signals |= set(sigs)
        included_scopes = set(block_bounds.keys())

    # dump_scopes 처리
    scope_probes: list[dict] = []
    for pattern, strat in (dump_scopes or {}).items():
        matched = [s for s in block_bounds if fnmatch(s, pattern)]
        if strat == "all":
            scope_probes.append({"scope": pattern, "depth": "all"})
            for sc in matched:
                signals -= set(block_bounds[sc])
                included_scopes.discard(sc)
        elif strat == "skip":
            for sc in matched:
                signals -= set(block_bounds[sc])
                included_scopes.discard(sc)
        elif strat == "boundary":
            for sc in matched:
                signals |= set(block_bounds[sc])
                included_scopes.add(sc)
        else:
            raise ValueError(
                f"Invalid dump_scopes value {strat!r}. Must be 'all', 'boundary', or 'skip'."
            )

    if dump_signals:
        signals |= set(dump_signals)

    # backward compat: block_boundaries 미정의 + 신규 파라미터 미사용
    if not block_bounds and not scope_probes and not any(
        v == "boundary" for v in (dump_scopes or {}).values()
    ):
        return ("signals", sorted(signals), None)

    # dump_summary 생성
    block_counts = {
        sc: (len(sigs) if sc in included_scopes else 0)
        for sc, sigs in block_bounds.items()
    }
    dump_summary = {
        "dump_depth": "boundary",
        "sim_mode": sim_mode,
        "top_boundary_count": len(top_signals),
        "block_boundaries": block_counts,
        "scope_overrides": dump_scopes or {},
        "total_signals": len(signals),
    }

    return ("hierarchical", {
        "signals": sorted(signals),
        "scope_probes": scope_probes,
    }, dump_summary)
```

---

## 4. `_replace_probe_lines` 확장 (`tcl_preprocessing.py`)

```python
def _replace_probe_lines(
    content: str,
    probe_type: str,
    probe_info: list | dict | None,  # v5.2: list|dict 모두 허용
) -> str:
    # ... 기존 filter 로직 (변경 없음) ...

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
        for sig in probe_info["signals"]:
            if sig not in existing_signals:
                new_probes.append(f"probe -create {sig}{db_opt}")
        for sp in probe_info["scope_probes"]:
            new_probes.append(f"probe -create {sp['scope']} -depth {sp['depth']}{db_opt}")

    # ... 기존 insert 로직 (변경 없음) ...
```

---

## 5. `_preprocess_setup_tcl` 시그니처 확장 (`tcl_preprocessing.py`)

```python
async def _preprocess_setup_tcl(
    sim_dir: str, runner: dict, test_name: str, sim_mode: str = "",
    dump_depth: str = "all",
    dump_signals: list[str] | None = None,
    dump_window: dict | None = None,
    # v5.2 신규
    dump_scopes: dict[str, str] | None = None,
    dump_strategy: dict | None = None,
) -> str:
    # ...
    probe_type, probe_info, dump_summary = _resolve_probe_signals(
        dump_signals, dump_depth,
        dump_scopes=dump_scopes,
        dump_strategy=dump_strategy,
        sim_mode=sim_mode,
    )
    new_content = _replace_probe_lines(content, probe_type, probe_info)
    # ...
    return out_path, dump_summary  # dump_summary를 함께 반환
```

> **주의**: `_preprocess_setup_tcl`의 반환값이 `str` → `tuple[str, dict | None]`로 변경됨.  
> 기존 호출부(`batch_runner.py`)에서 언패킹 처리 필요.

---

## 6. Tool API 확장 (`tools/batch.py`)

### 6.1 `sim_batch_run` 파라미터 추가

```python
@mcp.tool()
async def sim_batch_run(
    test_name: str,
    # ... 기존 파라미터 ...
    dump_depth: str = "",
    dump_window_start_ms: int = 0,
    dump_window_end_ms: int = 0,
    # v5.2 신규
    dump_scopes: dict[str, str] | None = None,
    use_dump_history: bool = False,
) -> str:
    """
    ...
    Args:
        dump_scopes: block 경로 → 전략 맵. 예: {"top.hw.u_ext.*": "boundary"}.
                    값: "all" | "boundary" | "skip". glob 패턴(fnmatch) 지원.
        use_dump_history: True → 이전 실행의 dump_scopes를 자동 재적용.
    """
```

**validation 추가:**
```python
# dump_scopes 검증
if dump_scopes:
    valid_vals = {"all", "boundary", "skip"}
    for k, v in dump_scopes.items():
        if not re.fullmatch(r"[\w.*]+", k):
            return f"Invalid dump_scopes key {k!r}: only [A-Za-z0-9_.* ] allowed."
        if v not in valid_vals:
            return f"Invalid dump_scopes value {v!r} for {k!r}. Must be one of {valid_vals}."
```

### 6.2 `sim_regression` 파라미터 추가

```python
@mcp.tool()
async def sim_regression(
    # ... 기존 파라미터 ...
    dump_scopes: dict[str, str] | None = None,  # 모든 test에 공통 적용
    use_dump_history: bool = False,              # test별 저장 dump_scopes 재적용
) -> str:
```

### 6.3 반환값 확장

`dump_depth="boundary"` 시 JSON 출력에 `dump_summary` / `dump_stats` 포함:

```json
// sim_batch_run 반환 (boundary 모드)
{
  "status": "completed",
  "dump_summary": {
    "dump_depth": "boundary",
    "sim_mode": "gate",
    "top_boundary_count": 28,
    "block_boundaries": {
      "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": 4,
      "top.hw.u_ext.u_ext_d_main.u_ext_askEncoder": 0
    },
    "scope_overrides": {},
    "total_signals": 32
  }
}
```

---

## 7. `batch_runner.py` — `_run_batch_single` 확장

```python
async def _run_batch_single(
    sim_dir, runner, test_name, ...,
    dump_scopes=None,
    use_dump_history=False,
) -> dict:  # 반환에 dump_summary 포함

    config = await load_sim_config(sim_dir)
    dump_strategy = get_dump_strategy(config or {}, sim_mode)

    # use_dump_history: 이전 dump_scopes 로드
    effective_scopes = dump_scopes or {}
    if use_dump_history and config:
        history = config.get("dump_history", {}).get(test_name, {})
        saved = history.get("dump_scopes", {})
        if saved:
            merged = dict(saved)
            merged.update(effective_scopes)   # 명시 override 우선
            effective_scopes = merged
        elif not effective_scopes:
            logger.warning(f"use_dump_history=True but no history for {test_name}")

    # Phase 2 lazy discovery (Flow B)
    if not dump_strategy.get("block_boundaries") and dump_strategy.get("default_block_policy"):
        boundaries = await _lazy_discover_boundaries(sim_dir, dump_strategy, sim_mode)
        if boundaries:
            dump_strategy = {**dump_strategy, "block_boundaries": boundaries}

    # tcl 전처리
    tcl_path, dump_summary = await _preprocess_setup_tcl(
        sim_dir, runner, test_name, sim_mode,
        dump_depth=effective_dump_depth,
        dump_signals=dump_signals,
        dump_window=dump_window,
        dump_scopes=effective_scopes if effective_scopes else None,
        dump_strategy=dump_strategy,
    )

    # dump_history 갱신 (dump_depth="boundary" 시 항상)
    if dump_summary and config is not None:
        await _update_dump_history(sim_dir, test_name, dump_summary, effective_scopes)

    # ... 기존 실행 로직 ...
    result["dump_summary"] = dump_summary
    return result
```

**`_update_dump_history` 분리 함수:**
```python
async def _update_dump_history(
    sim_dir: str, test_name: str, dump_summary: dict, dump_scopes: dict
) -> None:
    """config의 dump_history를 갱신하고 저장."""
    config = await load_sim_config(sim_dir, force=True)
    if config is None:
        return
    history = config.setdefault("dump_history", {})
    history[test_name] = {
        "last_dump_summary": {k: v for k, v in dump_summary.items() if k != "scope_overrides"},
        "dump_scopes": dump_scopes,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    await save_sim_config(sim_dir, config)
```

---

## 8. `_run_batch_regression` — `dump_stats` 집계

```python
async def _run_batch_regression(..., dump_scopes=None, use_dump_history=False) -> dict:
    results = []
    for test in tests:
        r = await _run_batch_single(..., dump_scopes=dump_scopes, use_dump_history=use_dump_history)
        results.append(r)

    # dump_stats 집계 (boundary 모드에서만)
    dump_stats = None
    boundary_results = [r for r in results if r.get("dump_summary")]
    if boundary_results:
        per_test = {
            r["test_name"]: {
                "total": r["dump_summary"]["total_signals"],
                "top_boundary": r["dump_summary"]["top_boundary_count"],
                "block_count": sum(
                    1 for c in r["dump_summary"]["block_boundaries"].values() if c > 0
                ),
            }
            for r in boundary_results
        }
        totals = [v["total"] for v in per_test.values()]
        avg = sum(totals) / len(totals)
        max_test = max(per_test, key=lambda t: per_test[t]["total"])
        min_test = min(per_test, key=lambda t: per_test[t]["total"])
        suggestions = [
            f"{t} total={per_test[t]['total']} (max): dump_scopes로 heavy block skip 검토"
            for t in per_test if per_test[t]["total"] > avg * 2
        ]
        dump_stats = {
            "per_test": per_test,
            "max": {"test": max_test, "total": per_test[max_test]["total"]},
            "min": {"test": min_test, "total": per_test[min_test]["total"]},
            "suggestions": suggestions,
        }

    return {"results": results, "dump_stats": dump_stats}
```

---

## 9. `registry.py` — `save_sim_config` 추가

```python
async def save_sim_config(sim_dir: str, config: dict) -> None:
    """Save .mcp_sim_config.json and invalidate cache."""
    path = Path(sim_dir) / ".mcp_sim_config.json"
    content = json.dumps(config, indent=2, ensure_ascii=False)
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")
    # 캐시 무효화
    _config_cache.pop(sim_dir, None)
```

---

## 10. Phase 2 — Auto-detection (`sim_env_detection.py`)

> Phase 1 MVP 완료 후 구현. Phase 1과 독립적.

### 10.1 신규 함수 3개 추가 (`sim_env_detection.py`)

```python
def _parse_describe_output(scope: str, output: str) -> list[str]:
    """scope -describe -sort kind 출력에서 port 신호 추출."""
    ports = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("input ", "output ", "inout ")):
            name = stripped.split(".")[0].strip().split()[1]  # 두 번째 토큰 = 신호명
            ports.append(f"{scope}.{name}")
    return ports


async def _boundaries_from_tcl(
    bridge, top_scope: str, depth: int
) -> dict[str, list[str]]:
    """TCL scope -describe로 hierarchy port 추출 (sim_bridge_run 전용)."""
    ...  # 재귀 탐색


def _boundaries_from_json(
    json_path: Path, top_module: str, depth: int,
    block_filter: list[str] | None = None,
) -> dict[str, list[str]]:
    """Yosys JSON에서 module port 추출 (sim_batch_run lazy discovery 전용)."""
    ...
```

### 10.2 Flow A — `sim_bridge_run(auto_boundaries=True)`

```python
# tools/sim_lifecycle.py 내 sim_bridge_run 확장
if auto_boundaries:
    from xcelium_mcp.sim_env_detection import _boundaries_from_tcl
    base_mode = "gate" if "gate" in sim_mode else "rtl"
    strategy = get_dump_strategy(config or {}, sim_mode)
    depth = strategy.get("boundary_depth", 3)
    boundaries = await _boundaries_from_tcl(bridge, top_module or "top", depth)
    await config_action("set", "config",
                        f"dump_strategy.{base_mode}.block_boundaries",
                        json.dumps(boundaries))
```

### 10.3 Flow B — `sim_batch_run` lazy discovery

```python
# batch_runner.py _lazy_discover_boundaries()
async def _lazy_discover_boundaries(
    sim_dir: str, dump_strategy: dict, sim_mode: str
) -> dict[str, list[str]] | None:
    from xcelium_mcp.sim_env_detection import _boundaries_from_json
    from xcelium_mcp.registry import load_sim_config, save_sim_config
    config = await load_sim_config(sim_dir)
    base_mode = "gate" if "gate" in sim_mode else "rtl"
    json_rel = (config or {}).get("netlist_info", {}).get(base_mode, {}).get("boundary_json", "")
    if not json_rel:
        return None
    json_path = Path(sim_dir) / json_rel
    if not json_path.exists():
        return None
    boundaries = _boundaries_from_json(
        json_path, "top",
        depth=dump_strategy.get("boundary_depth", 3),
        block_filter=dump_strategy.get("block_filter"),
    )
    if dump_strategy.get("write_discovered_boundaries") and config:
        config.setdefault("dump_strategy", {}).setdefault(base_mode, {})
        config["dump_strategy"][base_mode]["block_boundaries"] = boundaries
        await save_sim_config(sim_dir, config)
    return boundaries
```

### 10.4 `sim_discover` 확장

```python
async def sim_discover(
    sim_dir: str = "",
    force: bool = False,
    top_module: str = "",
    boundary_depth: int = 3,   # v5.2 신규
) -> str:
    # ... 기존 로직 ...
    # boundary_depth를 config에 기록
    if config:
        base_mode = "rtl"  # discover는 mode-agnostic; rtl/gate 공통
        for mode in ("rtl", "gate"):
            if "dump_strategy" in config and mode in config["dump_strategy"]:
                config["dump_strategy"][mode]["boundary_depth"] = boundary_depth
        await save_sim_config(sim_dir, config)
    # netlist_info 유효성 검사 + Yosys 안내 출력
```

---

## 11. 의미 매트릭스 (opt-in / opt-out)

### opt-in 모델 (`default_block_policy: "skip"`, 기본값)

| dump_depth | dump_scopes | 결과 |
|------------|-------------|------|
| `"all"` | — | `probe -create top -depth all` |
| `"boundary"` | — | `top_boundary`만 (v5.1 동일) |
| `"boundary"` | `{X: "boundary"}` | `top_boundary + block_boundaries[X]` |
| `"boundary"` | `{X: "all"}` | `top_boundary + probe -create X -depth all` |
| `"boundary"` | `{"top.hw.u_ext.*": "all"}` | `top_boundary + probe -create top.hw.u_ext.* -depth all` (xmsim native glob) |
| `"boundary"` | `{"*": "boundary"}` | `top_boundary + 모든 block 경계` |

### opt-out 모델 (`default_block_policy: "boundary"`)

| dump_depth | dump_scopes | 결과 |
|------------|-------------|------|
| `"boundary"` | — | `top_boundary ∪ 전체 block 경계` |
| `"boundary"` | `{X: "skip"}` | 전체 경계 - `block_boundaries[X]` |
| `"boundary"` | `{"*": "skip"}` | `top_boundary`만 |

---

## 12. Security

기존 `sanitize_signal_name` 패턴을 `dump_scopes` key/value에 적용:

```python
# dump_scopes key: [A-Za-z0-9_.*]+ 허용 (glob 패턴 포함)
# dump_scopes value: "all" | "boundary" | "skip" 3종만 허용
# fnmatch는 OS shell 호출 없음 → injection 위험 없음
# glob "*"만 허용 ("?", "[", "]", shell metachar 차단은 validation에서)
```

---

## 13. 테스트 계획

### 13.1 Unit Tests (`tests/test_hierarchical_dump.py`)

| 케이스 | 설명 |
|--------|------|
| `test_depth_all_backward_compat` | dump_depth="all" → ("depth_all", None, None) |
| `test_boundary_no_block_boundaries` | block_boundaries 미정의 → ("signals", [...], None) — v5.1 동일 |
| `test_opt_out_all_blocks` | default_policy="boundary" → top + 전체 block 합집합 |
| `test_dump_scopes_all_override` | dump_scopes={X: "all"} → scope_probe 추가, X 경계 제거 |
| `test_dump_scopes_skip` | dump_scopes={X: "skip"} → X 경계 제거, count=0 |
| `test_dump_scopes_boundary_optin` | dump_scopes={X: "boundary"} → X만 추가 |
| `test_glob_subtree_all` | `"top.hw.u_ext.*": "all"` → pattern 그대로 scope_probe |
| `test_glob_subtree_skip` | `"top.hw.u_ext.*": "skip"` → matched 전체 제거 |
| `test_invalid_dump_scopes_value` | 잘못된 값 → ValueError |
| `test_dump_history_written` | dump_summary 반환 시 dump_history 갱신 확인 |
| `test_use_dump_history_applies_saved` | use_dump_history=True → 저장된 dump_scopes 적용 |

### 13.2 Integration Tests (cloud0)

```
Test 1: boundary opt-out (default_policy="boundary") → block 경계 신호 포함 확인
Test 2: dump_scopes={i2cSlave: "all"} → i2cSlave 내부 신호 dump 확인
Test 3: dump_scopes={u_ext_clk: "skip"} → clock block 경계 제외 확인
Test 4: dump_scopes={"top.hw.u_ext.u_ext_d_main.*": "skip"} → subtree 전체 제외
Test 5: dump_scopes={"*": "skip"} → top_boundary 28개만 (v5.1 동일 결과)
```

### 13.3 Regression

- v5.1 regression 21/21 PASS (NFR-01, NFR-02, NFR-03 확인)
- SHM 크기: `"boundary"` v5.2 / `"all"` ≤ 10% (NFR-04)

---

## 14. 구현 순서

### Session 1 — Phase 1 Core (tcl_preprocessing + 테스트)

1. `tcl_preprocessing.py`
   - `get_dump_strategy()` helper 추가
   - `_resolve_probe_signals()` 3-tuple 반환으로 확장 (backward compat 유지)
   - `_replace_probe_lines()` `hierarchical` 타입 처리 추가
   - `_preprocess_setup_tcl()` `dump_scopes`/`dump_strategy` 파라미터 추가 + `(path, dump_summary)` tuple 반환
2. `tests/test_hierarchical_dump.py` 신규 작성 — unit test 10 case
3. `pytest tests/test_hierarchical_dump.py -v` PASS 확인

### Session 2 — Phase 1 Wiring (batch + registry + tools)

4. `registry.py` — `save_sim_config()` 추가
5. `batch_runner.py`
   - `_run_batch_single`: `dump_scopes`/`use_dump_history` + `_preprocess_setup_tcl` tuple 반환 처리 + `_update_dump_history` 호출
   - `_run_batch_regression`: `dump_stats` 집계 + `suggestions`
6. `tools/batch.py` — `sim_batch_run`/`sim_regression` 파라미터 추가 + validation
7. regression 21/21 PASS 확인

### Session 3 — Phase 2 Auto-detection (Optional)

8. `sim_env_detection.py` — `_parse_describe_output`, `_boundaries_from_tcl`, `_boundaries_from_json`
9. `tools/sim_lifecycle.py` — `sim_bridge_run(auto_boundaries)`, `sim_discover(boundary_depth)`
10. `batch_runner.py` — `_lazy_discover_boundaries()` 추가
11. Flow A/B 각 1회 수동 검증

---

## 15. Open Questions

1. **v5.2 릴리스 단위**: Phase 1 MVP → v5.2.0, Phase 2 → v5.2.1 권장 (Plan §10.1)
2. **`BOUNDARY_SIGNALS` 상수**: config `top_boundary` 우선, fallback으로 유지. deprecated 마킹 여부는 v5.3에서 결정.
3. **`dump_scopes` key 정규화**: trailing slash 미지원 (validation에서 차단). 절대/상대 경로 미지원 — full scope path 필수.
4. **`_preprocess_setup_tcl` 반환값 변경**: `str → tuple[str, dict|None]` 기존 호출부 모두 업데이트 필요 (현재 batch_runner.py 단 1곳).
