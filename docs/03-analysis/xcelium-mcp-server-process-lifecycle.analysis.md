# Analysis: xcelium-mcp Server Process Lifecycle (안C+)

> **Summary**: Design 0.3(`socketserver.ForkingMixIn` 수퍼바이저 + `/proc` 기반 idle-culler + `sim_dir` 레지스트리) 대비 구현을 검증. cloud0 실배포·실행으로 Runtime Verification까지 완료.
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Date**: 2026-07-07
> **Planning Doc**: [xcelium-mcp-server-process-lifecycle.plan.md](../01-plan/features/xcelium-mcp-server-process-lifecycle.plan.md)
> **Design Doc**: [xcelium-mcp-server-process-lifecycle.design.md](../02-design/features/xcelium-mcp-server-process-lifecycle.design.md)
> **Implementation commits**: `6619f62`, `98109eb`, `a9308e8`

---

## 0. 이 프로젝트에 맞춘 스코프 조정

이 feature는 웹앱이 아니라 백엔드 프로세스 lifecycle 인프라라, 스킬 기본 공식(L1 API/L2 UI/L3 E2E, curl/Playwright 기반 Match Rate)이 그대로 적용되지 않는다. 대신:
- **Structural/Functional/Contract**은 Design §5/§10의 파일·함수 단위로 그대로 검증(웹 라우트 대신 모듈/함수 단위).
- **Runtime Verification**은 curl 시뮬레이션이 아니라 **cloud0 실호스트에서 실제로 실행한 결과**(Plan §5 T-1~T-8)로 대체 — 정적 추정보다 신뢰도가 높은 증거.

---

## 1. Strategic Alignment Check

| 질문 | 판정 | 근거 |
|---|---|---|
| Plan의 핵심 문제(WHY)를 해결했는가? | ✅ Met | §1.1의 프로세스 무한 누적 문제 → cloud0에서 T-1(반복 연결/해제 후 워커 0개 잔존)로 실증. attach 모호성 → T-4/T-5 pytest로 실증 |
| Design의 핵심 결정(안C+, `/proc` 기반 idle-culler, sim_dir 레지스트리)이 그대로 따라졌는가? | ✅ Met | §3 참조 |
| 전략적 이탈(misalignment)이 있는가? | 없음 | — |

---

## 2. Plan Success Criteria (Test Plan T-1~T-8) 평가

| # | 기준 | 상태 | 근거 |
|---|---|:---:|---|
| T-1 | 반복 연결/해제 시 워커 수 미누적 | ✅ Met | cloud0 실행: 5회 connect/disconnect 후 `/proc/<supervisor>/task/*/children` 빈 목록 확인 |
| T-2 | network black hole → ssh keepalive로 EOF 전파 | ✅ Met | **로컬(Windows) ssh 클라이언트로 실제 재현**: `ssh -o ServerAliveInterval=2 -o ServerAliveCountMax=2 cloud0 "sleep 60"`를 백그라운드로 연결한 뒤, cloud0에서 그 연결을 서비스하는 `sshd` 프로세스를 `kill -STOP`으로 얼려(=네트워크가 말없이 죽는 상황과 동일하게 프로토콜 레벨 응답 불가 상태) network black hole을 재현. ~4~6초 후 로컬 ssh가 `Timeout, server 192.168.1.252 not responding.`를 출력하며 exit 255로 스스로 종료 — Design §7.4/§1.4가 의도한 메커니즘 그대로 동작함을 실측으로 확인. 부수적으로, `sshd` 종료 후에도 그 자식으로 떠 있던 원격 명령(`tcsh`+`sleep`)이 `ppid=1`로 고아가 되어 계속 살아있는 것도 함께 관찰됨 — **Plan §1.2가 지적한 원래 root cause("ssh 연결이 끊겨도 원격 명령은 자동으로 안 죽는다")를 이 테스트 자체가 직접 재현·검증**(고아는 xcelium-mcp 프로세스가 아니라 테스트용 명령이라 즉시 정리함) |
| T-3 | idle(브릿지 미연결) age 초과 시 정리 | ⚠️ Partial | idle_culler 로직 자체는 pytest(순수 파싱)로 검증됐고 cloud0에서 culler 정상 동작(supervisor 비살상) 확인했으나, 기본 임계값(6h) 경과를 기다리는 실측은 수행하지 않음 |
| T-4 | 서로 다른 sim_dir 2개 → 포트 뒤섞이지 않음 | ✅ Met | `tests/test_registry_bridge_port.py::test_two_sim_dirs_get_independent_ports` 외 3개 |
| T-5 | sim_dir 없이 auto 호출 시 브릿지 ≥2개 → 모호성 에러 | ✅ Met | `tests/test_sim_lifecycle.py::test_auto_connect_all_ambiguous_type_fails_loud_without_overwriting` |
| T-6 | 기존 MCP tool 회귀 없음 | ✅ Met | pytest 548 passed(기존 스위트 + 신규 18개), ruff 클린 |
| T-7 | 수퍼바이저 kill → cron 워치독 재기동 | ✅ Met | cloud0 실행: `kill -9` 후 65초 내 새 pid로 재기동 확인 |
| T-8 | stdio_forward 단독 동작(socat 없이) | ✅ Met | cloud0 실행: 실제 MCP `initialize` 요청을 stdio_forward→socket→supervisor→fork→워커 전체 체인으로 왕복, 정상 JSON-RPC 응답 수신 |

