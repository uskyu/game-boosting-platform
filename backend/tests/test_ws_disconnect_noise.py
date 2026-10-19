"""WebSocket 认证阶段断连不得重复发 close 帧（生产日志噪音修复）。

线上真实日志（2026-10-20 国内生产）：

    File "/app/app/api/endpoints/chat.py", line 227, in chat_websocket
        auth_payload = await asyncio.wait_for(...)
    starlette.websockets.WebSocketDisconnect: (1005, None)
    During handling of the above exception, another exception occurred:
    RuntimeError: Unexpected ASGI message 'websocket.close', after sending
    'websocket.close' or response already completed.

成因：认证阶段把 ``WebSocketDisconnect`` 和「超时/格式错」放进同一个 except，
然后统一调 ``websocket.close()``。客户端已经断开时连接已关，再发 close 帧
会让 Starlette 抛 RuntimeError，把正常断线刷成 ERROR + 完整堆栈，
干扰真正的故障排查（本次就是排查时被它误导了一轮）。

修复后：断连单独捕获、直接 return；只有仍在线（超时/格式错）才发 close 帧。
"""

from pathlib import Path

import pytest

CHAT_SOURCE = Path(__file__).resolve().parent.parent / "app" / "api" / "endpoints" / "chat.py"


@pytest.fixture(scope="module")
def source() -> str:
    return CHAT_SOURCE.read_text(encoding="utf-8")


def _auth_phase(source: str) -> str:
    """截取 WebSocket 认证阶段（Phase 1）那段代码。"""
    start = source.index("Phase 1: authenticate via first message")
    end = source.index("Phase 2: normal message loop", start)
    return source[start:end]


def test_disconnect_is_handled_separately_from_timeout(source: str):
    """WebSocketDisconnect 必须有独立的 except 分支，不能和超时混在一起。"""
    phase = _auth_phase(source)
    assert "except WebSocketDisconnect:" in phase, "缺少独立的 WebSocketDisconnect 分支"
    # 独立的断连分支里只能 return，不能**调用** websocket.close(...)
    disconnect_branch = phase.split("except WebSocketDisconnect:", 1)[1]
    disconnect_branch = disconnect_branch.split("except ", 1)[0]
    # 只看代码行，不看注释（注释里会提到这个报错文案）
    code_lines = [
        ln for ln in disconnect_branch.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "websocket.close(" not in code, (
        "客户端已断连时不能再调用 websocket.close()（会触发 Unexpected ASGI message）"
    )
    assert "await websocket.close(" not in code
    assert "return" in code


def test_timeout_still_closes_with_policy_violation(source: str):
    """超时/格式错仍要正常回策略性关闭帧，别把这条路径一起改没了。"""
    phase = _auth_phase(source)
    assert "except (asyncio.TimeoutError, ValueError):" in phase
    assert "WS_1008_POLICY_VIOLATION" in phase


def test_disconnect_no_longer_uses_the_combined_handler(source: str):
    """回归守卫：不允许再出现「三合一」的 except（老写法）。"""
    phase = _auth_phase(source)
    assert "asyncio.TimeoutError, WebSocketDisconnect, ValueError" not in phase, (
        "认证阶段的断连不能和超时合并处理"
    )


def test_phase_two_keeps_its_own_disconnect_guard(source: str):
    """第二阶段的 close 已包在 try/except 里，别在本次修复中被误删。"""
    phase2 = source[source.index("Phase 2: normal message loop"):]
    assert "except WebSocketDisconnect as exc:" in phase2
    # 心跳超时那条 close 必须在 try 里
    timeout_block = phase2.split("except asyncio.TimeoutError:", 1)[1].split("except WebSocketDisconnect", 1)[0]
    assert "websocket.close" in timeout_block
    assert "try:" in timeout_block
