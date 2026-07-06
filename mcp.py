"""
MCP (Model Context Protocol) 服务端 —— 纯标准库实现
Streamable HTTP 传输，JSON-RPC 2.0 协议
"""
import json
import uuid
from tools import execute_tool, TOOLS

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "DocuStack-mcp"
SERVER_VERSION = "1.0.0"

# 会话管理
_sessions = {}

# ── 工具 schema 转换 ──


def _tool_input_schema(tool_id: str, tool_info: dict) -> dict:
    """把 TOOLS 的 params 描述转为 MCP inputSchema"""
    schema = {"type": "object", "properties": {}}
    for k, desc in tool_info.get("params", {}).items():
        schema["properties"][k] = {"type": "string", "description": str(desc)}
    return schema


def _list_tools() -> list[dict]:
    """返回 MCP 格式的工具列表"""
    tools = []
    for tid, info in TOOLS.items():
        tools.append({
            "name": tid,
            "description": info["desc"],
            "inputSchema": _tool_input_schema(tid, info),
        })
    return tools


# ── JSON-RPC 路由 ──


def handle(request: dict, idx: dict = None) -> dict | None:
    """
    处理 JSON-RPC 请求，返回响应 dict。
    如果是 notification（无 id），返回 None。
    """
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})
    is_notification = req_id is None

    if method == "initialize":
        # 协议握手
        proto = params.get("protocolVersion", PROTOCOL_VERSION)
        sid = str(uuid.uuid4())
        _sessions[sid] = {"protocol": proto, "initialized": False}
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
            "_sessionId": sid,
        })

    if method == "notifications/initialized":
        sid = params.get("_sessionId", "") or request.get("_sessionId", "")
        if sid and sid in _sessions:
            _sessions[sid]["initialized"] = True
        return None  # notification, no response

    if method == "tools/list":
        return _ok(req_id, {"tools": _list_tools()})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in TOOLS:
            return _err(req_id, -32602, f"未知工具: {tool_name}")

        try:
            result = execute_tool(tool_name, arguments, index=idx)
            if result["ok"]:
                content = [{"type": "text", "text": result["result"]}]
                return _ok(req_id, {"content": content})
            else:
                return _ok(req_id, {
                    "content": [{"type": "text", "text": result["result"]}],
                    "isError": True,
                })
        except Exception as e:
            return _err(req_id, -32603, f"工具执行异常: {e}")

    return _err(req_id, -32601, f"未知方法: {method}")


# ── 响应构建 ──


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
