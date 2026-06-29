# Design: xcelium-mcp v4.3 — 3-Tier Dump Strategy (dump_depth + dump_window)

> **Feature**: 기존 sim_mode/extra_args 체계에 dump_depth, dump_window 파라미터 추가 + sdf_file fallback/override 지원
>
> **Date**: 2026-04-06
> **Status**: Draft
> **Plan**: `docs/01-plan/features/xcelium-mcp-v4.3-dump-strategy.plan.md`
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | sim_batch_run이 sim_mode별 dump 전략을 제어하지 못함. Gate/AMS에서 RTL과 동일한 full dump → SHM 10~100배 폭증 |
| **Solution** | `dump_depth`(boundary/all) + `dump_window`(시간 구간) + `sdf_file`(override/fallback). 기존 resolve_sim_params 확장, _preprocess_setup_tcl에 probe 분기 추가 |
| **Function UX Effect** | `sim_batch_run(sim_mode="gate", dump_depth="boundary")` 한 줄로 최적 dump. 기존 호출은 변경 없음 |
| **Core Value** | Gate/AMS SHM을 1/10~1/50로 줄여 디버깅 워크플로우를 Gate/AMS까지 확장 |

---

## 1. 파일 구조 및 변경 범위

```
src/xcelium_mcp/
├── batch_runner.py         # [수정] resolve_sim_params에 dump_depth 추가
│                           #        _preprocess_setup_tcl에 dump_depth 분기
│                           #        _run_batch_single에 dump_window 시퀀싱
│                           #        _run_batch_regression에 dump_depth/dump_window 전파
│                           #        _handle_sdf_override() + _patch_tb_sdf_guard() 신규
├── sim_runner.py           # [수정] sim_discover에 $sdf_annotate 가드 분석
│                           #        _extract_top_module_from_script() 신규
│                           #        _analyze_sdf_annotate() + _parse_ifdef_around_sdf() 신규
│                           #        start_simulation에 sdf_file/sdf_corner 전달
├── tools/
│   ├── batch.py            # [수정] sim_batch_run/regression 스키마에 파라미터 추가
│   └── sim_lifecycle.py    # [수정] sim_start 스키마에 dump_depth 추가
├── server.py               # 변경 없음 (tool 등록은 tools/ 모듈에서)
├── tcl_bridge.py           # 변경 없음
├── csv_cache.py            # 변경 없음
└── checkpoint_manager.py   # 변경 없음

tests/
└── test_sim_batch.py       # [수정] dump_depth/dump_window/sdf_file 단위 테스트 추가
```

---

## 2. resolve_sim_params 확장

### 2.1 현재 (v4.2)

```python
def resolve_sim_params(runner, sim_mode, extra_args, timeout):
    # ...
    return {
        "test_args_format": test_args_format,
        "timeout": effective.get("timeout", timeout),
        "probe_strategy": effective.get("probe_strategy", "all"),
        "extra_args": all_extra,
    }
```

### 2.2 변경 (v4.3)

```python
def resolve_sim_params(
    runner: dict,
    sim_mode: str = "rtl",
    extra_args: str = "",
    timeout: int = 600,
    dump_depth: str = None,          # v4.3 신규
) -> dict:
    # 기존 로직 유지 ...

    # v4.3: dump_depth 결정
    if dump_depth is not None:
        effective_dump_depth = dump_depth
    else:
        effective_dump_depth = effective.get("dump_depth", "all")

    return {
        "test_args_format": test_args_format,
        "timeout": effective.get("timeout", timeout),
        "probe_strategy": effective.get("probe_strategy", "all"),
        "extra_args": all_extra,
        "dump_depth": effective_dump_depth,      # v4.3 신규
    }
```

### 2.3 mode_defaults 확장 (sim_discover)

```python
# sim_runner.py — sim_discover가 생성하는 mode_defaults
mode_defaults = {
    "common":   {"timeout": 120, "probe_strategy": "all",       "dump_depth": "all"},
    "gate":     {"timeout": 1800, "probe_strategy": "selective", "dump_depth": "boundary"},
    "ams_rtl":  {"timeout": 3600, "probe_strategy": "selective", "dump_depth": "boundary"},
    "ams_gate": {"timeout": 3600, "probe_strategy": "selective", "dump_depth": "boundary"},
}
```

---

## 3. probe 신호 결정 로직

### 3.1 _resolve_probe_signals()

`_preprocess_setup_tcl()` 내부에서 호출. dump_depth와 dump_signals의 합집합으로 최종 probe 신호를 결정한다.

```python
# batch_runner.py 신규 함수
BOUNDARY_SIGNALS = [
    "top.hw.i_mainClk", "top.hw.i_rst_n",
    "top.hw.i_scl", "top.hw.io_sda",
    "top.hw.i_pcmIn", "top.hw.i_pcmSync",
    "top.hw.o_askData", "top.hw.o_askDataInv",
    "top.hw.o_askRefClk", "top.hw.o_refClk", "top.hw.o_refClkInv",
    "top.hw.o_btCoilShort",
    "top.hw.i_backTel_p", "top.hw.i_backTel_n",
    "top.hw.o_backTel_pwr_en",
    "top.hw.i_led_ctrl_r", "top.hw.i_led_ctrl_g", "top.hw.i_led_ctrl_b",
    "top.hw.o_led_r", "top.hw.o_led_g", "top.hw.o_led_b",
    "top.hw.i_earpiece_det_n", "top.hw.i_rmClkNum",
    "top.hw.i_deep_slp_en", "top.hw.i_dyn_slp_en",
    "top.hw.o_sync_req", "top.hw.o_stim_trig", "top.hw.o_serial_tp_out",
]


def _resolve_probe_signals(
    dump_signals: list[str] | None,
    dump_depth: str,
) -> tuple[str, list[str] | None]:
    """Resolve final probe signal set.

    Returns:
        ("depth_all", None)              — probe -create top -depth all
        ("signals", [sig1, sig2, ...])   — probe -create {each} individually
    """
    if dump_depth == "all":
        return ("depth_all", None)

    # boundary 기반
    base = set(BOUNDARY_SIGNALS)
    if dump_signals:
        base |= set(dump_signals)

    return ("signals", sorted(base))
```

