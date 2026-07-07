# xcelium-mcp-server-process-lifecycle Completion Report

> **Status**: Complete
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Completion Date**: 2026-07-07
> **PDCA Cycle**: Plan → Design(0.1→0.3) → Do(module-1~4) → Check(Match Rate 97%)

---

## Executive Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | xcelium-mcp 프로세스 lifecycle 재설계(안C+) + attach 모호성 해소(F-C) |
| Start Date | 2026-07-06 (venezia-fpga 세션에서 cloud0 프로세스 누적 발견) |
| End Date | 2026-07-07 |
| Duration | 2일(설계 검토·실측이 대부분, 구현 자체는 반나절) |

### 1.2 Results Summary

```
┌─────────────────────────────────────────────┐
│  Match Rate: 97%                             │
├─────────────────────────────────────────────┤
│  ✅ Met:          7 / 8  Test Plan 항목       │
│  ⚠️  Partial:      1 / 8  (T-3, 6h 대기 필요)  │
│  ❌ Critical Gap:  0                          │
└─────────────────────────────────────────────┘
```

### 1.3 Value Delivered

| Perspective | Content |
|-------------|---------|
| **Problem** | xcelium-mcp가 stdio 1:1 콜드 spawn 모델이라 세션 재연결마다 cloud0에 프로세스가 무한 누적되고, 여러 sim_dir을 동시에 다루면 어느 브릿지에 붙을지 모호했다. |
| **Solution** | `socketserver.ForkingMixIn` 기반 프리포크 수퍼바이저(안C+) — 애플리케이션 코드 무변경 — + 클라이언트 SSH keepalive + `/proc` 순수 관찰 기반 idle-culler(신규 스레드/파일 없음) + `sim_dir` 키 기반 브릿지 레지스트리(F-C). |
| **Function/UX Effect** | 실측 확인: 5회 반복 연결/해제 후 워커 0개 잔존, `kill -9`로 수퍼바이저를 죽여도 cron이 65초 내 재기동, 네트워크가 말없이 끊겨도 ~4~6초(운영값 90초) 내 정리, MCP tool 26개 시그니처·동작 완전히 동일. |
| **Core Value** | 사용자 수 × 재접속 횟수에 비례해 무한 증가하던 리소스 위험을 구조적으로 제거했고, 이 과정에서 root/sudo/systemd 없이도(cloud0 실제 제약에 맞춰) 완결되는 배포 경로를 확보했다. |

---

## 1.4 Success Criteria Final Status

> Plan §5 Test Plan(T-1~T-8)을 Success Criteria로 사용(이 프로젝트의 Plan은 표준 SC 표 대신 Test Plan 형식).

| # | Criteria | Status | Evidence |
|---|---------|:------:|----------|
| T-1 | 반복 연결/해제 시 워커 미누적 | ✅ Met | cloud0 실행 — 5회 후 `/proc/<supervisor>/task/*/children` 빈 목록 |
| T-2 | network black hole → ssh keepalive로 정리 | ✅ Met | 로컬 ssh + cloud0 `sshd` `SIGSTOP`으로 직접 재현, `exit 255` 자기종료 확인 |
| T-3 | idle(6h) 워커 정리 | ⚠️ Partial | 로직/파싱은 pytest+cloud0로 검증했으나 6h 실측 대기는 이연 |
| T-4 | 서로 다른 sim_dir 2개 → 포트 안 섞임 | ✅ Met | `tests/test_registry_bridge_port.py` 4건 |
| T-5 | auto 스캔 시 브릿지 ≥2개 → 모호성 에러 | ✅ Met | `tests/test_sim_lifecycle.py::test_auto_connect_all_ambiguous_type_fails_loud_without_overwriting` |
| T-6 | 기존 MCP tool 회귀 없음 | ✅ Met | pytest 548 passed, ruff 클린 |
| T-7 | 수퍼바이저 kill → cron 재기동 | ✅ Met | cloud0 `kill -9` 후 65초 내 재기동 확인 |
| T-8 | stdio_forward 단독 동작(socat 없이) | ✅ Met | cloud0에서 실제 MCP `initialize` 왕복 성공 |

**Success Rate**: 7/8 criteria met (87.5%, Design 문서 기준 Match Rate는 가중 평균으로 97%)

## 1.5 Decision Record Summary

