"""
轻量 MCP Client —— 编排器用它通过 MCP 协议调用工具。

两种传输可选,接口一致:
  InProcessTransport : 进程内直连 server(集成演示用,快)
  StdioTransport     : 把 server 当独立子进程,经标准输入输出通信(MCP 标准本地传输)
切换传输不改业务代码,这正是协议解耦的价值。
"""

import itertools
import json
import subprocess


class InProcessTransport:
    def __init__(self, server):
        self.server = server

    def send(self, req: dict) -> dict:
        return self.server.handle(req)


class StdioTransport:
    def __init__(self, cmd: list):
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8")

    def send(self, req: dict) -> dict:
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def close(self):
        self.proc.stdin.close()
        self.proc.terminate()


class MCPClient:
    def __init__(self, transport):
        self.t = transport
        self._id = itertools.count(1)

    def _rpc(self, method, params=None):
        req = {"jsonrpc": "2.0", "id": next(self._id), "method": method, "params": params or {}}
        resp = self.t.send(req)
        if "error" in resp:
            raise RuntimeError(f"MCP 错误 {resp['error']['code']}: {resp['error']['message']}")
        return resp["result"]

    def initialize(self):
        return self._rpc("initialize")

    def list_tools(self):
        return self._rpc("tools/list")["tools"]

    def call_tool(self, name, arguments=None):
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