### 3.2 동작 매트릭스

| dump_depth | dump_signals | 결과 |
|-----------|-------------|------|
| `"all"` | 무관 | `probe -create top -depth all` |
| `"boundary"` | 미지정 | BOUNDARY_SIGNALS (28개) |
| `"boundary"` | `["r_loopState", ...]` | BOUNDARY_SIGNALS ∪ dump_signals |
| 미지정 (RTL) | `["sig1", ...]` | `probe -create top -depth all` (RTL 기본="all" → dump_signals 무시) |
| 미지정 (RTL) | 미지정 | `probe -create top -depth all` (기존 동작) |
| 미지정 (Gate) | 미지정 | BOUNDARY_SIGNALS (28개) |
| 미지정 (Gate) | `["r_loopState", ...]` | BOUNDARY_SIGNALS ∪ dump_signals |

> **Note**: `dump_depth="all"` 시 dump_signals는 무시된다. 합집합 동작은 `dump_depth="boundary"` 일 때만 적용된다.
> 이유: `depth all`은 probe -create top -depth all로 모든 신호를 포함하므로 개별 신호 추가가 무의미하다.

### 3.3 AI agent의 dump_depth 결정 가이드 (FR-06)

mode_defaults가 sim_mode별 안전 기본값을 제공하지만, AI agent는 `.ai/analysis/` 분석서를 참조하여 더 정확한 판단을 할 수 있다.

**판단 기준:**

| 기준 | 소스 | dump_depth 판단 |
|------|------|----------------|
| DUT 계층 깊이 | RTL 분석서 — 모듈 계층 구조 | top → 하위 3 depth 이상 → "boundary" |
| 총 신호 수 | RTL 분석서 — 모듈별 신호 수 합산 | ~1000개 이하 → "all", 초과 → "boundary" |
| TB DUT 참조 비율 | TB 분석서 — DUT 신호 참조 목록 | 전체 대비 참조 비율이 높으면 → "all" |

**AI 워크플로우:**

```
1. .ai/analysis/{module}.analysis.md 읽기 → 모듈 계층/신호 수 파악
2. .ai/analysis/tb_TOP0XX.analysis.md 읽기 → TB가 참조하는 DUT 신호 수 파악
3. block-level (단일 모듈, 신호 수 적음) → dump_depth="all" 명시
4. chip-level (ext_d_top 이상, 신호 수 많음) → dump_depth="boundary" (기본값 사용)
5. 판단 불확실 시 → mode_defaults 기본값 그대로 사용 (안전)
```

> Design은 이 판단을 자동화하지 않는다. mode_defaults가 안전 기본값을 제공하고,
> AI agent가 상황에 따라 dump_depth를 명시적으로 override하는 구조이다.

---

## 4. _preprocess_setup_tcl 변경

### 4.1 현재 동작

`_preprocess_setup_tcl()`은 SHM 이름에 test_name을 주입하는 역할만 한다.

### 4.2 변경: dump_depth + dump_window 반영

```python
async def _preprocess_setup_tcl(
    sim_dir: str, runner: dict, test_name: str, sim_mode: str = "",
    dump_depth: str = "all",             # v4.3 신규
    dump_signals: list[str] | None = None,  # v4.3 신규
    dump_window: dict | None = None,     # v4.3 신규
) -> str:
    """Preprocess setup_tcl: SHM naming + probe scope + dump window."""

    content = read_setup_tcl(runner, sim_dir)
    if not content:
        return ""

    # 기존: SHM stem replacement
    if "$env(TEST_NAME)" not in content:
        content = _replace_shm_stems(content, test_name)

    # v4.3: probe scope 조정 (범위 지정 제거 + dump_depth별 추가)
    probe_type, probe_signals = _resolve_probe_signals(dump_signals, dump_depth)
    content = _replace_probe_lines(content, probe_type, probe_signals)

    # v4.3: dump_window — Batch mode일 때 run 시퀀스 주입
    if dump_window:
        content = _inject_dump_window(content, dump_window)

    # 변경 없으면 빈 문자열 반환 (기존 setup_tcl 사용)
    # ... (임시 파일 저장 로직은 기존과 동일)
```

### 4.3 _replace_probe_lines()

기존 setup_tcl에서 범위 지정 probe(`-depth` 옵션)만 제거하고, 사용자가 추가한 특정 신호 probe는 유지한다.
dump_depth에 따라 새 probe 라인을 추가하되, 기존 특정 신호와 중복되지 않도록 한다.

```python
def _replace_probe_lines(content: str, probe_type: str, probe_signals: list[str] | None) -> str:
    """Adjust probe lines in setup tcl based on dump_depth.

    - 범위 지정 probe (-depth 옵션) → 제거
    - 특정 신호 probe (사용자 커스텀) → 유지
    - dump_depth에 따라 새 probe 추가 (기존 특정 신호와 중복 제거)
    """
    lines = content.splitlines()

    # 범위 지정 probe 제거, 특정 신호 probe 유지
    filtered = []
    existing_signals = set()
    for l in lines:
        if _re.match(r'\s*probe\s+-create\b', l):
            if '-depth' in l:
                continue    # 범위 지정 → 제거
            # 특정 신호 → 유지 + 기록
            sig_match = _re.search(r'probe\s+-create\s+(\S+)', l)
            if sig_match:
                existing_signals.add(sig_match.group(1))
            filtered.append(l)
        else:
            filtered.append(l)

    # dump_depth에 따라 추가 (기존 특정 신호와 중복 제거)
    if probe_type == "depth_all":
        new_probes = ["probe -create top -depth all -shm"]
    else:
        new_probes = [
            f"probe -create {sig} -shm"
            for sig in probe_signals
            if sig not in existing_signals   # 기존에 있으면 스킵
        ]

    # database -open 직후에 삽입
    result = []
    inserted = False
    for line in filtered:
        result.append(line)
        if not inserted and _re.match(r'\s*database\s+-open\b', line):
            result.extend(new_probes)
            inserted = True

    if not inserted:
        # database -open이 없으면 맨 앞에 삽입
        result = new_probes + result

    return "\n".join(result) + "\n"
```