| Source | Decision | Followed? | Outcome |
|--------|----------|:---------:|---------|
| [Plan] | 안C+(프리포크 수퍼바이저) 채택, 안A/B(완전 세션 격리) 기각 | ✅ | `server.py`/`tools/*.py`/`BridgeManager` 전부 무변경으로 구현 완료 |
| [Design 0.1] | `socketserver.ForkingMixIn`(표준 라이브러리) 채택 | ✅ | zombie reap·워커 상한을 표준 라이브러리에 위임, 수제 구현 회피 |
| [Design 0.2] | cloud0 실측 후 systemd --user/socat → cron 워치독/순수 stdlib 포워더로 전환 | ✅ | root 없이 배포 가능함을 실제로 확인·적용 |
| [Design 0.3] | 하트비트 파일 완전 제거 → ssh keepalive + `/proc` 순수 관찰 | ✅ | 워커 프로세스에 lifecycle 코드 0줄 유지 |
| [Do] | F-C(attach 모호성)를 `bridge_manager.py` 수정 없이 `registry.py`+`sim_lifecycle.py`만으로 해결 | ✅(계획보다 축소) | `bridge_manager.py` 무변경으로 판명 — Design 계획 대비 스코프 축소, 결손 아님 |

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [xcelium-mcp-server-process-lifecycle.plan.md](../01-plan/features/xcelium-mcp-server-process-lifecycle.plan.md) | ✅ Finalized (v0.2) |
| Design | [xcelium-mcp-server-process-lifecycle.design.md](../02-design/features/xcelium-mcp-server-process-lifecycle.design.md) | ✅ Finalized (v0.3) |
| Check | [xcelium-mcp-server-process-lifecycle.analysis.md](../03-analysis/xcelium-mcp-server-process-lifecycle.analysis.md) | ✅ Complete (v0.2, Match Rate 97%) |
| Report | Current document | ✅ Complete |

---

## 3. Completed Items

### 3.1 Functional Requirements

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| F-A | 프리포크 수퍼바이저(안C+) | ✅ Complete | `supervisor.py`, cloud0 실배포·검증 완료 |
| F-B | idle-culler(`/proc` 순수 관찰) | ✅ Complete | `idle_culler.py`, 배포 후 발견한 flock 오인 버그까지 수정 완료 |
| F-C | attach 모호성 해소(sim_dir 레지스트리) | ✅ Complete | `registry.py`/`bridge_lifecycle.py`/`tools/sim_lifecycle.py` |
| 부수 | socat 대체 포워더 | ✅ Complete | `stdio_forward.py`, 실제 MCP 프로토콜 왕복 검증 |
| 부수 | 배포 문서화 | ✅ Complete | `deploy/`(crontab.example, systemd-user-optional/, claude-json 스니펫, README) |

### 3.2 Non-Functional Requirements

| Item | Target | Achieved | Status |
|------|--------|----------|--------|
| 기존 테스트 회귀 없음 | 472 passed 유지 | 548 passed(472+신규 76... 실제로는 신규 18개 테스트 파일/케이스, 총 548) | ✅ |
| root/sudo 불필요 배포 | 필수 요구사항(cloud0 제약) | cron 기반, 전 과정 root 불필요 확인 | ✅ |
| 애플리케이션 코드 무변경 | `server.py`/`tools/*.py` 핵심 로직 유지 | git diff로 확인, `server.py` 완전 무변경 | ✅ |
| 소켓 권한 | owner-only | `srw-------`(0600) cloud0 실측 확인 | ✅ |

### 3.3 Deliverables

| Deliverable | Location | Status |
|-------------|----------|--------|
| 수퍼바이저 | `src/xcelium_mcp/supervisor.py` | ✅ |
| 포워더 | `src/xcelium_mcp/stdio_forward.py` | ✅ |
| idle-culler | `src/xcelium_mcp/idle_culler.py` | ✅ |
| F-C 레지스트리 확장 | `src/xcelium_mcp/registry.py`, `bridge_lifecycle.py`, `tools/sim_lifecycle.py` | ✅ |
| 테스트 | `tests/test_registry_bridge_port.py`, `tests/test_idle_culler.py`, `tests/test_sim_lifecycle.py`(확장) | ✅ |
| 배포 문서/설정 | `deploy/` | ✅ |
| PDCA 문서 | `docs/01-plan`, `docs/02-design`, `docs/03-analysis`, 본 문서 | ✅ |

---

## 4. Incomplete Items

### 4.1 Carried Over (실사용 관찰 대기)

| Item | Reason | Priority | Estimated Effort |
|------|--------|----------|-------------------|
| T-3 실측(6h idle 후 culler 정리 확인) | 대기 시간이 길어 이번 세션에서 인위적 재현 안 함 — 코드/파싱 로직은 이미 검증됨 | Low | 관찰만 필요(추가 구현 없음) |

