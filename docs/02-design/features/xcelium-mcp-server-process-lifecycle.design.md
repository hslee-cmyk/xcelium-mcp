# Design: xcelium-mcp Server Process Lifecycle (안C+)

> **Summary**: stdio 1:1 콜드 spawn 모델을 프리포크 수퍼바이저(안C+)로 전환해 cloud0 프로세스 무한 누적을 막고, `sim_dir` 키 기반 레지스트리로 다중 시뮬레이터 attach 모호성을 해소한다.
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Date**: 2026-07-07
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-server-process-lifecycle.plan.md](../../01-plan/features/xcelium-mcp-server-process-lifecycle.plan.md)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | (1) `mcp.run(transport="stdio")`(`server.py:74`)의 1 connection = 1 cold process 모델 때문에 세션 재시작마다 cloud0에 무거운 풀서버 프로세스가 새로 뜨고 정리되지 않아 무한 누적된다. (2) `bridge_manager.py::scan_ready_files()`가 여러 개의 살아있는 브릿지 중 하나를 조용히 골라버려, 한 사용자가 서로 다른 sim_dir을 동시에 디버깅하면 엉뚱한 시뮬레이터에 붙을 위험이 있다. |
| **Solution** | Plan §2에서 결정한 **안C+**: `socketserver.ForkingMixIn` 기반 상주 수퍼바이저(신규 `supervisor.py`) + 연결당 fork 워커(애플리케이션 코드 무변경) + 클라이언트 측 SSH keepalive로 죽은 연결 탐지 + `/proc` 순수 관찰 기반 idle-culler(신규 파일/스레드 없음). attach 모호성은 이미 `registry.py::_update_registry_from_config`에 존재하는 `bridge_port` 필드를 런타임에 실제 접속 포트로 갱신하는 방식(F-C)으로 해소. |
| **Function/UX Effect** | 클라이언트 설정에서 launch 커맨드 한 줄만 바뀌고, MCP tool 26개의 동작·시그니처는 완전히 동일. 세션을 몇 번 재시작해도 cloud0 상주 프로세스 수가 동시 접속 수 이상으로 늘지 않음. |
| **Core Value** | `server.py`/`tools/*.py`/`BridgeManager`를 한 줄도 건드리지 않고(격리 단위=OS 프로세스를 그대로 유지) 프로세스 lifecycle과 attach 정확성만 고쳐, 472개 기존 테스트에 대한 회귀 리스크를 최소화하면서 근본 문제를 해결한다. |

---

## 1. Overview

### 1.1 Design Goals

1. cloud0에 상주하는 xcelium-mcp 관련 프로세스 수를 "동시 활성 세션 수" 이하로 항상 유지한다.
2. `server.py`, `tools/*.py`, `bridge_manager.py`의 기존 로직·시그니처는 변경하지 않는다(회귀 리스크 최소화).
3. TCP가 명시적으로 닫히지 않고 방치되는 경우(비정상 종료)도 탐지해 정리하되, 이를 위해 워커에 상시 백그라운드 작업(스레드/주기적 파일 쓰기)을 추가하지 않는다(0.3 개정, 아래 §1.4).
4. 한 사용자가 서로 다른 sim_dir 2개 이상을 동시에 열어도 브릿지가 뒤섞이지 않는다.
5. 기존 24~26개 MCP tool의 pytest 스위트가 수정 없이 그대로 통과한다.

### 1.2 Design Principles

- **무변경 우선(Zero App-Code Change)**: 격리는 OS 프로세스 분리로 이미 달성되어 있다(PgBouncer의 session-pooling과 동일 논리, Plan §2.3) — 애플리케이션 레이어를 건드리지 않고 프로세스 lifecycle만 감싼다.
- **표준 라이브러리 우선**: 수제 `os.fork()`/`SIGCHLD` 처리 대신 `socketserver.ForkingMixIn`을 사용해 zombie reap·워커 수 상한을 표준 라이브러리에 위임한다(Checkpoint 3 결정).
- **관심사 분리**: 수퍼바이저(fork 담당)와 idle-culler(정리 담당)를 별도 프로세스로 분리한다(JupyterHub 패턴, Plan §2.3).
- **기존 자산 재사용**: `registry.py`에 이미 존재하는 `bridge_port` 필드(§4.1)를 재활용하고, 신규 스키마를 만들지 않는다.

### 1.3 CentOS7(cloud0) 실측 제약 — 2026-07-07 SSH 직접 확인

> 0.1 초안은 "Linux면 systemd --user + socat이 당연히 된다"고 가정했다. cloud0에 직접 SSH로 접속해 확인한 결과 **두 전제 모두 지금 당장은 성립하지 않는다.** 아래 실측 결과에 따라 §3/§5/§7/§9를 수정한다.