**동작 예시:**

```tcl
# 기존 setup_gate.tcl
database -open dump.shm -shm
probe -create top -depth all -shm                    # ← 범위 지정 → 제거
probe -create top.hw.u_ext.debug_signal -shm         # ← 사용자 커스텀 → 유지
run

# dump_depth="boundary" 적용 후
database -open dump.shm -shm
probe -create top.hw.i_mainClk -shm                  # ← boundary 추가
probe -create top.hw.i_rst_n -shm                    # ← boundary 추가
# ... (나머지 boundary signals)
probe -create top.hw.u_ext.debug_signal -shm         # ← 사용자 커스텀 유지
run
```

### 4.4 _inject_dump_window()

Batch mode: setup tcl 끝에 probe on/off + run 시퀀스를 추가한다.

```python
def _inject_dump_window(content: str, dump_window: dict) -> str:
    """Inject probe on/off + run sequence for dump_window (Batch mode only).

    Replaces existing 'run' command with windowed probe on/off + run sequence.
    Setup tcl에 직접 주입되므로 bridge 통신 불필요.
    """
    start_ms = dump_window["start_ms"]
    end_ms = dump_window["end_ms"]
    duration_ms = end_ms - start_ms

    window_tcl = []
    # 기존 run 명령 제거
    lines = content.splitlines()
    filtered = [l for l in lines if not _re.match(r'\s*run\b', l)]

    window_tcl.append("probe -disable")
    if start_ms > 0:
        window_tcl.append(f"run {start_ms}ms")
    window_tcl.append("probe -enable")
    window_tcl.append(f"run {duration_ms}ms")
    window_tcl.append("probe -disable")
    window_tcl.append("run")  # $finish까지

    return "\n".join(filtered + window_tcl) + "\n"
```

---

## 5. _run_batch_single 변경

### 5.1 파라미터 추가

```python
async def _run_batch_single(
    sim_dir: str,
    test_name: str,
    runner: dict,
    rename_dump: bool = False,
    run_duration: str = "",
    timeout: int = 600,
    sim_mode: str = "rtl",
    extra_args: str = "",
    # === v4.3 신규 ===
    dump_depth: str = None,
    dump_signals: list[str] | None = None,
    dump_window: dict | None = None,
    sdf_file: str = "",
    sdf_corner: str = "max",
) -> str:
```

### 5.2 호출 체인 변경

```python
    # v4.3: dump_depth를 resolve_sim_params에 전달
    params = resolve_sim_params(runner, sim_mode, extra_args, timeout, dump_depth=dump_depth)
    effective_dump_depth = params["dump_depth"]

    # v4.3: SDF override 처리
    sdf_extra = ""
    if sdf_file:
        sdf_extra = await _handle_sdf_override(sim_dir, runner, sdf_file, sdf_corner)

    if sdf_extra:
        cmd = f"{cmd} {sdf_extra}"

    # v4.3: Batch vs Bridge 분기 (Gap 3)
    # _run_batch_single은 항상 Batch mode — setup tcl에 dump_window 주입
    # Bridge mode는 sim_start → connect_simulator 후 별도 _run_with_dump_window() 호출
    # 따라서 여기서는 dump_window를 setup tcl에 주입하는 Batch 경로만 처리
    preprocessed_tcl = await _preprocess_setup_tcl(
        sim_dir, runner, test_name, sim_mode,
        dump_depth=effective_dump_depth,
        dump_signals=dump_signals,
        dump_window=dump_window,       # Batch: setup tcl에 run/probe 시퀀스 주입
    )
```

### 5.3 Checkpoint + dump_depth 상호작용

`from_checkpoint`으로 복원 시, restore 후 Tcl 명령이 실행 가능하므로 probe 설정을 변경할 수 있다.

```python
    # from_checkpoint + dump_depth 지정 시:
    # - checkpoint restore 후 Tcl 명령 실행 가능
    # - 기존 probe를 disable → 새 dump_depth에 맞는 probe 추가 → enable
    # - SHM은 restore 시점부터 새 probe 설정으로 기록됨
    if from_checkpoint and dump_depth:
        # restore 후 probe 재설정 tcl 생성
        probe_type, probe_signals = _resolve_probe_signals(dump_signals, effective_dump_depth)
        probe_reset_tcl = _generate_probe_reset_tcl(probe_type, probe_signals)
        # MCP_INPUT_TCL로 주입: restore → probe reset → run
```

```python
def _generate_probe_reset_tcl(probe_type: str, probe_signals: list[str] | None) -> str:
    """Generate Tcl commands to reset probe configuration after checkpoint restore.

    Sequence: disable existing probes → add new probes → enable
    """
    lines = []
    lines.append("probe -disable")

    if probe_type == "depth_all":
        lines.append("probe -create top -depth all -shm")
    elif probe_signals:
        for sig in probe_signals:
            lines.append(f"probe -create {sig} -shm")

    lines.append("probe -enable")
    return "\n".join(lines) + "\n"
```

### 5.4 Bridge mode dump_window 호출 경로

