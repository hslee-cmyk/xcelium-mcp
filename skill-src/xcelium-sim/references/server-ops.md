# Ops — 원격 Supervisor 코드 반영 확인 + 재기동

## 언제 쓰나

- 최근 xcelium-mcp에 커밋/배포한 fix가 반영 안 된 것처럼 동작할 때
- MCP tool 호출이 응답 없음/timeout이거나 이전 버전 동작을 그대로 보일 때
- "재기동", "supervisor", "연결 안 됨", "최신 코드 반영 안 됨" 같은 키워드가 대화에 등장할 때

이 문서는 **특정 호스트를 전제하지 않는다** — 실제 접속 정보(SSH alias, host, 배포 경로)는
이 skill이 아니라 프로젝트별 CLAUDE.md/설정 또는 사용자에게 확인한다.

## 배경 (구조)

xcelium-mcp는 원격 시뮬레이션 서버에 상주하는 prefork supervisor(`xcelium_mcp.supervisor`,
`socketserver.ForkingMixIn` 기반)를 통해 MCP 세션을 처리한다
(설계: `docs/02-design/features/xcelium-mcp-server-process-lifecycle.design.md`).

supervisor는 최초 기동 시 `xcelium_mcp.server` 모듈을 1회 import하고, 이후 각 연결마다
fork하여 그 임포트된 코드의 복사본으로 세션을 처리한다. editable install(`pip install -e`)이라
디스크의 코드는 `git pull` 즉시 최신이 되지만, **이미 떠 있는 supervisor 프로세스는 재기동
전까지 import 시점의 코드를 메모리에 그대로 들고 있다** — fork는 재-import가 아니라 부모의
기존 메모리 상태를 복사하는 것이므로, 방금 fork된 워커라도 예외 없이 구코드로 동작한다.

재기동 자체는 원격 host별 배포 방식에 따라 다르게 관리될 수 있으나, 이 저장소가 실제로
검증한 기본 배포 방식(`deploy/README.md`, `deploy/crontab.example`)은 **cron 워치독 + flock**
기반이다:

```
* * * * *   /usr/bin/flock -n $HOME/.xcelium_mcp/run/supervisor.lock python3 -m xcelium_mcp.supervisor
```

`flock -n`(non-blocking)이므로 supervisor가 이미 떠서 lock을 쥐고 있으면 매분 실행되는 이
라인은 즉시 스킵되고, lock이 풀려야만(=supervisor가 죽어야만) 다음 tick에서 새 프로세스가
뜬다.

## 1. 코드 최신 반영 여부 확인

원격 host의 xcelium-mcp 소스 경로에서:

```bash
cd <remote xcelium-mcp source dir> && git log -1 --oneline
```

로컬 `git log -1 --oneline`과 비교해 커밋 해시가 같은지 먼저 확인한다. 다르면 원격에
`git pull`이 필요하다.

**커밋이 같아도 안심할 수 없다** — supervisor 프로세스의 시작 시각과 최신 커밋 시각을
반드시 함께 비교한다:

```bash
ps -eo pid,lstart,cmd | grep xcelium_mcp.supervisor | grep -v grep
git log -1 --format='%ad' --date=iso
```

프로세스 시작 시각이 최신 커밋 시각보다 **이전**이면, 디스크 코드는 최신이어도 실행 중인
프로세스는 구코드를 메모리에 들고 있는 상태 — 재기동이 필요하다.

## 1.5 활성 브릿지 세션 확인 — registry가 stale할 수 있다

`list_active_sessions()`(파라미터 없음, 읽기 전용)로 현재 등록된 모든 bridge 세션
(`connect_simulator`/`sim_bridge_run`으로 연결된 xmsim/SimVision)을 조회할 수 있다:

```python
list_active_sessions()
# → "/path/to/sim_dir  port=9876  test='...'  TTL remaining: 47.8h"
```

**registry 항목이 있다고 실제 프로세스가 살아있다는 뜻은 아니다.** TTL이 한참 남아있어도
그 뒤에 있어야 할 xmsim/SimVision 프로세스가 이미 죽어있을 수 있다(실측 사례, 2026-07-08:
registry에 TTL 47.8h 남은 세션이 있었지만, 원격 host에서 `ps -ef`로 확인하니 그 프로세스
자체가 존재하지 않았다 — `connect_simulator`로 재연결을 시도하면 `Connect call failed`로
실패한다).

