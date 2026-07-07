# xcelium-mcp-server-process-lifecycle Plan

> **Feature**: MCP 연결(stdio-over-SSH)마다 풀서버 프로세스가 새로 뜨고 정리되지 않아 cloud0에 프로세스가
> 무한 누적되는 구조적 문제 — 사용자 증가 시 더 심해짐. 부수적으로 발견된 `bridge_port` host-global 충돌
> 위험도 함께 다룬다.
>
> **Date**: 2026-07-06 (초안) / 2026-07-07 (아키텍처 검토·결정)
> **Status**: Draft — 데몬 구현 방식(안C+) 결정 완료, Design 단계 진입 전
> **Found in**: venezia-fpga 세션, cloud0에서 `xcelium-mcp` 프로세스 3쌍이 동시에 떠 있는 것을 발견(11:08/13:02/14:40 spawn)하고 원인 조사 중 구조적 문제로 확인

---

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | xcelium-mcp는 `mcp.run(transport="stdio")`(`server.py:74`)로 동작하는 stdio 1:1 프로세스 모델이라, 클라이언트가 `ssh cloud0 /opt/mcp-env/bin/xcelium-mcp`로 연결할 때마다(세션 재시작·재연결마다) 무거운 상태(bridge 연결, csv cache, registry)를 통째로 든 풀서버 프로세스가 새로 뜬다. 비정상 종료 시 정리 메커니즘이 없어 orphan으로 계속 누적된다. 부수적으로, 동시에 여러 시뮬레이터에 붙을 때 "어느 브릿지가 내 세션 것인지" MCP 레이어가 구분하지 못하는 attach 모호성도 존재한다. |
| **Solution** | **F-A(채택: 안C+)** 상주 프리포크 수퍼바이저 + 연결당 fork 워커(OS 프로세스 격리 그대로, 애플리케이션 코드 무변경) + 하트비트 기반 dead-worker 탐지 + idle-culler 분리 서비스. **F-C(재정의)** raw TCP 포트 bind 충돌은 v4.1에서 이미 해결되어 있었음(정정) — 남은 문제는 attach 모호성이며 `sim_dir` 키 기반 레지스트리로 해결. 향후 사용자 규모 확대 시 세션별 완전 격리(안A/B) 또는 인프라 레벨 라우팅으로 승격 가능. |
| **Function/UX Effect** | 세션을 몇 번 재시작해도 cloud0에 상주하는 xcelium-mcp 프로세스 수가 상한 내로 유지됨. 한 사용자가 서로 다른 sim_dir 2개 이상을 동시에 디버깅해도 브릿지가 뒤섞이지 않음. |
| **Core Value** | 지금 구조로는 사용자 수 × 세션 재시작 횟수에 비례해 프로세스가 무한 누적되어, 결국 cloud0 리소스(메모리/PID) 고갈로 이어질 수 있는 시한폭탄 — 사용자가 늘기 전에 구조적으로 막아야 함. 동시에, 이미 검증된 업계 패턴(JupyterHub/PgBouncer/Gunicorn) 위에서 최소 변경·최소 회귀 리스크로 근본 해결을 달성한다. |

---

## 1. Problem Detail

### 1.1 실전 관찰 (2026-07-06, venezia-fpga 세션)

```
$ ps -ef | grep xcelium-mcp
hoseung+ 12602 12592  tcsh -c /opt/mcp-env/bin/xcelium-mcp   (spawn 11:08, sshd 12592)
hoseung+ 12631 12602  python /opt/mcp-env/bin/xcelium-mcp
hoseung+ 18803 18795  tcsh -c /opt/mcp-env/bin/xcelium-mcp   (spawn 13:02, sshd 18795)
hoseung+ 18851 18803  python /opt/mcp-env/bin/xcelium-mcp
hoseung+ 24555 24544  tcsh -c /opt/mcp-env/bin/xcelium-mcp   (spawn 14:40, sshd 24544)
hoseung+ 24594 24555  python /opt/mcp-env/bin/xcelium-mcp
```