### 4.2 Cancelled/On Hold Items

| Item | Reason | Alternative |
|------|--------|-------------|
| `bridge_manager.py` 수정 | Design 계획에 있었으나 실제로 불필요로 판명 | `registry.py`+`sim_lifecycle.py`만으로 F-C 완결 |
| systemd --user 승격 배포 | admin이 `loginctl enable-linger`를 실행해야 함(root/인터랙티브 세션 필요) | 현재는 cron 워치독으로 완전히 대체, 승격은 Design §7.3에 문서화만 해둠(필요 시점에 admin이 결정) |

---

## 5. Quality Metrics

### 5.1 Final Analysis Results

| Metric | Target | Final | Change |
|--------|--------|-------|--------|
| Match Rate | 90% | 97% | +7%p |
| 기존 pytest 스위트 | 472 passed 유지 | 548 passed(472 + 신규) | +76 |
| ruff 오류 | 0(변경 파일 기준) | 0 | ✅ |
| 실배포 중 발견된 버그 | — | 3건(entry point 배포 방식, `$HOME` 경로, flock 오인) | 전부 수정·재검증 완료 |

### 5.2 Resolved Issues

| Issue | Resolution | Result |
|-------|------------|--------|
| 워커 하트비트가 idle-culler의 "활동 없음 N시간" 판정을 구조적으로 무력화(0.2 설계 결함) | 워커 계측 완전 제거, `/proc` 순수 관찰로 대체 | ✅ Resolved(Design 0.3) |
| `pyproject.toml` entry point가 root 소유 디렉토리에 설치 불가 | `python3 -m` 직접 호출로 배포 방식 전환 | ✅ Resolved |
| 클라이언트 소켓 경로가 실제 `$HOME`(`/users/`)과 다름 | 배포 문서·스니펫 정정 + "먼저 `echo $HOME` 확인" 경고 추가 | ✅ Resolved |
| idle_culler가 `flock` 래퍼를 supervisor로 오인(최악의 경우 supervisor 오살상 위험) | argv 파싱 + `flock` basename 명시적 제외(`is_supervisor_argv`) | ✅ Resolved + 회귀 테스트 5건 추가 |

---

## 6. Lessons Learned & Retrospective

### 6.1 What Went Well (Keep)

- **실측 우선 설계**: "Linux면 될 것"이라는 가정 대신 cloud0에 직접 SSH로 접속해 `systemd`/`sudo`/`socat`/`$HOME`을 매번 확인한 덕분에, 배포 단계에서 막혔을 문제(root 권한, 잘못된 경로)를 설계 단계에서 미리 제거할 수 있었다.
- **상용 사례 리서치를 설계 근거로 사용**: JupyterHub/PgBouncer/Gunicorn 등 구조적으로 유사한 선례를 조사해 "프로세스당 자원 1개 고정이 stateful 워크로드의 정석"이라는 근거를 확보 — 임의 설계가 아니라 검증된 패턴 위에 올라감.
- **사용자 리뷰가 실제 설계 결함을 잡음**: "하트비트 touch가 낭비 아니냐"는 지적이 단순 최적화가 아니라 idle-culler의 목적 자체를 무력화하는 버그로 이어졌음을 재검토 중 발견 — 표면적 지적을 가볍게 넘기지 않고 근본 원인까지 추적한 것이 유효했다.
- **실배포 후에도 계속 검증**: 코드가 "완료"된 뒤에도 cloud0에 실제로 crontab을 걸고 `kill -9`/`SIGSTOP`으로 직접 장애를 주입해 T-1/T-2/T-7/T-8을 실증 — 이 과정에서 flock 오인 버그를 찾아냈다(정적 리뷰만으로는 못 잡았을 버그).

### 6.2 What Needs Improvement (Problem)

- Design 0.1 초안이 cloud0 실측 없이 "systemd --user + socat"을 가정해서, 이후 두 차례(0.2, 배포 후 추가 수정)에 걸쳐 정정이 필요했다 — 처음부터 대상 호스트를 실측했다면 이 왕복을 줄일 수 있었다.
- `find_supervisor_pid()`의 flock 오인 버그는 Design 의사코드 단계에서도 예측 가능했던 문제(cron이 어떻게 감쌀지는 §7.2에서 이미 결정돼 있었음)인데 Do 단계 코드 작성 시점엔 놓쳤다 — 배포 방식이 확정된 직후 "그 배포 방식이 이 로직에 어떤 영향을 주는지"를 한 번 더 점검하는 습관이 필요.