Bridge mode에서 dump_window를 사용하는 경우, `sim_start` (`connect_simulator` 포함) 후 AI agent가 직접 호출:

```python
# Bridge mode 워크플로우 (AI agent 또는 tools/sim_lifecycle.py에서)
# 1. sim_start (bridge mode) — 시뮬레이터 시작 + connect 포함, setup tcl에 probe -disable
await sim_start(test_name="TOP015", mode="bridge", sim_mode="gate",
                dump_depth="boundary")

# 2. dump_window 시퀀싱 — Bridge 전용 (_run_with_dump_window 호출)
await _run_with_dump_window(bridges, dump_window={"start_ms": 50, "end_ms": 55})

# 3. 이후 추가 디버깅 가능 (get_signal_value, watch_signal, bisect 등)
# 4. 디버깅 완료 후 사용자가 shutdown_simulator() 명시 호출
```

---

## 6. SDF Override 설계

### 6.1 _extract_top_module_from_script()

```python
# sim_runner.py 신규 함수
async def _extract_top_module_from_script(sim_dir: str, runner: dict) -> str:
    """Extract top module name from run_sim script.

    Parses xmsim/xrun/irun invocation to find the last argument (= top module).
    e.g. "xmsim ... top" → "top", "xrun -f file.f tb_top" → "tb_top"

    Returns: top module name, or "" if not found.
    """
    script_name = runner.get("script", "")
    if not script_name:
        return ""

    content = await ssh_run(
        f"cat {sq(sim_dir + '/' + script_name)} 2>/dev/null", timeout=10
    )
    if not content:
        return ""

    # xmsim/xrun/irun 호출에서 마지막 인자 추출
    # 방법 1: backslash 연속줄을 합친 후 마지막 인자
    # 방법 2: 단일 줄에서 마지막 단어
    # 두 방법 모두 시도

    # backslash 연속줄 합치기
    joined = _re.sub(r'\\\s*\n\s*', ' ', content)

    match = _re.search(
        r'(?:eval\s+)?(?:xmsim|xrun|irun)\s+(.+)',
        joined, _re.MULTILINE,
    )
    if match:
        args_line = match.group(1).strip()
        # 마지막 인자 (옵션이 아닌 것) 추출 — $로 시작하는 변수나 -로 시작하는 옵션 제외
        tokens = args_line.split()
        # 뒤에서부터 옵션/변수가 아닌 첫 토큰 찾기
        for token in reversed(tokens):
            if not token.startswith("-") and not token.startswith("$") and _re.fullmatch(r'\w+', token):
                return token

    return ""
```

**제약 사항:**

- `eval xmsim ...` 패턴 지원 (venezia-t0의 run_sim이 eval 사용)
- backslash 연속줄 합치기 지원
- 스크립트가 변수로 모듈명을 구성하는 경우 (예: `xmsim $TOP_MODULE`) 는 추출 불가 → UserInputRequired로 전환
- 추출 실패 시 빈 문자열 반환 → 호출부에서 UserInputRequired 발생

### 6.2 _analyze_sdf_annotate() — sim_discover에서 호출

