"""
轻量 MCP Server —— 按 MCP 规范自实现协议层,零重依赖,可在 LoongArch 原生运行。

实现的标准方法(JSON-RPC 2.0):
  initialize   握手,声明协议版本与能力
  tools/list   列出可用工具及其 inputSchema
  tools/call   调用某个工具

安全设计:tools/call 调用"变更类"工具时,服务端会【独立再过一次安全护栏】。
这就是"双手不盲信大脑"——即便编排器(client)漏校验,执行端自身也守得住,纵深防御。
只读工具无副作用,直接执行。

可作为独立进程跑(stdio 传输):  python3 mcp_server.py  然后逐行喂 JSON-RPC。
"""

import json
import os
import sys

import perception

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "guardrail"))
from guardrail import load_config, verify  # noqa: E402


class MCPServer:
    PROTOCOL = "2024-11-05"

    def __init__(self, root: str = _HERE):
        self.tools, self.assets, self.rules = load_config(os.path.join(root, "guardrail"))

    def _tool_list(self):
        out = []
        for t in self.tools.values():
            schema = {"type": "object", "properties": {}, "required": []}
            ta = t.get("target_arg")
            if ta:
                schema["properties"][ta] = {"type": "string"}
                schema["required"] = [ta]
            out.append({
                "name": t["name"],
                "description": f"[{t['category']}] op={t.get('op')} risk={t.get('risk_base')}",
                "inputSchema": schema,
            })
        return out

    def handle(self, req: dict) -> dict:
        """处理一条 JSON-RPC 2.0 请求,返回响应。"""
        mid, method = req.get("id"), req.get("method")
        params = req.get("params") or {}
        try:
            if method == "initialize":
                res = {"protocolVersion": self.PROTOCOL, "capabilities": {"tools": {}},
                       "serverInfo": {"name": "ops-agent-mcp", "version": "0.1"}}
            elif method == "tools/list":
                res = {"tools": self._tool_list()}
            elif method == "tools/call":
                res = self._call(params.get("name"), params.get("arguments") or {})
            else:
                return _err(mid, -32601, f"method not found: {method}")
            return {"jsonrpc": "2.0", "id": mid, "result": res}
        except Exception as e:  # noqa: BLE001
            return _err(mid, -32603, str(e))

    def _call(self, name, args):
        tool = self.tools.get(name)
        if tool is None:
            return _content(f"未注册工具: {name}", is_error=True)

        # 只读工具:直接执行
        if tool["category"] == "readonly":
            try:
                out = perception.run_tool(name, args)
            except KeyError:
                out = {"note": f"只读工具 {name} 原型期未模拟"}
            return _content(json.dumps(out, ensure_ascii=False))

        # 变更工具:服务端独立再过护栏
        call = {"call_id": "mcp", "tool": name, "arguments": args, "reason": "via MCP tools/call"}
        v = verify(call, self.tools, self.assets, self.rules)

        if v["decision"] == "deny":
            txt = f"【护栏拒绝】{v['message']}" + (f" 建议: {v['suggestion']}" if v["suggestion"] else "")
            return _content(txt, is_error=True, verdict=v)
        if v["decision"] == "require_confirm" and not args.get("_confirmed"):
            return _content(f"【需确认】{v['message']} 前置: {v['requires']}", is_error=True, verdict=v)
        # allow / warn / 已确认 → 执行(原型:模拟执行,正式版交给最小权限执行器)
        return _content(f"已执行 {name}({args})[原型模拟],裁决={v['decision']}", verdict=v)


def _content(text, is_error=False, verdict=None):
    r = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if verdict is not None:
        r["verdict"] = verdict
    return r


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _stdio_main():
    """stdio 传输:逐行读 JSON-RPC 请求,逐行写响应。"""
    srv = MCPServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        sys.stdout.write(json.dumps(srv.handle(json.loads(line)), ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    _stdio_main()
