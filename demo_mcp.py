"""MCP 协议演示:initialize / tools/list / tools/call,服务端护栏拦截,两种传输。"""
import json
import os

from mcp_client import MCPClient, InProcessTransport, StdioTransport
from mcp_server import MCPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 1) 进程内传输:走完整协议流程 ----
client = MCPClient(InProcessTransport(MCPServer()))

print("=== initialize 握手 ===")
print(" ", client.initialize())

print("\n=== tools/list 列出工具 ===")
for t in client.list_tools():
    print(f"  {t['name']:<16} {t['description']}")

print("\n=== tools/call 只读工具(直接执行)===")
r = client.call_tool("get_disk_usage")
print("  get_disk_usage →", r["content"][0]["text"])

print("\n=== tools/call 删 binlog —— 看原始 JSON-RPC 报文 ===")
srv = MCPServer()
req = {"jsonrpc": "2.0", "id": 99, "method": "tools/call",
       "params": {"name": "delete_file", "arguments": {"path": "/var/lib/mysql/binlog.001"}}}
print("  → 请求:", json.dumps(req, ensure_ascii=False))
resp = srv.handle(req)
print("  ← 响应:", json.dumps(resp, ensure_ascii=False))
print(f"  [服务端护栏在协议边界拦截了删除,isError={resp['result']['isError']}]")

# ---- 2) stdio 传输:把 server 当独立子进程,验证标准本地传输也通 ----
print("\n=== stdio 子进程传输验证 ===")
tr = StdioTransport(["python3", os.path.join(HERE, "mcp_server.py")])
sclient = MCPClient(tr)
print("  子进程 initialize →", sclient.initialize()["serverInfo"])
r = sclient.call_tool("delete_file", {"path": "/var/lib/mysql/binlog.001"})
print("  子进程删 binlog →", r["content"][0]["text"])
tr.close()
print("\n结论:协议握手、工具发现、工具调用全通;变更操作在服务端被独立护栏拦截。")