```python
# sim_runner.py 신규 함수
async def _analyze_sdf_annotate(sim_dir: str, runner: dict, top_module: str = "") -> dict:
    """Analyze $sdf_annotate in TB RTL and surrounding ifdef guards.

    Top module discovery:
      1. run_sim 스크립트에서 xmsim/xrun 호출의 마지막 인자로 추출
      2. 못 찾으면 top_module 파라미터 사용 (사용자 지정)
      3. 둘 다 없으면 UserInputRequired 발생 → AI가 사용자에게 질문
      4. 사용자 미응답 시 기본값 "top" 사용

    Returns:
        {
            "has_sdf_annotate": bool,
            "top_module": str,                # 실제 사용된 top module 이름
            "sdf_source_file": str,           # $sdf_annotate가 있는 소스 파일 경로
            "sdf_guard_define": str | None,   # $sdf_annotate 비활성화 가드 (자동 탐지)
            "sdf_entries": list[dict],        # scope별 SDF 엔트리 (아래 상세)
        }

    sdf_entries 각 항목:
        {
            "scope": str,          # $sdf_annotate 대상 scope (e.g. "`DTOP")
            "conditions": dict,    # 적용 조건 ifdef (e.g. {"BEST": True, "AMS": False})
            "file": str,           # SDF 파일 경로
        }
    conditions가 비어있으면 default (else 블록, 예: TYP)
    """
    # Step 1: top module 이름 결정
    effective_top = top_module
    if not effective_top:
        effective_top = await _extract_top_module_from_script(sim_dir, runner)
    if not effective_top:
        raise UserInputRequired(
            "Top module 이름을 자동으로 찾지 못했습니다.\n"
            "시뮬레이션의 top module 이름을 입력해주세요.\n"
            "  (예: top, tb_top, testbench)\n"
            "  입력하지 않으면 기본값 'top'을 사용합니다."
        )

    # Step 2: top module을 정의하는 파일 찾기
    top_v = await ssh_run(
        f"grep -rl 'module\\s\\+{effective_top}\\b' {sq(sim_dir)} "
        f"--include='*.v' --include='*.sv' 2>/dev/null | head -1",
        timeout=10,
    )
    if not top_v.strip():
        return {"has_sdf_annotate": False, "top_module": effective_top}

    # Step 3: top module + include/instance된 파일들에서 $sdf_annotate 검색
    # 먼저 top module 파일 자체를 확인
    content = await ssh_run(f"cat {sq(top_v.strip())}", timeout=10)

    # top module 파일에 없으면 include/instantiation을 추적하여 검색
    if "$sdf_annotate" not in content:
        top_v_path = top_v.strip()

        # 3a. top module 파일의 `include 목록 추출
        includes = await ssh_run(
            f"grep -oP '`include\\s+\"\\K[^\"]+' {sq(top_v_path)} 2>/dev/null",
            timeout=10,
        )

        # 3b. top module의 instantiation에서 모듈명 추출
        instances = await ssh_run(
            f"grep -oP '^\\s*(\\w+)\\s+\\w+\\s*\\(' {sq(top_v_path)} 2>/dev/null",
            timeout=10,
        )

        # 3c. include 파일 + instance 모듈 파일 수집
        search_files = []
        for inc in includes.strip().splitlines():
            if inc:
                search_files.append(f"{sim_dir}/*/{inc}")
        for line in instances.strip().splitlines():
            inst_module = line.strip().split()[0] if line.strip() else ""
            if inst_module:
                f = await ssh_run(
                    f"grep -rl 'module\\s\\+{inst_module}\\b' {sq(sim_dir)} "
                    f"--include='*.v' --include='*.sv' 2>/dev/null | head -1",
                    timeout=10,
                )
                if f.strip():
                    search_files.append(f.strip())

        # 3d. 수집된 파일들에서 $sdf_annotate + 주변 컨텍스트 검색
        if search_files:
            files_arg = " ".join(sq(f) for f in search_files)
            context_result = await ssh_run(
                f"grep -n -B10 -A2 '\\$sdf_annotate' {files_arg} 2>/dev/null",
                timeout=10,
            )
            if context_result.strip():
                content = context_result
            else:
                return {"has_sdf_annotate": False, "top_module": effective_top}
        else:
            return {"has_sdf_annotate": False, "top_module": effective_top}

    # $sdf_annotate 존재 확인
    if "$sdf_annotate" not in content:
        return {"has_sdf_annotate": False, "top_module": effective_top}

    # $sdf_annotate가 발견된 소스 파일 경로 기록
    # top module 파일 자체에 있었으면 top_v, include/instance 추적으로 찾았으면 해당 파일
    sdf_source = top_v.strip()
    if search_files:
        # grep -n 출력에서 파일 경로 추출 (첫 번째 매치)
        first_line = content.strip().splitlines()[0] if content.strip() else ""
        if ":" in first_line:
            sdf_source = first_line.split(":")[0]

    # ifdef 가드 + scope별 SDF 엔트리 파싱 (하드코딩 없음)
    result = {
        "has_sdf_annotate": True,
        "top_module": effective_top,
        "sdf_source_file": sdf_source,
    }
    parsed = _parse_ifdef_around_sdf(content)
    result.update(parsed)

    return result
```

```python
def _parse_ifdef_around_sdf(content: str) -> dict:
    """Parse ifdef structure around $sdf_annotate — no hardcoded define names.

    Builds structured sdf_entries: each $sdf_annotate call with its scope,
    conditions (ifdef stack at that point), and SDF file path.

    Returns:
        {
            "sdf_guard_define": str | None,    # define that disables all $sdf_annotate
            "sdf_entries": list[dict],          # scope별 SDF 엔트리
        }

    Each sdf_entry:
        {
            "scope": str,          # e.g. "`DTOP"
            "conditions": dict,    # e.g. {"BEST": True, "AMS": False}
            "file": str,           # e.g. "d_top.bst.sdf.gz"
        }
    """
    sdf_guard_define = None
    sdf_entries = []

    lines = content.splitlines()

    # ifdef 스택 추적
    # 각 frame: {"define": str, "type": "ifdef"|"ifndef", "branch": "if"|"else"}
    ifdef_stack = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # ifdef/ifndef 추적
        m = _re.match(r'`(ifdef|ifndef)\s+(\w+)', stripped)
        if m:
            ifdef_stack.append({"define": m.group(2), "type": m.group(1), "branch": "if"})
        elif stripped.startswith('`else'):
            if ifdef_stack:
                ifdef_stack[-1]["branch"] = "else"
        elif stripped.startswith('`endif'):
            if ifdef_stack:
                ifdef_stack.pop()

        # $sdf_annotate 발견 (주석 제외)
        if '$sdf_annotate' not in line or stripped.startswith('//'):
            continue

        # 1. guard 탐지 — $sdf_annotate가 `else 블록 안에 있으면 대응 `ifdef가 guard
        if sdf_guard_define is None:
            for frame in reversed(ifdef_stack):
                if frame["branch"] == "else" and frame["type"] == "ifdef":
                    sdf_guard_define = frame["define"]
                    break
                elif frame["branch"] == "if" and frame["type"] == "ifndef":
                    sdf_guard_define = frame["define"]
                    break

        # 2. 현재 ifdef 스택에서 conditions 구성
        conditions = {}
        for frame in ifdef_stack:
            if frame["define"] == sdf_guard_define:
                continue  # guard는 conditions에서 제외
            if frame["type"] == "ifdef":
                conditions[frame["define"]] = (frame["branch"] == "if")
            elif frame["type"] == "ifndef":
                conditions[frame["define"]] = (frame["branch"] == "else")

        # 3. $sdf_annotate 인자 추출: ("file", scope)
        sdf_match = _re.search(
            r'\$sdf_annotate\s*\(\s*"([^"]+)"\s*,\s*([^,)\s]+)',
            line,
        )
        if sdf_match:
            sdf_entries.append({
                "scope": sdf_match.group(2),
                "conditions": conditions,
                "file": sdf_match.group(1),
            })

    return {
        "sdf_guard_define": sdf_guard_define,
        "sdf_entries": sdf_entries,
    }
```

### 6.3 sim_discover 통합

```python
# sim_runner.py — discover_simulation() 시그니처 변경
async def discover_simulation(
    sim_dir: str = "",
    force: bool = False,
    top_module: str = "",        # v4.3: 사용자 지정 top module (없으면 자동 탐지)
) -> str:
    # ... 기존 탐지 로직 ...

    # v4.3: $sdf_annotate 가드 분석
    # UserInputRequired는 catch하지 않음 → MCP 응답으로 사용자에게 전달
    # 흐름:
    #   1. top_module 자동 탐지 실패 → raise UserInputRequired
    #   2. sim_discover 함수 전체 종료 → MCP 응답: "Top module 이름을 입력해주세요..."
    #   3. AI agent가 사용자에게 질문 전달
    #   4. 사용자 응답 후 AI agent가 sim_discover(top_module="응답") 재호출
    #   5. 사용자 미응답 시 AI agent가 sim_discover(top_module="top") 기본값으로 재호출
    sdf_info = await _analyze_sdf_annotate(sim_dir, runner_info, top_module)

    config = {
        # ... 기존 필드 ...
        "sdf_info": sdf_info,    # v4.3 신규
    }
```

### 6.4 _handle_sdf_override() — Tier 1b/1c 처리

```python
# batch_runner.py 신규 함수
async def _handle_sdf_override(
    sim_dir: str, runner: dict, sdf_file: str, sdf_corner: str,
) -> str:
    """Handle SDF override: disable TB $sdf_annotate + generate tfile.

    Returns extra_args string to append (e.g. "-define NODLY").
    Side effect: generates tfile at /tmp/mcp_sdf_tfile.
    """
    config = await load_sim_config(sim_dir)
    sdf_info = config.get("sdf_info", {})
    extra_defines = []

    # Step 1: TB $sdf_annotate 비활성화
    if sdf_info.get("has_sdf_annotate"):
        guard = sdf_info.get("sdf_guard_define")
        if guard:
            # Case A: 기존 가드 활용 (예: NODLY)
            extra_defines.append(f"-define {guard}")
        else:
            # Case B: 가드 없음 → TB RTL 패치
            await _patch_tb_sdf_guard(sim_dir, config)
            extra_defines.append("-define MCP_SDF_OVERRIDE")

    # Step 2: tfile 생성 — sdf_entries의 scope를 활용
    corner_map = {"min": "MINIMUM", "max": "MAXIMUM", "typ": "TYPICAL"}
    sdf_corner_upper = corner_map.get(sdf_corner, "MAXIMUM")

    user_tmp = await get_user_tmp_dir()
    tfile_path = f"{user_tmp}/mcp_sdf_tfile"

    # sdf_entries에서 unique scope 목록 추출
    sdf_entries = sdf_info.get("sdf_entries", [])
    scopes = sorted(set(e["scope"] for e in sdf_entries)) if sdf_entries else ["top"]

    # 각 scope에 대해 tfile 엔트리 생성
    tfile_lines = []
    for scope in scopes:
        tfile_lines.append(f'COMPILED_SDF_FILE "{sdf_file}"')
        tfile_lines.append(f'  SCOPE {scope}')
        tfile_lines.append(f'  {sdf_corner_upper}')
        tfile_lines.append(';')
    tfile_content = "\n".join(tfile_lines) + "\n"

    await ssh_run(f"cat > {sq(tfile_path)} << 'TFILE_EOF'\n{tfile_content}TFILE_EOF", timeout=10)

    # Step 3: elab_args 반환
    elab_extra = f"-delay_mode path -sdf_verbose -timescale 1ns/1fs -tfile {tfile_path}"
    all_extra = " ".join(extra_defines + [elab_extra])
    return all_extra
```

### 6.5 _patch_tb_sdf_guard() — Case B: TB RTL 패치

```python
async def _patch_tb_sdf_guard(sim_dir: str, config: dict):
    """Patch TB RTL to add `ifndef MCP_SDF_OVERRIDE guard around $sdf_annotate.

    Only called when sdf_guard_define is None (no existing guard).
    Creates backup before patching.
    $sdf_annotate가 포함된 파일은 sdf_info에서 참조 (하드코딩 없음).
    """
    sdf_info = config.get("sdf_info", {})

    # _analyze_sdf_annotate에서 이미 찾은 소스 파일 경로를 직접 참조 (grep 재검색 불필요)
    top_v = sdf_info.get("sdf_source_file", "")
    if not top_v:
        return

    # 백업 (실제 파일명 기반)
    user_tmp = await get_user_tmp_dir()
    filename = top_v.split("/")[-1]
    await ssh_run(f"cp {sq(top_v)} {user_tmp}/{filename}.bak.mcp_sdf", timeout=5)

    # 패치: $sdf_annotate 블록을 `ifndef MCP_SDF_OVERRIDE로 감싸기
    content = await ssh_run(f"cat {sq(top_v)}", timeout=10)

    # $sdf_annotate가 포함된 initial begin...end 블록 찾기
    patched = _re.sub(
        r'(\s*initial\s+begin\s*\n)(.*?\$sdf_annotate.*?\n)(.*?\s*end)',
        r'\1`ifndef MCP_SDF_OVERRIDE\n\2`endif\n\3',
        content,
        flags=_re.DOTALL,
    )

    if patched != content:
        await ssh_run(
            f"cat > {sq(top_v)} << 'PATCH_EOF'\n{patched}PATCH_EOF",
            timeout=10,
        )
