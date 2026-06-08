"""
Web 后端 (B/S 的 S) —— 用 Python 标准库起 HTTP 服务,零重依赖,LoongArch 友好。
  GET  /            返回单页控制台
  POST /api/chat    body {message}; 返回 {reply, chain, verdict, status, audit_*}

把编排器(已接 MCP 双手 + 审计)包一层暴露为接口,前端纯静态 HTML 调用即可。
真机部署同样:python3 web_server.py,浏览器访问。
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from audit import AuditLog
from mcp_client import MCPClient, InProcessTransport
from mcp_server import MCPServer
from orchestrator import Orchestrator
from llm_client import DeepSeekClient, RuleBasedClient

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "web", "index.html")

# 大脑选择:设了 DEEPSEEK_API_KEY 就用 DeepSeek(模型驱动),否则退回规则路由。
# DeepSeekClient 内置失败降级,模型偶发不可用时会自动退回规则路由,服务不中断。
if os.environ.get("DEEPSEEK_API_KEY"):
    _LLM = DeepSeekClient()
    print("大脑:DeepSeek(模型驱动)")
else:
    _LLM = RuleBasedClient()
    print("大脑:规则路由(未检测到 DEEPSEEK_API_KEY)")

AGENT = Orchestrator(
    llm=_LLM,
    mcp_client=MCPClient(InProcessTransport(MCPServer())),
    audit=AuditLog(os.path.join(HERE, "web_audit.jsonl")),
)


def derive_status(chain, verdict):
    """为前端归纳一个总体状态,便于渲染裁决横幅。"""
    for stage, detail in chain:
        if stage == "注入检查" and "检出疑似" in str(detail):
            return "blocked", "提示词注入已拦截"
    if verdict:
        return {
            "deny": ("blocked", "危险操作已拦截"),
            "require_confirm": ("confirm", "高风险 · 待人工确认"),
            "allow": ("ok", "已放行执行"),
            "warn": ("ok", "已执行并记录"),
        }.get(verdict.get("decision"), ("info", "已处理"))
    return "info", "已处理"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(INDEX, encoding="utf-8") as f:
                self._send(200, f.read(), "text/html")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/chat":
            self._send(404, "{}")
            return
        n = int(self.headers.get("Content-Length", 0))
        msg = json.loads(self.rfile.read(n) or "{}").get("message", "")
        r = AGENT.handle(msg)
        status, label = derive_status(r["chain"], r.get("verdict"))
        self._send(200, json.dumps({
            "reply": r["reply"],
            "chain": [{"stage": s, "detail": str(d)} for s, d in r["chain"]],
            "verdict": r.get("verdict"),
            "status": status,
            "status_label": label,
            "audit_seq": r.get("audit_seq"),
            "audit_hash": r.get("audit_hash"),
        }, ensure_ascii=False))

    def log_message(self, *a):  # 静音访问日志
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"运维 Agent 控制台已启动:http://127.0.0.1:{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