| 확인 항목 | 결과 | 설계에 미치는 영향 |
|---|---|---|
| OS | CentOS Linux 7 (Core), `systemd 219` | systemd 자체는 있음(socket/timer unit 문법 지원) — 문제는 버전이 아니라 **가용성**(아래) |
| `loginctl show-user` → `Linger=no` | lingering 꺼져 있음 | 로그인 세션이 끝나면 `systemd --user` 인스턴스 자체가 내려감 — 상주 데몬 전제가 깨짐 |
| `systemctl --user status` (비대화형 SSH) | `Failed to get D-Bus connection: No such file or directory` | **지금 이 SSH 세션에서는 systemd --user 유닛을 관리할 수조차 없음** |
| `loginctl enable-linger $(whoami)` (본인 계정으로 시도) | `Interactive authentication required`(polkit) | 비대화형 SSH에서 본인이 직접 켤 수 없음 — root 또는 인터랙티브 세션 필요 |
| `sudo -n true` | `sudo: a password is required` | 비밀번호 없는 sudo 불가 — 즉시 root 권한 확보 불가(이 프로젝트 규칙상 root 작업은 안내만, 직접 실행 금지와도 부합) |
| `which socat` | 없음(`no socat in ...`) | 클라이언트 launch 커맨드에서 `socat` 의존 불가 — 새 시스템 패키지 설치도 root 필요라 즉시 불가 |
| `/opt/mcp-env/bin/python --version` | `Python 3.10.8` | ✅ 기존 venv가 이미 요구사항(`>=3.10`) 충족 — 신규 인터프리터 불필요 |
| `getenforce` | 명령 없음 → `/etc/selinux/config`: `SELINUX=disabled` | SELinux 관련 caveat 전부 제거(N/A) |
| `systemctl is-active/is-enabled crond` | `active`/`enabled` (root가 이미 상시 구동 중) | **사용자 crontab은 즉시 사용 가능** — 별도 권한 요청 없이 지금 배포 가능 |
| `crontab -l` / 빈 crontab 등록 시도 | 정상 동작(cron.allow/deny 제한 없음) | 사용자 권한만으로 워치독 등록 가능 확인 |
| `/run/user/1001` | 존재는 하나 `Linger=no`라 마지막 세션 종료 후 정리될 수 있음 | 소켓을 `/run/user/$UID/`에 두면 유실 위험 — `$HOME` 하위로 이동 |

**결론 — 0.1 대비 3가지 변경**:

1. **기본 배포안을 "cron 기반 워치독"으로 바꾼다.** `systemd --user`는 admin이 나중에 `loginctl enable-linger hoseung.lee`를 (root로, 또는 본인이 인터랙티브 세션에서) 한 번 실행해준 뒤 승격 가능한 **옵션**으로 격하한다(§7).
2. **`socat` 의존을 제거하고 순수 Python 포워더(`stdio_forward.py`)로 대체한다** — 새 시스템 패키지 설치가 필요 없어야 root 개입 없이 지금 배포 가능하기 때문.
3. **소켓 경로를 `/run/user/$UID/` 대신 `$HOME/.xcelium_mcp/run/`으로 옮긴다** — lingering이 꺼진 상태에서 세션 종료 후 tmpfs 정리로 소켓이 사라지는 사고를 피하기 위함.

### 1.4 하트비트 파일 제거 — 0.3 개정

> 0.2까지는 워커가 15초마다 하트비트 파일을 touch하는 방식이었다. 리뷰 중 "계속 파일을 touch하는 게 낭비"라는 지적을 받고 재검토한 결과, 낭비보다 더 근본적인 문제 — **F-B(idle 정리)의 목적과 정면으로 충돌**한다는 점을 발견해 완전히 제거했다.

**문제**: 무조건 15초마다 touch하면, 사용자가 실제로 몇 시간을 아무것도 안 해도 idle-culler에게는 "방금 활동이 있었다"로 보인다. Plan §F-B가 원래 정의한 "브릿지 미연결 + 활동 없음 N시간" 대상을 구조적으로 하나도 못 잡는다.

**재검토 결과 하트비트가 필요했던 두 목적을 분리해서 각각 더 정확하고 더 저렴한 방법으로 대체**:

| 목적 | 기존(0.2) | 신규(0.3) | 근거 |
|---|---|---|---|
| 죽은/방치된 연결 탐지(F-A) | 워커 하트비트 스레드 | **클라이언트 측 SSH keepalive**(`ServerAliveInterval`/`ServerAliveCountMax`, §7.4) | MCP SDK 소스(`mcp/server/stdio.py::stdio_server`) 확인 결과 stdin EOF 시 워커가 이미 스스로 정상 종료한다 — 필요한 건 "방치된 연결을 EOF로 바꿔주는 것"뿐이고, 이건 root 없이 클라이언트 ssh 옵션만으로 해결된다(`sshd`의 `ClientAliveInterval`과 달리 root 불필요) |
| idle 워커 정리(F-B) | 하트비트 mtime 검사 | **`/proc` 순수 관찰** — 수퍼바이저의 자식 pid 목록(`/proc/<supervisor_pid>/task/*/children`) 중 outbound TCP established 연결이 하나도 없는(=브릿지 미연결) 워커를 대상으로, 프로세스 시작 시각(`/proc/<pid>/stat`)이 임계값보다 오래된 경우 정리 | 워커 쪽에 스레드도 파일도 전혀 추가하지 않음 — idle-culler 혼자 외부에서 관찰만 해서 판단 |

결과적으로 `heartbeat.py`는 삭제하고, 워커 프로세스는 lifecycle 목적의 백그라운드 작업을 **일절 갖지 않는다**(§1.2 "무변경 우선" 원칙을 스레드 레벨까지 확장).

---

## 2. Architecture Options (Checkpoint 3 — 완료)