### 6.3 What to Try Next (Try)

- 다음 유사 feature(예: ssh-mcp의 동일한 프로세스 lifecycle 문제)에서는, Design 단계에 들어가기 **전에** 대상 호스트 실측(권한/설치된 도구/실제 경로)을 먼저 하고 그 결과를 Design 초안에 바로 반영 — 이번처럼 사후 정정 라운드를 줄인다.
- idle-culler류 로직처럼 "배포 래퍼(cron/flock/systemd)가 프로세스 트리 모양에 영향을 주는" 코드는, 실제 배포 스크립트를 먼저 확정한 뒤 그 트리 모양을 가정해 코드를 작성하는 순서로 진행.

---

## 7. Process Improvement Suggestions

### 7.1 PDCA Process

| Phase | Current | Improvement Suggestion |
|-------|---------|------------------------|
| Design | 대상 환경 가정 기반 초안 → 사후 실측 정정(0.1→0.2) | Design 착수 전에 대상 호스트 실측을 먼저 수행하는 체크리스트화 |
| Do | 배포 스크립트가 코드보다 늦게 확정됨 | 배포 방식(cron/systemd wrapper 등)을 코드 작성 전에 먼저 확정하고, 그 wrapper가 만드는 프로세스 트리를 가정해 코드 작성 |
| Check | 시간이 오래 걸리는 검증(T-3)은 자연스럽게 이연 | 장시간 검증 항목은 Plan 단계에서부터 "즉시 검증 가능/실사용 관찰 필요"로 미리 분류해 Check 단계의 기대치를 명확히 함 |

### 7.2 Tools/Environment

| Area | Improvement Suggestion | Expected Benefit |
|------|------------------------|------------------|
| cloud0 실측 | SSH 직접 접속 기반 사실 확인을 Design/Do 표준 절차로 문서화(체크리스트) | 이번처럼 가정 기반 설계→재작업 왕복 감소 |
| ssh-mcp | 이번 feature와 동일한 구조적 문제(프로세스 누적)가 있음이 별도로 확인됨(`docs/01-plan`에 별도 Plan 등록 완료) | 다음 PDCA 사이클로 재사용 가능한 방법론 확보 |

---

## 8. Next Steps

### 8.1 Immediate

- [x] cloud0 배포 완료(crontab 등록, 클라이언트 설정 교체 가이드 제공)
- [ ] T-3(6h idle 실측)을 실사용 중 자연스럽게 관찰 — 별도 조치 불필요, 이상 발견 시 재조사
- [ ] 사용자가 실제 `~/.claude.json`을 `deploy/claude-json-mcpServers-snippet.json` 기준으로 교체(직접 실행 필요 — 로컬 설정 변경은 이 세션 범위 밖)

### 8.2 Next PDCA Cycle

| Item | Priority | Expected Start |
|------|----------|-----------------|
| ssh-mcp-process-lifecycle(동일 문제, `ssh_agent.py`) | Medium | Plan 완료(`Todoc/fpga/ssh-mcp/docs/01-plan/`), Design 단계부터 별도 세션에서 재개 |

---

## 9. Changelog

### v1.0.0 (2026-07-07)

**Added:**
- `src/xcelium_mcp/supervisor.py` — `ForkingMixIn` 기반 프리포크 수퍼바이저
- `src/xcelium_mcp/stdio_forward.py` — socat 대체 순수 stdlib 포워더
- `src/xcelium_mcp/idle_culler.py` — `/proc` 기반 idle 워커 정리
- `registry.py`의 `update_bridge_port`/`get_bridge_port`
- `deploy/`(crontab.example, systemd-user-optional/, claude-json 스니펫, README)

**Changed:**
- `bridge_lifecycle.py`: 연결 성공 시 실제 런타임 포트를 레지스트리에 write-back
- `tools/sim_lifecycle.py`: `connect_simulator`에 `sim_dir` 파라미터 추가, `_auto_connect_all` fail-loud화
- `pyproject.toml`: entry point 2개 추가(실배포는 `python3 -m` 직접 호출로 대체)

**Fixed:**
- idle_culler가 `flock` 래퍼 프로세스를 supervisor로 오인하던 버그(최악의 경우 supervisor 오살상 위험)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-07-07 | 완료 보고서 작성 — Match Rate 97%, Critical 이슈 0건, T-3만 실사용 관찰로 이연. | hoseung.lee |