```

**제약 사항:**

- regex는 `initial begin ... $sdf_annotate ... end` 패턴만 지원
- `always` 블록, `#0` 지연 구문, 중첩 begin...end 등은 미대응
- 대부분의 TB에서 `$sdf_annotate`는 `initial begin...end` 안에 위치하므로 실용적으로 충분
- 패치 실패 시 (regex 미매칭): 원본 유지, 경고 출력, 사용자에게 수동 가드 삽입 안내

---

## 7. Bridge mode dump_window 처리

Bridge mode에서 dump_window 사용 시, setup tcl에서 `probe -disable`로 시작하고 bridge 호출로 전환한다.

```python
# _run_with_dump_window() — Bridge mode 전용
# sim_runner.py 또는 tools/sim_lifecycle.py에서 호출

async def _run_with_dump_window(bridges, dump_window):
    """Bridge mode dump_window: 2 turnaround only."""
    start_ms = dump_window["start_ms"]
    end_ms = dump_window["end_ms"]
    duration_ms = end_ms - start_ms

    # settling — probe 이미 off (setup tcl에서 disable)
    await bridges.xmsim.send_command(f"run {start_ms}ms")

    # 관심 구간 — probe on (turnaround 1)
    await bridges.xmsim.send_command("probe -enable")
    await bridges.xmsim.send_command(f"run {duration_ms}ms")

    # 나머지 — probe off (turnaround 2)
    await bridges.xmsim.send_command("probe -disable")
    await bridges.xmsim.send_command("run")
```