Plan §2에서 데몬 방식 자체(안A/B/C)는 이미 "안C+(프리포크 수퍼바이저)"로 결정되었다. Design 단계에서는 그 수퍼바이저를 **구체적으로 어떻게 구현할지** 3가지로 좁혀 비교했다.

| 기준 | A: 수제 `os.fork()` | B: systemd 소켓 액티베이션 | **C: `socketserver.ForkingMixIn`(선택)** |
|---|:-:|:-:|:-:|
| 코드량 | 많음(SIGCHLD/zombie reap 직접 구현) | 적음(수퍼바이저 코드 자체가 없음) | 적음(표준 라이브러리가 fork+reap 담당) |
| Zombie reap | 직접 구현 필요, 버그 위험 큼 | systemd가 담당 | **`ForkingMixIn`이 자동 처리**(내부적으로 자식 종료 시 회수) |
| 워커 수 상한 | 직접 구현 | 없음(제어 어려움) | `max_children` 속성으로 기본 제공 |
| 로컬 개발/테스트 | 가능하나 버그 위험 | **systemd 없이는 재현 불가**(로컬 개발 난이도 높음) | 순수 Python이라 재현 가능(단, Linux 전용) |
| 하트비트/idle-culler 통합 | 직접 구현 | 여전히 별도 필요 — systemd가 대신해주지 않음 | 동일하게 필요하나 워커 실행부에 자연스럽게 결합 |
| 배포 의존성 | 없음 | systemd unit 파일 소유권(운영 인프라)에 강하게 결합 | systemd는 "상주시키는 용도"로만 사용(1개 프로세스 등록), 로직은 코드에 남음 |

**선택: C — `socketserver.ForkingMixIn`**. 근거: zombie reap과 워커 수 상한을 검증된 표준 라이브러리에 위임해 A의 버그 위험을 피하고, B처럼 배포 인프라(systemd)에 핵심 로직을 묻지 않아 로컬에서 재현·테스트 가능하다(단, `os.fork()` 기반이라 Linux 전용 — §8 Test Plan 참조).

---

## 3. Component Architecture

### 3.1 Component Diagram

```
[클라이언트] --ssh--> [cloud0]
                         │
                         ▼
              ~/.claude.json mcpServers.xcelium-mcp
              command: ssh cloud0 /opt/mcp-env/bin/python -m xcelium_mcp.stdio_forward
                        $HOME/.xcelium_mcp/run/xcelium-mcp.sock
                         │                                  (순수 stdlib, socat 불필요 — §1.3)
                         ▼
        ┌────────────────────────────────────┐
        │  Supervisor (신규 supervisor.py)     │  cron 워치독으로 상주(기본안, §7.2)
        │  socketserver.ForkingMixIn           │  또는 systemd --user(승격안, §7.3)
        │  + UnixStreamServer                  │
        │  listen: $HOME/.xcelium_mcp/run/     │  ← /run/user/$UID 아님(§1.3, linger=no라 유실 위험)
        │          xcelium-mcp.sock            │
        └──────────────┬───────────────────────┘
                        │ accept() 마다 fork()
                        ▼
        ┌────────────────────────────────────┐
        │  Worker (fork된 자식 프로세스)         │  연결 1개당 1개, 무변경 재사용
        │  1. stdin/stdout ← 유닉스소켓 dup2     │  lifecycle용 백그라운드 작업 없음(§1.4)
        │  2. xcelium_mcp.server.main() 그대로  │  ← BridgeManager/tools/*.py 무변경
        │     (mcp.run(transport="stdio"))      │
        └──────────────┬───────────────────────┘
                        │ (stdin EOF 시 SDK가 스스로 종료 — §1.4)
                        ▼
              클라이언트 SSH가 죽으면(ServerAliveInterval
              초과) ssh가 스스로 끊음 → EOF 전파

        ┌────────────────────────────────────┐
        │  idle-culler (신규 idle_culler.py)   │  수퍼바이저와 별도 프로세스, 순수 관찰만
        │  cron 폴링(기본) 또는 systemd timer(승격) │
        │  /proc/<supervisor>/task/*/children  │  ← 새 파일/스레드 없이 워커 pid 목록 확보
        │  + 워커별 established TCP 유무 확인     │  ← 브릿지 연결 여부 판단(§1.4)
        │  + 프로세스 시작 시각(age) 확인 → kill  │
        └────────────────────────────────────┘
```

### 3.2 Data Flow — 정상 연결

```
클라이언트 연결 → cloud0에서 stdio_forward.py가 unix socket에 연결 → Supervisor.accept()
  → fork() → 자식: stdio를 소켓에 재바인딩 → server.main() 그대로 실행
  → (기존과 동일) BridgeManager, tools/*.py, MCP tool 26개 정상 동작
  → 연결 종료 시 자식 프로세스 정상 종료 → ForkingMixIn이 자동 reap
```

### 3.3 Data Flow — attach 모호성 해소 (F-C)

```
sim_bridge_run(sim_dir=A) 성공
  → 실제 접속 포트를 registry.py의 projects[root].environments[A].bridge_port에 기록(갱신)

connect_simulator(sim_dir=A) 호출
  → registry에서 environments[A].bridge_port 조회 → 그 포트로만 direct connect
  → (기존처럼 bridge_ready_* 전체를 글롭 스캔하지 않음)

connect_simulator(target="auto") — sim_dir 미지정(레거시)
  → 기존 scan_ready_files() 폴백
  → 후보 ≥ 2개면: 조용히 덮어쓰지 않고 "여러 브릿지 감지됨, sim_dir 지정 필요" 에러 반환
```

