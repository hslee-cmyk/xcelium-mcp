# SimVision 연동 테스트 절차

## 1단계: SimVision 측 (Linux 시뮬레이션 서버)

**Tcl 브릿지 로드:**
```bash
# 방법 A: xrun 실행 시 자동 로드
xrun -gui -input "@simvision {source /path/to/xcelium-mcp/tcl/mcp_bridge.tcl}" design.v

# 방법 B: SimVision 콘솔에서 수동 로드
simvision% source /path/to/mcp_bridge.tcl
```

성공 시 콘솔에 `MCP Bridge: listening on port 9876` 출력.

**포트 변경이 필요한 경우:**
```bash
export MCP_BRIDGE_PORT=9877
```

## 2단계: TCP 연결 확인 (netcat)

SimVision이 실행 중인 서버에서:
```bash
echo "__PING__" | nc localhost 9876
```

정상 응답:
```
OK 4
pong
<<<END>>>
```

직접 Tcl 명령도 테스트 가능:
```bash
echo "where" | nc localhost 9876
echo "scope" | nc localhost 9876
```

## 3단계: 원격 접속 시 SSH 터널

SimVision이 원격 Linux 서버에 있는 경우, 로컬 PC에서:
```bash
ssh -L 9876:localhost:9876 user@sim-server
```

이후 로컬에서 `localhost:9876`으로 접근 가능.

## 4단계: Python 클라이언트 직접 테스트

```python
import asyncio
from xcelium_mcp.tcl_bridge import TclBridge

async def test():
    bridge = TclBridge(host="localhost", port=9876)
    print(await bridge.connect())          # "pong"
    print(await bridge.execute("where"))   # 시뮬레이션 시간/위치
    print(await bridge.execute("scope"))   # 현재 스코프
    await bridge.disconnect()

asyncio.run(test())
```

## 5단계: Claude Desktop/Code에서 MCP 서버 등록

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "xcelium": {
      "command": "xcelium-mcp"
    }
  }
}
```

**Claude Code** (`.claude.json`):
```json
{
  "mcpServers": {
    "xcelium": {
      "command": "xcelium-mcp"
    }
  }
}
```

## 6단계: End-to-End 검증 순서

Claude에서 순서대로 호출:

| 순서 | Tool | 확인 사항 |
|------|------|-----------|
| 1 | `connect_simulator` | `ping=pong` + 현재 시뮬 위치 반환 |
| 2 | `sim_status` | Position, Scope 정상 출력 |
| 3 | `sim_run` (duration="100ns") | 시뮬레이션 전진 확인 |
| 4 | `get_signal_value` | 신호값 읽기 |
| 5 | `waveform_add_signals` | SimVision waveform에 신호 추가 확인 |
| 6 | `take_waveform_screenshot` | PNG 이미지 반환 (ghostscript 필요) |
| 7 | `run_debugger_mode` | 통합 리포트 + 스크린샷 |
| 8 | `disconnect_simulator` | 정상 해제 |

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `Connection refused` | 브릿지 미실행 또는 포트 불일치 | SimVision 콘솔에서 `source mcp_bridge.tcl` 재실행 |
| `Another client is connected` | 이전 연결 미해제 | SimVision 재시작 또는 이전 클라이언트 종료 |
| `Screenshot failed` | ghostscript/ImageMagick 미설치 | `sudo yum install ghostscript` 또는 `sudo apt install ghostscript` |
| `hardcopyPrint` 실패 | waveform 창이 열려 있지 않음 | SimVision에서 waveform 창 먼저 열기 |
| SSH 터널 후에도 연결 안 됨 | 터널 포트 불일치 | `ssh -L 9876:localhost:9876` 확인 |