---

## 8. MCP Tool 스키마 변경

### 8.1 sim_batch_run (tools/batch.py)

```python
async def sim_batch_run(
    self,
    test_name: str,
    dump_signals: list[str] | None = None,
    from_checkpoint: str = "",
    timeout: int = 600,
    sim_mode: str = "",
    extra_args: str = "",
    # === v4.3 신규 ===
    dump_depth: str = "",          # "boundary" | "all" | "" (auto)
    dump_window_start_ms: int = 0, # 0이면 미사용
    dump_window_end_ms: int = 0,   # 0이면 미사용
    sdf_file: str = "",
    sdf_corner: str = "max",       # "min" | "max" | "typ"
) -> str:
```

> **Note**: MCP tool 파라미터는 flat type만 지원하므로 `dump_window` dict 대신 `dump_window_start_ms`/`dump_window_end_ms` 2개의 int로 분리한다.

내부에서 검증 + 변환:

```python
    # dump_depth enum 검증
    VALID_DUMP_DEPTHS = {"", "boundary", "all"}
    if dump_depth not in VALID_DUMP_DEPTHS:
        return f"Invalid dump_depth='{dump_depth}'. Must be one of: {VALID_DUMP_DEPTHS}"

    # sdf_corner enum 검증
    VALID_SDF_CORNERS = {"min", "max", "typ"}
    if sdf_file and sdf_corner not in VALID_SDF_CORNERS:
        return f"Invalid sdf_corner='{sdf_corner}'. Must be one of: {VALID_SDF_CORNERS}"

    # dump_window 변환
    dump_window = None
    if dump_window_start_ms > 0 or dump_window_end_ms > 0:
        if dump_window_end_ms <= dump_window_start_ms:
            return f"Invalid dump_window: end_ms ({dump_window_end_ms}) must be > start_ms ({dump_window_start_ms})"
        dump_window = {"start_ms": dump_window_start_ms, "end_ms": dump_window_end_ms}
```

### 8.2 sim_batch_regression (tools/batch.py)

동일하게 dump_depth/dump_window_start_ms/dump_window_end_ms/sdf_file/sdf_corner 추가. 내부에서 `_run_batch_regression()`에 전파.

### 8.3 sim_start (tools/sim_lifecycle.py)

```python
async def sim_start(
    self,
    test_name: str,
    sim_dir: str = "",
    mode: str = "bridge",
    sim_mode: str = "",
    run_duration: str = "",
    timeout: int = 120,
    extra_args: str = "",
    # === v4.3 신규 ===
    dump_depth: str = "",          # "boundary" | "all" | "" (auto)
) -> str:
```

dump_depth만 추가 (dump_window는 Bridge mode에서 `_run_with_dump_window()` 별도 제어).

### 8.4 extra_args와 sim_mode 유효 조합

extra_args는 run_sim 스크립트에 패스스루되므로 xcelium-mcp가 검증하지 않는다. 단, 일반적인 오용을 방지하기 위해 다음 조합은 경고를 출력한다:

| sim_mode | extra_args | 동작 |
|----------|-----------|------|
| `"rtl"` | `"-max"`, `"-worst"` 등 | 경고: "corner options are typically for gate/ams mode" |
| `"gate"` | `"-ams"` | 경고: "AMS option in gate mode — use sim_mode='ams_gate' instead" |
| 미지정 | `"-gate post"` | 경고: "use sim_mode='gate' instead of extra_args" |

경고만 출력하고 실행은 차단하지 않는다 (사용자가 의도적으로 조합할 수 있으므로).

### 8.5 sim_discover (tools/sim_lifecycle.py)

```python
async def sim_discover(
    self,
    sim_dir: str = "",
    force: bool = False,
    top_module: str = "",        # v4.3: 사용자 지정 top module (없으면 자동 탐지 → 질문)
) -> str:
```

---

## 9. 테스트 계획

### 9.1 단위 테스트