### 3.4 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `supervisor.py`(신규) | `socketserver`(표준 라이브러리), `xcelium_mcp.server.main` | fork-per-connection, stdio 재바인딩 |
| `idle_culler.py`(신규) | `/proc`(표준 라이브러리 `pathlib`/`os`만으로 파싱, 신규 의존성 없음) | 워커 pid·TCP 상태·age를 순수 관찰(§1.4) — 워커 쪽 계측 불필요 |
| `bridge_lifecycle.py`(기존, 수정) | `registry.py`(기존, 확장) | 실제 접속 포트를 레지스트리에 write-back |
| `sim_lifecycle.py`/`bridge_manager.py`(기존, 수정) | `registry.py`(기존, 확장) | sim_dir 지정 시 레지스트리 우선 조회 |

---

## 4. Data Model

### 4.1 `mcp_registry.json` — 기존 필드 재사용 (신규 스키마 없음)

`registry.py:129 _update_registry_from_config()`가 **이미** sim_dir마다 `bridge_port` 필드를 쓰고 있음을 확인했다:

```python
envs[sim_dir] = {
    "tb_type": tb_type,
    "is_default": ...,
    "config_version": ...,
    "bridge_port": config.get("bridge", {}).get("port", DEFAULT_BRIDGE_PORT),  # 이미 존재
}
```

**문제**: 이 값은 `sim_discover` 시점의 **설정값**(`.mcp_sim_config.json`)일 뿐, `mcp_bridge.tcl`의 P1-2 auto-range가 실제로 바인딩한 **런타임 포트**와 다를 수 있다. F-C는 새 필드를 추가하는 게 아니라, 이 기존 필드를 연결 성공 시점에 **실제 포트로 덮어쓰는 write-back 경로 하나만 추가**한다.

```python
# bridge_lifecycle.py::_start_bridge, 연결 성공 직후(bridges.set_xmsim(new_bridge) 다음)
await update_bridge_port(sim_dir, actual_port)   # registry.py 신규 헬퍼
```

### 4.2 idle 판정 근거 — 신규 파일 없음(§1.4, 0.3 개정)

> 0.2의 하트비트 파일(`$HOME/.xcelium_mcp/run/workers/{pid}.heartbeat`)은 §1.4 사유로 제거했다. idle-culler는 다음 두 가지를 **순수하게 외부에서 관찰**해 판단하며, 워커/수퍼바이저 어느 쪽에도 새 파일이나 쓰기 작업을 추가하지 않는다.

1. **워커 pid 목록**: `/proc/<supervisor_pid>/task/<supervisor_pid>/children` 읽기(Linux 3.5+, CentOS7의 3.10 커널에서 사용 가능) — 수퍼바이저의 직계 자식 = 현재 살아있는 워커들.
2. **브릿지 연결 여부**: 각 워커 pid에 대해 `/proc/<pid>/net/tcp`(`+net/tcp6`)를 파싱해 established 상태의 outbound 소켓이 있는지 확인 — 하나도 없으면 "브릿지 미연결"로 간주.
3. **age**: `/proc/<pid>/stat`의 프로세스 시작 시각(`starttime`, 시스템 부팅 이후 tick)으로 계산.
4. 판정: 브릿지 미연결 **AND** age > 임계값(예: N시간) → SIGTERM, 유예 후에도 살아있으면 SIGKILL.

> 기존 `bridge_ready_*`(사용자별 `/tmp/xcelium_mcp_{uid}/`)나 소켓 경로(`$HOME/.xcelium_mcp/run/`, §1.3)와 달리, 이 섹션은 이제 **파일을 전혀 새로 만들지 않는다** — `/proc`은 커널이 이미 유지하는 정보이므로 읽기 전용으로 끝난다.

---

## 5. Component Detail

### 5.1 `src/xcelium_mcp/supervisor.py` (신규)

```python
class Supervisor(socketserver.ForkingMixIn, socketserver.UnixStreamServer):
    max_children = 40          # 표준 라이브러리 기본값, 필요 시 조정

class WorkerHandler(socketserver.BaseRequestHandler):
    def handle(self):
        os.dup2(self.request.fileno(), 0)
        os.dup2(self.request.fileno(), 1)
        xcelium_mcp.server.main()  # 기존 진입점 그대로, 무변경. 그 외 아무 것도 하지 않는다(§1.4)
```

- `server.main()`은 **한 줄도 수정하지 않는다.**
- **0.3 개정**: `WorkerHandler`도 하트비트 스레드 start/stop 호출이 완전히 빠졌다(§1.4) — 워커는 이제 순수하게 `dup2` 두 줄 + 기존 `main()` 호출뿐이다. lifecycle 관련 코드가 워커 프로세스 안에 단 한 줄도 없다.
- **fork-safety**: 수퍼바이저 프로세스 자신은 **asyncio 이벤트 루프를 절대 실행하지 않는다**(`xcelium_mcp.server`를 import만 하고 `main()`은 호출 안 함) — 이벤트 루프는 매 연결마다 fork된 자식 안에서 처음 생성된다. 부모(수퍼바이저)가 fork 시점에 멀티스레드 상태가 아니므로("fork+threads" 고전적 데드락 위험 없음) 안전하다.
- 소켓 리슨 경로: `$HOME/.xcelium_mcp/run/xcelium-mcp.sock`(§1.3), 생성 시 `os.chmod(sock_path, 0o600)` 명시(§9).
- 신규 진입점 `xcelium-mcp-supervisor`(pyproject.toml `[project.scripts]` 추가).