**stale 여부 판단 절차**:

1. `list_active_sessions()`로 registry에 등록된 후보 목록 확보
2. 각 항목의 sim_dir/port를 원격 host에서 직접 교차 검증:
   ```bash
   ps -ef | grep -iE 'xrun|xmsim|simvision' | grep -v grep
   ss -tlnp | grep <port>
   ```
3. registry에는 있는데 실제 프로세스가 없으면 → stale entry. 그 sim_dir로 다시 붙으려면
   `connect_simulator`가 아니라 `sim_bridge_run`으로 새로 기동해야 한다.
4. registry에도 있고 실제 프로세스도 살아있으면 → 그 세션을 그대로 재사용해 재현/검증을
   진행할 수 있다.

**재기동 맥락에서 이 확인이 필요한 이유**: supervisor 재기동 여부를 판단하거나 재기동 후
영향을 설명할 때, "지금 이 host에 살아있는 시뮬레이션이 있는지"를 먼저 파악해두면 (a) 재기동이
그 세션의 워커에 실제로 영향을 주는지(§3 참조 — 이미 fork된 워커는 영향받지 않고 계속
구코드로 실행) 정확히 설명할 수 있고, (b) 코드 수정을 실제 배포까지 검증하려 할 때 새 세션을
기동해야 하는지 기존 세션을 재사용해도 되는지 미리 판단할 수 있다.

## 2. 재기동

```bash
pkill -f xcelium_mcp.supervisor
```

- 이 한 줄이면 충분하다. `flock` 래퍼 프로세스와 실제 python 프로세스 모두 cmdline에
  `xcelium_mcp.supervisor` 문자열을 포함하므로 함께 종료되어 lock이 확실히 풀린다.
- cron 워치독이 다음 tick(최대 1분)에 lock을 획득해 새 supervisor를 기동하고, 이때 최신
  디스크 코드를 새로 import한다.
- `supervisor.py`의 `_prepare_socket_path()`가 기동 시 stale 소켓 파일을 자동으로 지우므로
  별도 cleanup은 필요 없다.
- 별도의 "정상 종료" 시퀀스(SIGTERM 핸들러 등)는 존재하지 않는다 — kill 후 워치독이 재기동하는
  방식이 이 배포의 설계상 공식 절차다(`deploy/README.md` §4, 실측 테스트 T-7로 검증됨).

## 3. 주의사항

- **재기동은 상태를 변경하는 작업이다 — 실행 전 사용자 승인을 받는다.** 다른 세션이 이
  supervisor를 통해 활성 시뮬레이션을 제어하고 있을 수 있다.
- 재기동 시점에 이미 fork되어 연결을 처리 중인 워커(활성 세션)는 영향을 받지 않고 **계속
  구코드로 실행된다** — 새 코드는 재기동 이후 새로 맺어지는 연결부터 적용된다.
- 재기동 후에는 그 supervisor에 연결하던 각 MCP 클라이언트(Claude Code/Desktop) 쪽에서
  재연결(`/mcp reconnect` 또는 세션 재시작)이 필요하다 — 그렇지 않으면 클라이언트가 죽은
  연결을 계속 붙들고 있거나, 재기동 전 상태로 검증을 진행하게 된다.
- 배포 방식이 cron 워치독이 아니라 다른 방식(예: systemd)으로 바뀐 host라면 kill 절차 자체는
  유효하지만(자식 프로세스 재기동 트리거), "몇 초/분 내 재기동"이라는 타이밍 전제는 그
  host의 워치독 주기에 맞게 다시 확인해야 한다.

## 관련 문서

- `deploy/README.md` — 배포·검증 절차 원본 (T-1/T-2/T-7/T-8)
- `deploy/crontab.example` — 워치독 crontab 라인
- `docs/02-design/features/xcelium-mcp-server-process-lifecycle.design.md` — supervisor 설계 배경
- `src/xcelium_mcp/supervisor.py` — 실제 구현