| 테스트 | 입력 | 기대 결과 |
|--------|------|----------|
| `test_resolve_probe_signals_all` | dump_depth="all" | ("depth_all", None) |
| `test_resolve_probe_signals_boundary` | dump_depth="boundary", dump_signals=None | ("signals", BOUNDARY_SIGNALS) |
| `test_resolve_probe_signals_union` | dump_depth="boundary", dump_signals=["extra"] | ("signals", BOUNDARY + ["extra"]) |
| `test_resolve_sim_params_gate_default` | sim_mode="gate", dump_depth=None | dump_depth="boundary" |
| `test_resolve_sim_params_gate_override` | sim_mode="gate", dump_depth="all" | dump_depth="all" |
| `test_replace_probe_lines_all` | probe_type="depth_all" | probe -create top -depth all |
| `test_replace_probe_lines_signals` | probe_type="signals", ["sig1","sig2"] | 2개 probe -create 라인 |
| `test_inject_dump_window` | start_ms=50, end_ms=55 | probe disable/enable/run 시퀀스 |
| `test_analyze_sdf_annotate_nodly` | venezia-t0 top module file | sdf_guard_define="NODLY" |
| `test_handle_sdf_override_with_guard` | sdf_file + NODLY 가드 | -define NODLY + tfile |
| `test_handle_sdf_override_no_guard` | sdf_file + 가드 없음 | -define MCP_SDF_OVERRIDE + TB 패치 + tfile |
| `test_extract_top_module_from_script` | venezia-t0 run_sim | "top" |
| `test_extract_top_module_not_found` | 빈 스크립트 | "" → UserInputRequired |
| `test_extract_top_module_eval` | `eval xmsim ... top` | "top" |
| `test_extract_top_module_backslash` | `xmsim \\\n  ... top` | "top" |
| `test_dump_depth_invalid` | dump_depth="invalid" | 에러 메시지 반환 |
| `test_dump_window_invalid_range` | end_ms < start_ms | 에러 메시지 반환 |
| `test_checkpoint_with_dump_depth` | from_checkpoint + dump_depth | 경고 메시지 포함 |

### 9.2 통합 테스트 (cloud0)

| 테스트 | 방법 | 기대 결과 |
|--------|------|----------|
| 하위 호환 | 기존 21개 regression (dump_depth 미지정) | 전수 PASS |
| Gate boundary | sim_mode="gate", dump_depth="boundary", TOP015 | SHM 90%+ 절감 |
| Gate all (block) | sim_mode="gate", dump_depth="all", TOP015 | 기존과 동일 SHM |
| dump_window Batch | dump_window, TOP015 | (1) SHM 파일 크기가 full dump 대비 감소, (2) window 외 시간 범위 CSV 추출 시 데이터 없음, (3) window 내 시간 범위에서는 정상 데이터 확인 |
| sdf_file override | sdf_file 지정 + NODLY 가드 | Gate sim PASS |

---

## 10. 구현 순서

```
Phase 1: dump_depth + probe setup (1.5h)
  1. resolve_sim_params에 dump_depth 추가
  2. _resolve_probe_signals() 신규
  3. _replace_probe_lines() 신규
  4. _preprocess_setup_tcl에 dump_depth 전달
  5. mode_defaults에 dump_depth 기본값 추가
  6. sim_batch_regression 전파
  → 검증: 기존 regression PASS + Gate boundary probe 확인

Phase 2: dump_window (1.5h)
  1. _inject_dump_window() 신규
  2. _preprocess_setup_tcl에 dump_window 전달
  3. _run_with_dump_window() Bridge mode 전용
  → 검증: dump_window 후 CSV로 window 외 데이터 없음 확인

Phase 3: SDF override (1h)
  1. _analyze_sdf_annotate() 신규
  2. sim_discover에 sdf_info 추가
  3. _handle_sdf_override() 신규
  4. _patch_tb_sdf_guard() 신규
  → 검증: sdf_file 지정 Gate sim PASS

Phase 4: 스키마 + 테스트 (1h)
  1. tools/batch.py 파라미터 추가
  2. tools/sim_lifecycle.py dump_depth 추가
  3. 단위 테스트 17건
  4. 통합 테스트 5건
  → 검증: 전체 regression PASS
```

---

## 11. 향후 개선 사항

### sim_start(mode="batch") ↔ sim_batch_run 파라미터 통합

현재 `sim_start(mode="batch")`와 `sim_batch_run`은 같은 `_run_batch_single()`을 호출하지만,
`sim_batch_run`만 고급 파라미터를 지원한다:

| 파라미터 | sim_start | sim_batch_run |
|---------|:---------:|:------------:|
| from_checkpoint | ❌ | ✅ |
| probe_signals | ❌ | ✅ |
| shm_path | ❌ | ✅ |
| rename_dump | ❌ | ✅ |
| dump_signals | ❌ | ✅ |
| dump_depth (v4.3) | ✅ | ✅ |
| dump_window (v4.3) | ❌ | ✅ |
| sdf_file (v4.3) | ❌ | ✅ |

**원래 v4 Plan 의도**: `sim_start(mode="batch")`가 `sim_batch_run`에 완전히 위임.
**현재 상태**: `sim_start`는 `start_simulation()` 경유, `sim_batch_run`은 직접 `_run_batch_single()` 호출. 파라미터 불일치.

**향후 통합 방안:**

1. `sim_start(mode="batch")`에 `sim_batch_run`의 모든 파라미터 추가
2. 또는 `sim_start(mode="batch")` 내부에서 `sim_batch_run`을 직접 호출하도록 변경
3. v4.3에서 추가되는 dump_depth/dump_window/sdf_file도 함께 통합
4. timeout 기본값도 통일 (현재 sim_start=120, sim_batch_run=600)

> v4.3 scope에서는 현행 유지. sim_start에는 dump_depth만 추가하고,
> dump_window/sdf_file은 sim_batch_run에만 추가한다.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-06 | 초안 — Plan 기반 상세 설계. resolve_sim_params 확장, probe 분기, dump_window Batch/Bridge, SDF override 3-case, MCP 스키마 flat 파라미터 | hoseung.lee |
| 0.2 | 2026-04-06 | Gap detection 9건 반영: (1) checkpoint+dump_depth 상호작용 §5.3, (2) enum 검증 §8.1, (3) Bridge vs Batch 분기 §5.2/5.4, (4) AI dump_depth 판단 가이드 §3.3, (5) SHM 검증 방법 보강 §9.2, (6) extra_args 유효 조합 경고 §8.4, (7) TB 패치 regex 제약 문서화 §6.5, (8) top module regex 보강+제약 §6.1, (9) dump_depth="all" 시 dump_signals 무시 명시 §3.2 | hoseung.lee |