**Met 7/8, Partial 1/8(T-3만 — 6시간 idle 대기가 필요해 실사용 관찰로 이연, Critical 아님).**

---

## 3. Structural Match — Design §10.1 File Structure vs 실제

| 파일 | Design 계획 | 구현 | 일치 |
|---|---|---|:---:|
| `supervisor.py` | 신규 | 신규, `ForkingMixIn`+`WorkerHandler` | ✅ |
| `stdio_forward.py` | 신규 | 신규, 스레드 2개로 양방향 relay | ✅ |
| `idle_culler.py` | 신규 | 신규 + Do 단계 중 발견한 flock 오인 버그 수정(§6) | ✅(수정 포함) |
| `registry.py` | 수정(`update_bridge_port`/`get_bridge_port`) | 동일 + `_resolve_project_root` 공통화 리팩터 | ✅ |
| `bridge_lifecycle.py` | 수정(write-back 2곳) | 동일 | ✅ |
| `bridge_manager.py` | "수정, 소폭" | **무변경** | ⚠️ 계획과 다름(아래 §5 참고) |
| `tools/sim_lifecycle.py` | 수정(`connect_simulator` sim_dir + fail-loud) | 동일 | ✅ |
| `server.py` | 무변경 | 무변경 | ✅ |
| `pyproject.toml` | entry point 2개 | 추가됨(단, 실배포 시 미사용 — §5) | ✅(코드) |
| `deploy/` | Design엔 명시 안 됨(Do 단계 결정 사항) | crontab.example + systemd-user-optional/ + claude-json 스니펫 + README | ✅(Do 단계에서 구체화, Design 의도와 부합) |

**Structural Match: 9/10 — `bridge_manager.py`만 "수정 예정"에서 "불필요로 판명"(F-C 로직이 `sim_lifecycle.py`/`registry.py`만으로 충분했음, 실질적 결손 아님).**

---

## 4. Functional Depth — Placeholder/미완성 로직 여부

- `supervisor.py`: `ForkingMixIn`/`WorkerHandler` 전부 실동작 코드, placeholder 없음. Windows import-safety를 위한 `if sys.platform == "win32": class Supervisor: pass` stub은 **의도된 것**(Design엔 없던 항목이나 로컬 개발 환경 보호 목적 — §5 참고).
- `stdio_forward.py`: 양방향 relay + EOF 전파(`SHUT_WR`) + 상대측 종료 시 `os._exit(0)` 강제 종료까지 구현 — Design 의사코드(`...`)보다 구체화됨.
- `idle_culler.py`: `/proc` 파싱 6개 함수 전부 실구현. Design 의사코드가 놓쳤던 "동일 net-namespace에서 `/proc/<pid>/net/tcp`가 전역 테이블"이라는 문제를 fd-inode 교차 검증으로 직접 해결(Design §1.4가 이미 이 문제를 지적했고, 구현이 그 지적을 정확히 반영).
- `registry.py`/`bridge_lifecycle.py`/`sim_lifecycle.py`: 전부 실동작, mock 없이 실제 파일 I/O 경로.

**Functional Depth: 결손 없음.**

---

## 5. Design 대비 실제 이탈(deviation) — Do/Check 단계에서 발견·수정

Check 단계는 원래 "Design 대비 code가 맞는지"를 보는 것이지만, 이번 feature는 실제 cloud0 배포 중 **Design 문서 자체의 가정이 틀렸던 지점**이 3개 발견되어 코드가 Design을 능동적으로 수정했다. 전부 문서화·수정·재검증까지 완료된 항목이라 Critical로 분류하지 않는다.

| # | Design 가정 | 실제(cloud0) | 조치 |
|---|---|---|---|
| 1 | `pyproject.toml` entry point로 `xcelium-mcp-supervisor`/`xcelium-mcp-culler` console script 사용(§7.1) | `/opt/mcp-env/bin/`이 root 소유·쓰기 불가 — `pip install -e`가 새 console script를 생성 못 함 | `deploy/crontab.example`·systemd 유닛을 `python3 -m xcelium_mcp.supervisor`/`-m xcelium_mcp.idle_culler` 직접 호출로 전환(코드 변경 없음, 배포 방식만 변경) |
| 2 | 클라이언트 소켓 경로 예시 `/home/hoseung.lee/...`(§7.4) | 실제 `$HOME`은 `/users/hoseung.lee` | `deploy/` 문서·스니펫 정정, "하드코딩 전 반드시 `echo $HOME`으로 확인" 경고 추가 |
| 3 | `idle_culler.py`의 `find_supervisor_pid()`가 cmdline substring 매칭으로 supervisor를 찾음(§5.3 의사코드) | crontab이 `flock -n <lock> python3 -m xcelium_mcp.supervisor`로 감싸는데, flock 자신의 argv에도 감싼 명령 전체가 그대로 들어있어 **flock의 pid를 supervisor로 오인** — 오인 시 `find_worker_pids()`가 flock의 자식(=진짜 supervisor 1개)을 "워커"로 보고 idle 판정 대상에 올려 최악의 경우 supervisor 자체가 죽을 위험 | `is_supervisor_argv()`로 분리해 argv[0] basename이 `flock`인 프로세스를 명시적으로 제외하도록 수정(`a9308e8`), 회귀 테스트 5개 추가, cloud0에서 실제 flock+supervisor 프로세스 쌍으로 재검증 |