### 5.2 `src/xcelium_mcp/stdio_forward.py` (신규 — socat 대체)

> §1.3에서 cloud0에 `socat`이 설치돼 있지 않고, 새로 설치하려면 root(yum)가 필요함을 확인했다. 대신 표준 라이브러리 `socket`만으로 stdin/stdout ↔ unix socket을 중계하는 최소 스크립트를 작성한다.

```python
# python -m xcelium_mcp.stdio_forward <socket_path>
def main():
    sock_path = sys.argv[1]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    # 두 방향 중계: stdin→sock, sock→stdout (select 또는 스레드 2개)
    ...
```

- 클라이언트(로컬 Claude Code)의 `ssh` 명령이 cloud0 위에서 **이 스크립트를 실행**하고, 그 표준입출력이 ssh 파이프를 통해 로컬과 연결된다 — 로컬에는 아무것도 설치할 필요 없음(기존과 동일하게 ssh만 있으면 됨).
- 신규 진입점 없이 `python -m xcelium_mcp.stdio_forward`로 호출(엔트리포인트로 감싸도 되지만, 모듈 직접 실행이 더 단순).

### 5.3 `src/xcelium_mcp/idle_culler.py` (신규, 0.3 개정 — 순수 `/proc` 관찰)

```python
def find_worker_pids(supervisor_pid: int) -> list[int]:
    children = Path(f"/proc/{supervisor_pid}/task/{supervisor_pid}/children")
    return [int(p) for p in children.read_text().split()]

def has_established_tcp(pid: int) -> bool:
    # /proc/<pid>/net/tcp(+tcp6): 각 줄의 상태 필드가 01(ESTABLISHED)인 소켓이 있는지 확인
    ...

def process_age_seconds(pid: int) -> float:
    # /proc/<pid>/stat의 starttime(clock ticks) vs /proc/uptime으로 계산
    ...

def main():
    supervisor_pid = find_supervisor_pid()   # pgrep -f xcelium-mcp-supervisor 등
    for pid in find_worker_pids(supervisor_pid):
        if not has_established_tcp(pid) and process_age_seconds(pid) > IDLE_THRESHOLD_SEC:
            os.kill(pid, signal.SIGTERM)  # 유예 후 미종료 시 SIGKILL
```

- 단발성 스크립트(반복 실행은 cron이 담당, §7.2) — **워커/수퍼바이저 어느 쪽에도 계측 코드가 없다**(§1.4). 표준 라이브러리(`pathlib`, `os`, `signal`)만 사용.
- 신규 진입점 `xcelium-mcp-culler`.

### 5.4 `src/xcelium_mcp/registry.py` (기존, 확장)

- 신규 헬퍼: `async def update_bridge_port(sim_dir: str, port: int) -> None` — 기존 `_update_registry_from_config`와 동일한 project_root 정규화 로직을 재사용해 `environments[sim_dir]["bridge_port"]`만 갱신.
- 신규 헬퍼: `async def get_bridge_port(sim_dir: str) -> int | None` — 조회 전용.

### 5.5 `src/xcelium_mcp/bridge_lifecycle.py` (기존, 수정)

- `_start_bridge()`의 연결 성공 경로(2곳 — TCP 직접 연결 성공 시 / ready-file 폴백 성공 시) 양쪽에 `await update_bridge_port(sim_dir, actual_port)` 호출 추가.

### 5.6 `src/xcelium_mcp/bridge_manager.py` / `src/xcelium_mcp/tools/sim_lifecycle.py` (기존, 수정)

- `connect_simulator(sim_dir=...)`: sim_dir이 주어지면 `get_bridge_port(sim_dir)` 우선 조회 → 그 포트로 direct connect. 실패 시에만 기존 스캔으로 폴백(포트가 재기동으로 바뀌었을 극단 케이스 대비).
- `connect_simulator(target="auto")`(sim_dir 미지정): 기존 `scan_ready_files()` 로직 유지하되, 후보가 2개 이상이면 첫 번째로 조용히 덮어쓰지 않고 `"ERROR: N개의 브릿지가 감지됨({ports}). sim_dir을 지정하세요."` 반환.

---

## 6. Error Handling

| 상황 | 처리 |
|---|---|
| 클라이언트 네트워크가 말없이 죽음(network black hole) | §7.4 `ServerAliveInterval`/`ServerAliveCountMax` 초과 시 로컬 ssh가 스스로 종료 → EOF 전파 → 워커가 SDK 표준 동작으로 스스로 종료(§1.4, 하트비트 불필요) |
| 워커가 브릿지 미연결 상태로 장시간 방치(idle) | idle-culler가 `/proc` 관찰만으로 판정해 SIGTERM→SIGKILL(§4.2/§5.3) — 새 파일/스레드 없음 |
| `max_children` 초과 동시 연결 | `ForkingMixIn` 표준 동작(요청 큐잉/거부) — 운영 중 실제 발생 시 값 상향 검토 |
| `connect_simulator(auto)` 시 브릿지 ≥2개 감지 | 명시적 에러 반환(§5.6), 조용한 오연결 방지 |
| 수퍼바이저 자체가 죽음(드묾) | 기본안: cron 워치독(1분 이내 감지·재기동, §7.2) / 승격안: systemd `Restart=on-failure`(§7.3, admin이 linger를 켜준 이후). 재기동 중 새 연결 실패는 클라이언트 재시도로 흡수(운영 가이드, 이 repo 범위 밖) |