동일 사용자(hoseung.lee)의 세션이 하루 동안 최소 3번 (재)시작되면서 각각 독립된 `sshd → tcsh → python` 프로세스 트리를 남겼다. 3개 모두 서로 다른 `sshd: hoseung.lee@notty` 세션에 뿌리를 두고 있어(각각 별도 TCP 연결), 이전 연결이 종료됐어도 원격 프로세스가 자동으로 정리되지 않았음을 시사한다.

### 1.2 Root Cause

**클라이언트 launch 설정** (`~/.claude.json` mcpServers.xcelium-mcp):
```json
{"type": "stdio", "command": "ssh", "args": ["-o", "BatchMode=yes", "cloud0", "/opt/mcp-env/bin/xcelium-mcp"]}
```

**서버 진입점** (`src/xcelium_mcp/server.py:72-74`):
```python
def main():
    ...
    mcp.run(transport="stdio")
```

`mcp` SDK의 `stdio` transport는 설계상 "프로세스 1개 = 클라이언트 연결 1개"다. 이 프로세스가 다음 무거운 상태를 전부 자신의 메모리 안에 들고 있다:
- `bridges`(BridgeManager) — xmsim/SimVision TCP 연결
- `csv_cache` — SHM→CSV 추출 캐시
- `_USER_TMP`(`shell_utils.py:335`), `mcp_registry.json` 등 — 사실 이건 파일 기반이라 프로세스 재시작에도 살아남지만, 프로세스 자체의 존재/개수와는 무관

**정리 메커니즘 부재**: 클라이언트(Claude Code)가 비정상 종료(터미널 강제 종료, 노트북 절전, 네트워크 단절)되면 로컬 `ssh` 자식 프로세스도 함께 없어지길 기대하지만, TCP 연결이 명시적으로 닫히지 않고 방치되면(keepalive 미설정) 원격 `sshd`와 그 자식 `xcelium-mcp` 프로세스가 죽지 않고 남는다. 코드 내에 idle-timeout이나 self-reap 로직이 없음(`grep -rn "SIGTERM\|atexit\|idle.*timeout" src/xcelium_mcp/server.py` 결과 없음, 2026-07-06 확인).

### 1.3 파급 범위 — 왜 사용자가 늘면 더 심각한가

- 현재는 1인(hoseung.lee) 사용만 상정된 구조다: `mcp_registry.json`이 `Path.home()/.xcelium_mcp/`(`registry.py:12`)에, scratch 캐시가 `/tmp/xcelium_mcp_{uid}/`(`shell_utils.py:335`)에 있어 **파일 기반 상태는 이미 사용자별로 격리**돼 있다(2026-07-06 직접 코드 확인 — 사용자 추정이 맞았음).
- 그러나 **프로세스 자체의 누적**은 이 격리와 무관하게 (사용자 수) × (세션 재시작 횟수)로 그대로 증가한다.
- 추가로 **`bridge_port`는 host-global 자원**이다: `tcl_bridge.py:8 DEFAULT_BRIDGE_PORT = 9876`가 고정값이다.
  > **[2026-07-07 정정]** 2026-07-06 초안에서는 "포트 충돌 시 재시도·자동할당 로직이 없음(`find_free_port` 류 함수 부재)"이라고 썼으나, 이는 **Python 쪽만 grep한 결과로 부정확했다.** `tcl/mcp_bridge.tcl:85-99`("P1-2: Auto port")가 이미 base port(9876)부터 `port_range`(10개) 범위 내에서 자동으로 빈 포트를 찾아 bind하고, 실제 할당된 포트를 `bridge_ready_$port` 파일에 기록하는 로직을 갖고 있다 — `git log -S "P1-2: Auto port"` 확인 결과 커밋 `35bded1`("v4.1 P1-1/P1-2/P1-3")로, 이 plan 초안보다 훨씬 이전에 이미 존재했다. **즉 raw TCP bind 충돌 자체는 이미 해결되어 있다.**
  >
  > 대신 진짜 남은 문제는 **attach 쪽의 모호성**이다: `bridge_manager.py:scan_ready_files()` + `sim_lifecycle.py:_auto_connect_all()`는 `user_tmp` 디렉토리의 `bridge_ready_*` 파일을 전부 글롭으로 긁어와 순서대로 `bridges.set_xmsim()`으로 덮어쓴다 — ready 파일이 2개 이상이면(같은 사용자가 서로 다른 sim_dir 2개를 동시에 디버깅하는 경우) **마지막으로 처리된 것으로 조용히 덮어써버려**, MCP 세션이 의도치 않은 시뮬레이터에 명령을 보낼 위험이 있다. 지금까지는 세션마다 프로세스가 통째로 새로 떠서(`sim_bridge_run` 직후 그 프로세스가 정확한 TclBridge 객체를 직접 들고 있음) 잘 드러나지 않았지만, F-A(데몬화)로 워커가 더 오래 살아있게 되면 이 모호성이 실전에서 자주 노출된다. 이 문제는 F-C로 재정의해서 다룬다(§3 F-C).