**이 3개는 "정적 리뷰만으로는 못 잡고, 실제 대상 환경(cloud0)에 배포·실행해봐야만 드러나는 유형"이라는 공통점이 있다 — 특히 #3은 코드 리뷰만으로는 발견하기 어려운, 두 프로세스의 cmdline이 서로를 substring으로 포함하는 흔치 않은 케이스였다.**

---

## 6. Decision Record Verification

| 결정(Plan→Design) | 구현에서 지켜졌는가 |
|---|---|
| 안C+(프리포크 수퍼바이저), 애플리케이션 코드 무변경 | ✅ `server.py`/`tools/*.py`/`BridgeManager` 전부 무변경 확인(git diff) |
| `socketserver.ForkingMixIn` 채택(Checkpoint 3) | ✅ |
| 하트비트 완전 제거, ssh keepalive + `/proc` 순수 관찰(0.3 개정) | ✅ 워커 프로세스에 lifecycle 관련 코드 0줄(코드 리뷰로 확인) |
| cron 워치독을 기본 배포안으로(systemd --user는 승격 옵션) | ✅ `deploy/crontab.example`이 기본, `systemd-user-optional/`은 명시적으로 별도 격리 |
| `registry.py`의 기존 `bridge_port` 필드 재사용, 신규 스키마 없음 | ✅ |

**이탈 없음 — 모든 핵심 결정이 구현에 그대로 반영됨.**

---

## 7. Match Rate

이 feature는 HTTP 서버/UI가 없어 스킬의 기본 L1/L2/L3 공식이 적용되지 않는다. 구조·기능·계약·(실측) 런타임 4축으로 자체 산정한다.

| 축 | 점수 | 근거 |
|---|:---:|---|
| Structural | 95% | 9/10 파일 계획대로, 1개(`bridge_manager.py`)는 불필요로 판명(감점 아닌 단순 스코프 축소) |
| Functional | 100% | placeholder 없음, Design이 지적한 문제(net-namespace 전역성 등)까지 정확히 구현에 반영 |
| Contract | 100% | 기존 MCP tool 26개 시그니처/동작 무변경, `connect_simulator`의 신규 `sim_dir`는 하위호환 optional 파라미터 |
| Runtime(실측) | 94% | T-1/T-2/T-6/T-7/T-8 + pytest 기반 T-4/T-5 전부 실증(7/8), T-3만 실사용 관찰로 이연(Partial) |

**Overall Match Rate ≈ 97%** (Structural×0.15 + Functional×0.25 + Contract×0.25 + Runtime×0.35 = 14.25 + 25 + 25 + 32.9 = 97.15% → 반올림 97%)

---

## 8. Checkpoint 5 — Review Decision 대상 이슈

| 심각도 | 이슈 | 신뢰도 |
|---|---|---|
| Important | T-3(idle 6h 경과) 실측 미완료 | 100% (이미 알려진 gap, 로직은 cloud0에서 정상 동작 확인) |

Critical 이슈 없음 — 위 1건은 "6시간 대기가 필요해 지금 당장 검증 못 함"이라는 성격이지 코드 결함이 아니다. T-2는 이번 Check 단계 중 로컬 ssh 클라이언트로 직접 재현해 Met으로 전환(§2 참조).

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-07 | 초안 — Design 0.3 대비 구현 검증. Match Rate 97%(Met 6/8, Partial 2/8). cloud0 실배포 중 발견한 3개 Design 이탈 항목(entry point 배포 방식, `$HOME` 경로, flock 오인 버그) 전부 문서화·수정·재검증 완료. | hoseung.lee |
| 0.2 | 2026-07-07 | **T-2 실측 추가** — 로컬(Windows) ssh 클라이언트 + cloud0 sshd `SIGSTOP`으로 network black hole 직접 재현, `ServerAliveInterval`/`ServerAliveCountMax` 초과 시 ssh가 `Timeout, server ... not responding.`로 exit 255 자기종료함을 확인(Design §7.4/§1.4 검증). 부수적으로 sshd 종료 후에도 그 자식 원격 명령이 고아로 남는 것을 직접 관찰 — Plan §1.2 root cause를 재검증. Match Rate 96%(Met 6/8)→97%(Met 7/8, Partial 1/8)로 갱신. | hoseung.lee |