---

## 7. Deployment

> §1.3 실측 결과에 따라 **root/admin 개입 없이 지금 바로 가능한 안(7.2)을 기본**으로 하고, systemd --user(7.3)는 admin이 linger를 켜준 뒤 선택적으로 승격하는 안으로 둔다.

### 7.1 신규 entry point (`pyproject.toml`)

```toml
[project.scripts]
xcelium-mcp = "xcelium_mcp.server:main"
xcelium-mcp-supervisor = "xcelium_mcp.supervisor:main"
xcelium-mcp-culler = "xcelium_mcp.idle_culler:main"
# stdio_forward는 진입점 없이 `python -m xcelium_mcp.stdio_forward`로 직접 실행(§5.2)
```

### 7.2 기본 배포안 — cron 워치독 (root 불필요, 지금 바로 가능)

crond가 이미 root에 의해 상시 구동 중이고(`systemctl is-active crond` → `active`), 사용자 crontab 등록에 별도 제한이 없음을 확인했다(§1.3). `flock`으로 중복 기동을 막는다.

```cron
# crontab -e (hoseung.lee, sudo 불필요)
@reboot         /usr/bin/flock -n $HOME/.xcelium_mcp/run/supervisor.lock /opt/mcp-env/bin/xcelium-mcp-supervisor
* * * * *       /usr/bin/flock -n $HOME/.xcelium_mcp/run/supervisor.lock /opt/mcp-env/bin/xcelium-mcp-supervisor
*/5 * * * *     /opt/mcp-env/bin/xcelium-mcp-culler
```

- `@reboot`: 호스트 재부팅 시 기동(사실상 거의 발생 안 함 — 아래 매분 라인이 실질적 워치독).
- `* * * * *`(매분): `flock -n`이 이미 실행 중이면(락 보유 중) 즉시 종료 → 사실상 "죽어 있을 때만 재기동"하는 워치독으로 동작. 최악의 경우 죽은 뒤 최대 1분 내 재기동.
- idle-culler는 5분 간격 폴링 — `/proc` 관찰 기반(§4.2)이라 폴링 주기가 곧 "브릿지 미연결 워커가 방치되는 최대 오차 시간"이 됨. 필요 시 더 좁힐 수 있음.

### 7.3 승격 배포안 — systemd --user (admin이 `loginctl enable-linger hoseung.lee`를 실행한 이후)

```ini
# ~/.config/systemd/user/xcelium-mcp-supervisor.service (사용자 홈, root 불필요 — linger만 켜지면 등록 가능)
[Service]
ExecStart=/opt/mcp-env/bin/xcelium-mcp-supervisor
Restart=on-failure
UMask=0177

# ~/.config/systemd/user/xcelium-mcp-culler.timer
[Timer]
OnUnitActiveSec=5min
```

전환 시 §7.2의 crontab 라인은 제거하고 `systemctl --user enable --now xcelium-mcp-supervisor.service xcelium-mcp-culler.timer`로 대체한다. **이 승격은 이 저장소의 구현 범위가 아니며, admin이 linger를 켜준 뒤 운영 가이드로만 제공한다.**

### 7.4 클라이언트 측 `~/.claude.json` (각 사용자 로컬 설정, 이 저장소 범위 밖)

```json
{"type": "stdio", "command": "ssh", "args": [
  "-o", "BatchMode=yes",
  "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
  "cloud0",
  "/opt/mcp-env/bin/python", "-m", "xcelium_mcp.stdio_forward",
  "/home/hoseung.lee/.xcelium_mcp/run/xcelium-mcp.sock"]}
```

> `socat` 대신 §5.2의 순수 Python 포워더를 사용 — 로컬/원격 어디에도 새 시스템 패키지 설치가 필요 없다.
>
> **`ServerAliveInterval=30`/`ServerAliveCountMax=3`(0.3 신규, §1.4)**: 로컬 ssh 클라이언트가 30초마다 살아있는지 확인하고, 3회(90초) 응답이 없으면 **로컬 ssh 스스로 연결을 끊는다** — `sshd`의 `ClientAliveInterval`(root 필요, Plan §F-B에서 이미 범위 밖으로 뺌)과 달리 클라이언트 쪽 설정이라 root가 전혀 필요 없다. 이 옵션이 "네트워크가 말없이 죽는" 케이스(Plan §1.2)를 EOF로 바꿔주면, 워커는 기존 SDK 동작(stdin EOF → 정상 종료)만으로 스스로 정리된다 — 워커 하트비트가 더 이상 필요 없는 이유(§1.4).

---

## 8. Test Plan