---

## 2. Architecture Decision — 데몬 구현 방식 (2026-07-07)

### 2.1 검토한 안

F-A("상주 데몬 + 얇은 포워더")를 구체화하기 위해 3가지 구현안을 비교했다. 핵심 제약: `BridgeManager`(`bridge_manager.py:38`)가 프로세스당 단일 전역 인스턴스로 `xmsim`/`simvision`/`current_test_name`/`current_tb_source`를 들고 있어, 데몬화 시 여러 클라이언트가 이 상태를 어떻게 공유/격리하느냐가 관건이다.

| 안 | 격리 레이어 | BridgeManager/DI 변경 | tools/*.py 변경 | 회귀 리스크 |
|---|---|---|---|---|
| **A** — `Context` 기반 세션 격리 | 애플리케이션(세션 단위) | `Dict[session_id, BridgeManager]` | 7개 모듈 전부 `Context` 파라미터 추가 | 높음 |
| **B** — `sim_dir` 키 기반 자원 격리 | 애플리케이션(자원 키 단위) | `Dict[sim_dir, BridgeManager]` | `bridges` 인자를 조회 함수로 교체 | 중간 |
| **C** — 프리포크 수퍼바이저 | OS 프로세스(fork 단위) | 변경 없음 | 변경 없음 | **거의 없음** |

### 2.2 상용/오픈소스 사례 리서치

"세션 1개 = stateful 백엔드 커넥션 1개"를 실제로 다루는 상용 MCP 서버 사례는 찾지 못했다 — GitHub/Sentry 등 대부분의 원격 MCP 서버는 REST API를 감싼 stateless wrapper라 세션 간 유지할 상태가 원래 없다. MCP 스펙의 `Mcp-Session-Id`도 순수 상관관계 ID일 뿐, 세션→실제 자원 매핑은 구현자 책임으로 위임되어 있다. 대신 구조적으로 더 가까운 선례는 다음과 같다.

| 시스템 | 격리 단위 | 개수 제한 | 정리(reap) 방식 | 시사점 |
|---|---|---|---|---|
| Cloudflare `McpAgent`+Durable Object | 세션당 Durable Object 1개 | 플랫폼 관리 | idle 시 hibernate, 재연결 시 wake | 안A의 완성형(플랫폼 위임) |
| Microsoft `mcp-gateway` | session_id→백엔드 pod 라우팅 고정 | k8s 관리 | k8s 위임 | 라우팅/lifecycle을 인프라로 분리하는 선례 |
| **JupyterHub** | 세션(사용자)당 프로세스/컨테이너 1개 | 기본 없음(쿼터로만) | **별도 idle-culler 서비스**가 폴링, 시간 기준 정리 | **우리 문제와 형태가 가장 유사한 실전 선례** |
| PgBouncer(session pooling 모드) | 클라이언트 세션 생존 기간 동안 커넥션 1개 고정 | 풀 크기 | 세션 종료 시 반납 | "세션당 자원 1개 고정"이 stateful 워크로드의 정석임을 검증 — 안C의 근거 |
| Gunicorn(prefork) | 워커 프로세스당 | 고정 워커 수 | **silent-timeout(무응답 N초) 감지 → kill+재기동** | 우리에게 없던 하트비트 기반 dead-worker 탐지 아이디어 |

### 2.3 결정: 안C+ 채택

**안C(프리포크 수퍼바이저)를 기본으로 채택**하되, 위 선례에서 검증된 패턴 2가지를 추가해 "안C+"로 보강한다.

1. **프리포크 수퍼바이저** — cloud0에 상주 수퍼바이저 1개가 unix domain socket을 리슨. 연결이 들어올 때마다 이미 import가 끝난 인터프리터에서 `fork()`로 워커를 떠서, 그 워커가 지금과 동일하게 `mcp.run(transport="stdio")` 1:1 모델로 동작. `BridgeManager`/`tools/*.py` 등 애플리케이션 코드는 **무변경** — PgBouncer의 session-pooling이 검증하듯, stateful 백엔드에는 "세션당 자원 1개 고정"이 정석이므로 지금 구조의 격리 단위 자체는 그대로 두는 것이 맞다.
2. **하트비트 기반 dead-worker 탐지**(Gunicorn 사례 차용) — §1.2에서 지적한 "TCP가 명시적으로 안 닫히고 방치되는" 케이스는 단순 EOF 감지로 못 잡는다. 워커가 주기적으로 생존 신호를 남기고, 수퍼바이저가 무응답 N초 시 강제 kill.
3. **idle-culler를 별도 서비스로 분리**(JupyterHub 사례 차용) — reap 로직을 수퍼바이저 프로세스 안에 욱여넣지 않고, 독립 폴링 데몬(또는 systemd timer)으로 분리. 기존 F-B(즉시 완화책)와 자연스럽게 합쳐짐.

**기각 사유**:
- 안A(완전 세션 격리)는 7개 tool 모듈 DI 패턴 전면 교체 + 472개 기존 테스트 재작업이 필요해 지금 시점(1인~소수 사용자) 대비 비용이 과함.
- 안B(sim_dir 키 격리)는 안C보다 회귀 리스크는 낮지만 안C만큼 "무변경"은 아니며, 아래 F-C(attach 모호성 해소)로 필요한 부분만 선택적으로 흡수한다.

### 2.4 향후 승격 경로

사용자 규모가 실질적으로 늘어 안C+의 "워커 재기동 시 세션 연속성 상실"이 문제가 되는 시점에 다음 순서로 승격한다.

- **1단계(세션 재접속, 저비용)**: 워커가 죽어도 **xmsim/SimVision 자체는 별도 장수 프로세스라 죽지 않는다** — 이미 `bridge_manager.py`의 `scan_ready_files()`/`_auto_connect_all()`로 재접속 가능. 없어지는 건 순수 Python 메모리 상태(`current_test_name`/`current_tb_source`, `bridge_manager.py:51-52`)뿐이다. **주의: `checkpoint.py`(action=save/restore)는 xmsim의 시뮬레이션 시간/파형 상태를 다루는 기능이지 MCP 세션 상태가 아니므로 재활용 대상이 아니다.** 대신 §3 F-C에서 도입하는 `sim_dir` 키 기반 레지스트리를 그대로 확장해, 워커 종료 시 `current_test_name`/`current_tb_source`/포트를 `mcp_registry.json`에 sim_dir 키로 저장해두고 재기동한 워커가 그 키로 조회·복원한다.
- **2단계(완전 세션 격리)**: 안A/B로 승격 — `Context` 또는 `sim_dir` 키 기반으로 `BridgeManager`를 진짜 다중화.
- **3단계(인프라 위임)**: Microsoft `mcp-gateway` 사례처럼 세션→백엔드 라우팅을 nginx/systemd socket activation 등 인프라 레이어로 분리. 단일 호스트·소수 사용자 규모에서는 과설계이므로 필요해질 때만 고려.

---

## 3. Fix Items

### F-A: 프리포크 수퍼바이저 데몬(안C+)으로 구조 전환 (근본 해결, 우선순위 높음)

> §2.3에서 결정한 안C+ 반영 — 기존 초안의 "streamable-http 전면 전환"안은 기각하고, stdio 1:1 모델과 애플리케이션 코드는 유지한 채 프로세스 lifecycle만 수퍼바이저가 관리하는 방식으로 대체한다.

- cloud0에 상주 수퍼바이저 1개를 `systemd --user` 서비스로 등록. unix domain socket(`/run/user/$UID/xcelium-mcp.sock`)을 리슨.
- 연결이 들어올 때마다 이미 import가 끝난 인터프리터에서 `fork()`로 워커 프로세스를 떠서, 그 워커가 지금과 동일하게 `mcp.run(transport="stdio")` 1:1 모델로 동작(`BridgeManager`/`tools/*.py` 등 애플리케이션 코드 무변경).
- 클라이언트 launch 커맨드를 `ssh cloud0 /opt/mcp-env/bin/xcelium-mcp`(풀서버 콜드 기동)에서 `ssh cloud0 socat STDIO UNIX-CONNECT:/run/user/$UID/xcelium-mcp.sock`(수퍼바이저에 연결) 형태로 교체.
- **하트비트 기반 dead-worker 탐지**: 워커가 주기적으로 생존 신호(파일 touch 또는 수퍼바이저에 ping)를 남기고, 수퍼바이저가 무응답 N초 시 강제 kill — §1.2의 "TCP가 명시적으로 안 닫히고 방치" 케이스를 EOF 감지만으로 못 잡는 문제 보완(Gunicorn silent-timeout 패턴 차용).
- **idle-culler는 수퍼바이저와 별도 서비스로 분리**(JupyterHub 패턴 차용) — F-B와 통합.
- 효과: 세션이 몇 번 재시작되든 콜드 spawn(ssh→tcsh→python 풀 기동) 없이 fork 워커로 처리되고, 동시 워커 수 상한을 둬서 무한 누적을 원천 차단. 세션별 상태 격리는 OS 프로세스 분리로 자동 보장(§2.3 근거 — PgBouncer의 session pooling과 동일한 논리).
- 리스크: 워커가 죽으면(하트비트 타임아웃 등) 그 세션의 Python 메모리 상태(`current_test_name`/`current_tb_source`)는 사라짐 — 재접속 시 사용자가 다시 `sim_bridge_run`/`connect_simulator`를 호출해야 할 수 있음. 완전 자동 재접속은 §2.4 향후 승격 경로(1단계)에서 다룬다.

### F-B: Idle 워커 culler + 정리 스크립트 (F-A와 통합)

- 워커가 stdin EOF(부모 연결 종료)를 감지하면 즉시 종료되는지 확인 — 안 되면 명시적 EOF 핸들러 추가.
- **독립된 idle-culler 서비스**(cron 또는 systemd timer, F-A 수퍼바이저와 별도 프로세스)가 하트비트 파일들을 폴링해 idle(브릿지 미연결 + 활동 없음 N시간 이상) 워커를 찾아 정리 — JupyterHub의 `jupyterhub-idle-culler`와 동일한 관심사 분리.
- sshd `ClientAliveInterval`/`ClientAliveCountMax` 설정 권장(죽은 TCP 연결을 sshd가 스스로 정리) — **root 소유 시스템 설정이라 코드 변경이 아니라 운영 가이드로만 제안**, 이 prd/plan의 구현 스코프에는 포함하지 않음.

### F-C: attach 모호성 해소 — `sim_dir` 키 기반 브릿지 레지스트리 (재정의, 2026-07-07)

> **[정정]** 기존 초안의 "raw TCP 포트 bind 충돌 회피"는 **이미 v4.1(`tcl/mcp_bridge.tcl:85-99`, 커밋 `35bded1`)에서 해결되어 있었음**을 확인(§1.3 정정 참조) — base port(9876)부터 `port_range`(10개) 내에서 자동으로 빈 포트를 탐색해 bind하는 로직이 이미 존재. F-C는 이 항목을 폐기하고, 실제 남은 문제인 **attach 쪽 모호성**으로 재정의한다.

- **문제**: `bridge_manager.py:scan_ready_files()` + `sim_lifecycle.py:_auto_connect_all()`가 `bridge_ready_*` 파일을 전부 글롭으로 긁어와 순서대로 덮어쓰기 때문에, 한 사용자가 서로 다른 sim_dir 2개 이상을 동시에 디버깅하면 어느 브릿지에 붙을지 모호해진다. F-A(안C+)로 워커가 더 오래 살아있게 되면 이 케이스가 실전에서 자주 노출된다.
- **해결**: `mcp_registry.json`(`registry.py:12`, 사용자별 파일이라 사용자 간 충돌 없음)에 `sim_bridge_run` 성공 시 `{sim_dir: {"bridge_port": N, "bridge_type": "xmsim"}}`를 기록.
  - `sim_dir`이 주어진 호출(`connect_simulator` 등)은 글롭 스캔 대신 레지스트리에서 정확한 포트를 조회해 그 포트로만 direct connect.
  - `sim_dir` 없이 "auto" 호출하는 레거시 경로는 기존 스캔을 폴백으로 유지하되, 후보가 2개 이상이면 **조용히 덮어쓰지 말고 "여러 브릿지가 감지됨, sim_dir을 지정하라"는 명시적 에러**로 변경(현재 코드의 잠재 버그이기도 함 — F-A 여부와 무관하게 수정 대상).
- 이 레지스트리 스키마는 §2.4 향후 승격 경로(1단계, 워커 재기동 시 `current_test_name`/`current_tb_source` 복원)에서 필드만 추가해 그대로 재사용한다.

**우선순위**: F-A(근본, 안C+) > F-B(F-A와 통합 구현) > F-C(attach 모호성 — F-A 배포 전에도 독립적으로 넣을 수 있는 낮은 리스크 항목이나, F-A로 노출 빈도가 높아지므로 F-A와 동시 배포 권장).

---

## 4. Scope

| 파일 | 변경 내용 |
|------|----------|
| 수퍼바이저(신규, 위치는 Design 단계에서 결정) | F-A: unix socket 리슨, 연결당 fork, 하트비트 감시 |
| `src/xcelium_mcp/server.py` | F-A: 워커 쪽 하트비트 신호 발신 로직 추가(transport는 `stdio` 유지) |
| 배포 스크립트/systemd unit(신규) | F-A: 수퍼바이저 상주 등록 + idle-culler timer 등록. 이 저장소 범위 밖일 수 있음(배포 인프라) — 위치는 Design 단계에서 결정 |
| `src/xcelium_mcp/registry.py` | F-C: sim_dir별 `bridge_port`/`bridge_type` read/write 헬퍼 추가 |
| `src/xcelium_mcp/bridge_lifecycle.py` | F-C: `_start_bridge` 연결 성공 시 레지스트리에 포트 기록 |
| `src/xcelium_mcp/bridge_manager.py` / `src/xcelium_mcp/tools/sim_lifecycle.py` | F-C: `connect_simulator`가 sim_dir 있으면 레지스트리 우선 조회, 후보 2개 이상이면 명시적 에러로 fail-loud |
| 클라이언트 측 `~/.claude.json` mcpServers.xcelium-mcp | F-A: launch 커맨드를 수퍼바이저 소켓 연결 방식으로 교체(각 사용자 로컬 설정, 이 저장소 범위 밖) |

---

## 5. Test Plan

| # | 테스트 | 검증 |
|---|--------|------|
| T-1 | 동일 클라이언트로 5회 연속 연결/해제 반복 | cloud0의 수퍼바이저는 1개, fork된 워커 수가 동시 연결 수 이상으로 누적되지 않음(F-A) |
| T-2 | 클라이언트를 비정상 종료(SSH 강제 kill) 후 재연결 | 이전 연결의 워커가 하트비트 타임아웃으로 정리됨(F-A) |
| T-3 | idle 상태(브릿지 미연결)로 N시간 경과 후 idle-culler 동작 확인(F-B) | 정리 대상 워커가 실제로 종료됨, 수퍼바이저 자체는 영향 없음 |
| T-4 | 같은 사용자가 서로 다른 sim_dir 2개에서 동시에 `sim_bridge_run` 실행 후 각각 `connect_simulator(sim_dir=...)` 호출 | 각 호출이 자신의 sim_dir에 해당하는 포트로만 연결되고 서로 뒤섞이지 않음(F-C) |
| T-5 | sim_dir 없이 `connect_simulator(target="auto")` 호출 시 살아있는 브릿지가 2개 이상인 상태 | 조용히 하나로 덮어쓰지 않고 모호성 에러 반환(F-C) |
| T-6 | 회귀: 기존 24개 MCP tool이 프리포크 워커 위에서도 동일하게 동작 | 기존 pytest 스위트 전체 통과 |

---

## 6. Related Documents

- `xcelium-mcp-tool-usage-guide.plan.md` — tool 인벤토리/사용법 문서, 이 Plan과 직접적 연관은 없으나 F-A로 transport가 바뀌면 참조 갱신 필요할 수 있음.
- `plans/prd.json` F-175/F-176 — 같은 세션에서 발견된 **별개 문제**(TB 소스 provenance 부재). 이 문서(프로세스 라이프사이클)와는 무관하지만 동일한 배경(venezia-fpga 세션에서 xcelium-mcp 구조 조사 중 발견)에서 나왔다.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-07-06 | 초안 — venezia-fpga 세션에서 cloud0 프로세스 누적(3쌍) 발견, root cause(stdio 1:1 프로세스 모델) 특정, F-A(데몬화)/F-B(reaper)/F-C(포트 충돌 회피) 정리. 사용자 확인: `/tmp`/`$HOME` 파일 기반 상태는 이미 사용자별 격리됨(코드로 확인) — 프로세스 개수·포트는 별개 미해결 문제. |
| 0.2 | 2026-07-07 | 아키텍처 검토·결정 — F-A 데몬 구현 방식으로 안A(Context 세션 격리)/안B(sim_dir 키 격리)/안C(프리포크 수퍼바이저) 비교, JupyterHub/PgBouncer/Gunicorn/Cloudflare McpAgent/Microsoft mcp-gateway 실전 사례 리서치 후 **안C+(프리포크 수퍼바이저 + 하트비트 dead-worker 탐지 + idle-culler 분리) 채택**. F-C를 "raw 포트 충돌 회피"에서 **"attach 모호성 해소(sim_dir 키 레지스트리)"로 재정의** — 기존 초안의 포트 bind 충돌 주장은 v4.1(`35bded1`)에서 이미 해결되어 있었음을 확인, 정정. 향후 승격 경로(§2.4) 명시: 1단계 세션 재접속(`checkpoint.py`는 시뮬레이션 상태용이라 재활용 대상이 아님을 확인 — sim_dir 키 레지스트리 확장으로 대체) → 2단계 완전 세션 격리(안A/B) → 3단계 인프라 위임. |