> Plan.md §5 T-1~T-6을 구현 단위로 분해. **중요 제약**: `socketserver.ForkingMixIn`/`os.fork()`는 POSIX 전용이라 이 프로젝트의 로컬 개발 환경(Windows)에서는 직접 실행할 수 없다 — 수퍼바이저/idle-culler 단위 테스트는 `sys.platform != "win32"`로 skip 처리하고, 실제 검증은 **cloud0(CentOS 7, systemd 219, root 권한 없음)** SSH 세션에서 §7.2 cron 배포안 그대로 수행한다(§1.3에서 실측한 동일 환경·동일 권한 제약 하에서 검증 — "Linux면 될 것"이라는 가정이 아니라 실제 cloud0에서). 기존 472개 pytest(MockTclServer 기반, transport 무관)는 영향 없음.

| # | 테스트 | 대상 | 환경 |
|---|---|---|---|
| T-1 | 동일 클라이언트 5회 연속 연결/해제 → 워커 수가 동시 연결 수 이상 누적되지 않음 | `supervisor.py` | cloud0(§7.2 cron 배포) |
| T-2 | 클라이언트 쪽 네트워크를 방화벽 규칙 등으로 말없이 끊음(network black hole 재현) → `ServerAliveCountMax`×`ServerAliveInterval` 경과 후 로컬 ssh가 스스로 종료 → 원격 워커도 EOF로 스스로 종료(하트비트 없이) | 클라이언트 ssh 옵션(§7.4) + 기존 SDK EOF 처리 | cloud0(+ 로컬 ssh 클라이언트) |
| T-3 | idle(브릿지 미연결) 상태로 age 임계값 경과 → idle-culler가 `/proc` 관찰만으로 해당 워커를 종료, 수퍼바이저·다른 워커 영향 없음 | `idle_culler.py` | cloud0 |
| T-4 | 서로 다른 sim_dir 2개에서 `sim_bridge_run` 후 각각 `connect_simulator(sim_dir=...)` | `bridge_lifecycle.py`+`registry.py` | pytest(MockTclServer, OS 무관) |
| T-5 | sim_dir 없이 `connect_simulator(auto)` 호출 시 브릿지 ≥2개 → 모호성 에러 | `sim_lifecycle.py` | pytest(OS 무관) |
| T-6 | 회귀: 기존 26개 MCP tool이 fork 워커 위에서도 동일 동작 | 전체 | 기존 pytest 스위트(무변경) + cloud0 수동 스모크 |
| T-7(신규) | 수퍼바이저를 `kill -9`로 강제 종료 후 최대 1분 대기 → cron `* * * * *` 워치독이 `flock` 락 해제를 감지하고 자동 재기동 | `supervisor.py`+crontab(§7.2) | cloud0 |
| T-8(신규) | `python -m xcelium_mcp.stdio_forward <sock>`을 cloud0에서 직접 실행 → 소켓 연결 및 양방향 바이트 중계 확인(socat 없이) | `stdio_forward.py` | cloud0 |

---

## 9. Security Considerations

- unix domain socket(`$HOME/.xcelium_mcp/run/xcelium-mcp.sock`)은 supervisor.py가 생성 직후 `os.chmod(sock_path, 0o600)`으로 소유자만 접근 가능하도록 명시적으로 강제한다 — §1.3에서 SELinux가 `disabled`임을 확인했으므로 MAC 정책에 기대지 않고 DAC(unix 퍼미션)만으로 방어선을 만든다.
- `$HOME/.xcelium_mcp/`는 홈 디렉토리 기본 퍼미션(통상 700)에 이미 의존하고 있었다(기존 `bridge_ready_*` 등과 동일 전제) — 소켓도 같은 경계 안에 있어 새로운 권한 경계를 만들지 않는다. idle-culler의 `/proc/<pid>/...` 조회도 리눅스 기본 정책상 본인 소유 프로세스만 읽을 수 있어 별도 권한 경계가 생기지 않는다.
- **root 관련**: 이 기능의 구현·배포 전체가 root/sudo/polkit 없이 완결되도록 설계했다(§7.2) — 이 프로젝트의 "root 소유 파일 직접 수정 금지" 운영 규칙과 일치.

---

## 10. Implementation Guide

### 10.1 File Structure

```
src/xcelium_mcp/
├── supervisor.py       (신규) — ForkingMixIn 수퍼바이저 + WorkerHandler(lifecycle 계측 없음, §1.4)
├── stdio_forward.py     (신규) — socat 대체, 순수 stdlib stdin/stdout↔unix socket 중계
├── idle_culler.py       (신규) — /proc 순수 관찰 기반 idle 워커 탐지·정리
├── registry.py          (수정) — update_bridge_port/get_bridge_port 추가
├── bridge_lifecycle.py  (수정) — 연결 성공 시 레지스트리 write-back
├── bridge_manager.py    (수정, 소폭) — 필요 시 헬퍼 노출
├── tools/sim_lifecycle.py (수정) — connect_simulator sim_dir 우선 조회 + fail-loud
└── server.py             (무변경)
```

### 10.2 Implementation Order

1. [ ] `registry.py`: `update_bridge_port`/`get_bridge_port` 추가 + 단위 테스트(T-4/T-5 선행 조건)
2. [ ] `bridge_lifecycle.py`: `_start_bridge` 연결 성공 경로 2곳에 write-back 추가
3. [ ] `tools/sim_lifecycle.py`: `connect_simulator` sim_dir 우선 조회 + auto 모호성 에러(T-4/T-5)
4. [ ] `stdio_forward.py`: 순수 stdlib 포워더(독립적, cloud0에서 socat 없이 직접 실행해 검증 가능 — T-8)
5. [ ] `supervisor.py`: ForkingMixIn 수퍼바이저 + WorkerHandler(§1.4 — 계측 코드 없음), 소켓 경로 `$HOME/.xcelium_mcp/run/`(Linux 전용, cloud0에서 검증)
6. [ ] `idle_culler.py`: `/proc` 기반 워커 pid·TCP 상태·age 판정 + kill 로직(§4.2/§5.3, Linux 전용)
7. [ ] `pyproject.toml`: entry point(`xcelium-mcp-supervisor`, `xcelium-mcp-culler`) 추가
8. [ ] 배포: §7.2 crontab 라인 등록(cloud0, root 불필요) + 클라이언트 `~/.claude.json`에 `ServerAliveInterval`/`ServerAliveCountMax` 포함해 교체 가이드 작성(§7.4)
9. [ ] 회귀: 기존 pytest 스위트 전체 통과 확인(T-6) + cloud0 스모크(T-1,T-2,T-7,T-8)

### 10.3 Session Guide

| Module | Scope Key | Description | 비고 |
|--------|-----------|-------------|------|
| F-C (attach 모호성) | `module-1` | registry.py + bridge_lifecycle.py + sim_lifecycle.py | 플랫폼 무관, 기존 pytest로 검증 가능 — 먼저 진행 권장 |
| F-A (수퍼바이저) | `module-2` | supervisor.py + stdio_forward.py | Linux 전용, cloud0 접속 필요. socat/systemd/하트비트 불필요(§1.3/§1.4) |
| F-B (idle-culler) | `module-3` | idle_culler.py + crontab 등록(§7.2) | module-2 이후, Linux 전용, root 불필요, 워커 계측 불필요 |
| 배포 | `module-4` | entry point, crontab 등록, 클라이언트 설정(ssh keepalive 포함) 가이드 | module-2/3 완료 후 |

#### Recommended Session Plan

| Session | Phase | Scope | 비고 |
|---------|-------|-------|------|
| Session 1 | Do | `--scope module-1` | 로컬(Windows)에서 pytest로 완결 가능 |
| Session 2 | Do | `--scope module-2,module-3` | cloud0 SSH 세션 필요, root 불필요(§1.3 실측 확인) |
| Session 3 | Do | `--scope module-4` + Check | crontab 등록·스모크 테스트, 회귀 스위트 전체 실행 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-07 | 초안 — Plan §2 안C+ 결정을 구체화, Checkpoint 3(수퍼바이저 구현 방식 A/B/C)에서 `socketserver.ForkingMixIn` 채택. F-C는 `registry.py`의 기존 `bridge_port` 필드 재사용으로 스코프 축소. `server.py` 무변경(하트비트는 supervisor.py에서 스레드로 처리)으로 Plan.md 대비 회귀 리스크 추가 절감. | hoseung.lee |
| 0.2 | 2026-07-07 | **cloud0 실측 반영(§1.3)** — SSH로 직접 확인한 결과 CentOS7 `systemd 219`, `Linger=no`, `systemctl --user` D-Bus 세션 없음, 비대화형 `loginctl enable-linger` 불가(polkit), `sudo -n` 불가(비밀번호 필요), `socat` 미설치를 확인. 이에 따라 (1) 기본 배포안을 root/admin 개입 없는 **cron 워치독**으로 변경, systemd --user는 admin이 linger를 켜준 뒤의 승격안으로 격하(§7), (2) `socat` 의존 제거하고 순수 stdlib `stdio_forward.py`로 대체(§5.2), (3) 소켓/하트비트 경로를 `/run/user/$UID/`에서 `$HOME/.xcelium_mcp/run/`으로 이동(linger 꺼진 상태의 tmpfs 정리 위험 회피). SELinux는 `disabled` 확인되어 관련 caveat 제거. `/opt/mcp-env/bin/python` 3.10.8 확인되어 신규 인터프리터 불필요. Test Plan에 T-7(워치독 재기동)/T-8(포워더 단독 검증) 추가. | hoseung.lee |
| 0.3 | 2026-07-07 | **하트비트 파일 제거(§1.4)** — 사용자 리뷰("주기적 touch가 낭비")를 계기로 재검토한 결과, 15초 주기 무조건 touch가 F-B(idle 정리)의 "활동 없음 N시간" 판정을 구조적으로 무력화하는 버그였음을 확인. `heartbeat.py`를 완전히 삭제하고 두 목적을 분리: (1) 죽은/방치된 연결 탐지는 클라이언트 측 SSH `ServerAliveInterval`/`ServerAliveCountMax`(root 불필요)로 대체 — MCP SDK 소스(`mcp/server/stdio.py`) 확인 결과 stdin EOF 시 워커가 이미 스스로 정상 종료함을 검증, (2) idle 워커 정리는 `/proc/<supervisor_pid>/task/*/children` + 워커별 established TCP 유무 + 프로세스 age를 idle-culler가 순수 관찰(신규 파일/스레드 없음)해 판단하도록 변경. `WorkerHandler`는 이제 `dup2` 두 줄 + 기존 `server.main()` 호출뿐 — 워커 프로세스 안에 lifecycle 관련 코드가 전혀 없다. Test Plan T-2를 하트비트 타임아웃 검증에서 network-black-hole(ssh keepalive) 검증으로 교체. | hoseung.lee |
