"""
编排器 (Orchestrator) —— Agent 的大脑。

一次 handle() 走完:接收指令 → 注入检查 → 意图路由(LLMClient)
→ 根因引擎/直接工具 → 安全护栏裁决 → 组织成人话回复。
全程把每一步记进 chain(思维链),对应评分项"推理链路溯源"。
"""

import glob
import os
import sys

import yaml

import injection_guard
from llm_client import RuleBasedClient
from rca_engine import diagnose

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "guardrail"))
from guardrail import load_config, verify  # noqa: E402


class Orchestrator:
    def __init__(self, root: str = _HERE, llm=None, audit=None, mcp_client=None):
        self.root = root
        self.gdir = os.path.join(root, "guardrail")
        self.tools, self.assets, self.rules = load_config(self.gdir)
        self.playbooks = [yaml.safe_load(open(p, encoding="utf-8"))
                          for p in glob.glob(os.path.join(root, "playbooks", "*.yaml"))]
        self.llm = llm or RuleBasedClient()   # 默认零依赖路由,可换 DeepSeekClient()
        self.audit = audit                    # 可选审计哈希链;传入则每次对话落一条记录
        self.mcp = mcp_client                 # 传入则工具经 MCP 协议调用(大脑→协议→双手)

    def handle(self, user_input: str) -> dict:
        """对外入口:跑完整链路,并把全过程落一条防篡改审计记录。"""
        result = self._handle(user_input)
        if self.audit is not None:
            v = result.get("verdict")
            rec = self.audit.append({
                "user_input": user_input,
                "chain": [[stage, str(detail)] for stage, detail in result["chain"]],
                "reply": result["reply"],
                "decision": (v or {}).get("decision", "n/a"),
            })
            result["audit_seq"], result["audit_hash"] = rec["seq"], rec["hash"][:12]
        return result

    def _handle(self, user_input: str) -> dict:
        chain = [("接收指令", user_input)]

        # 1) 入口注入检查
        scan = injection_guard.scan(user_input, source="user")
        chain.append(("注入检查", scan["reason"]))
        if scan["blocked"]:
            return self._result(
                "检测到疑似提示词注入,已拒绝执行,本次请求不会触达任何运维动作。",
                chain, verdict=None)

        # 2) 意图路由
        intent = self.llm.route(user_input, self.playbooks, self.tools)
        chain.append(("意图路由", f"{intent.get('type')} — {intent.get('why','')}"))

        if intent["type"] == "playbook":
            return self._handle_playbook(intent, chain)
        if intent["type"] == "tool":
            return self._handle_tool(self._mk_call(intent), chain)
        # chat:有边界的友好应答(介绍自己/引导回运维),由模型生成或固定兜底
        reply = self.llm.chat_reply(user_input) if hasattr(self.llm, "chat_reply") \
            else "你好,我是运维 Agent,可以帮你排查磁盘、进程、配置等系统问题。"
        return self._result(reply, chain)

    # --- 剧本路径:取证 → 推理 → 候选动作过护栏 ---
    def _handle_playbook(self, intent, chain):
        pb_path = os.path.join(self.root, "playbooks", f"{intent['id'].replace('-', '_')}.yaml")
        if not os.path.exists(pb_path):
            pb_path = self._find_playbook(intent["id"])
        res = diagnose(pb_path)
        for step in res["chain"]:
            chain.append(("感知取证", f"{step['tool']}{step['output']}"))
        concl = res["conclusion"]
        chain.append(("根因推理", f"{concl['root_cause']}(置信度 {concl['confidence']})"))

        call = res["remediation_call"]
        if not call:
            return self._result(f"诊断结论:{concl['root_cause']} 暂无可自动执行的修复。", chain)
        return self._handle_tool(call, chain, root_cause=concl["root_cause"])

    # --- 工具路径:有 MCP client 则经协议调用,否则本地直调(兼容) ---
    def _handle_tool(self, call, chain, root_cause=None):
        if self.mcp is not None:
            return self._handle_tool_via_mcp(call, chain, root_cause)

        v = verify(call, self.tools, self.assets, self.rules)
        chain.append(("安全校验", f"{v['decision']} / 风险 {v['risk_level']}"))

        target = call["arguments"].get("path") or call["arguments"].get("name") or ""
        prefix = f"诊断到根因「{root_cause}」," if root_cause else ""
        if v["decision"] == "deny":
            reply = (f"{prefix}本应执行 {call['tool']}({target}),但已被安全护栏拦截:"
                     f"{v['message']}。"
                     + (f"建议改用:{v['suggestion']}" if v["suggestion"] else ""))
        elif v["decision"] == "require_confirm":
            reply = (f"{prefix}{call['tool']}({target}) 属于高风险操作,需要你确认。"
                     f"原因:{v['message']};执行前会自动完成:{v['requires']}。回复『确认』继续。")
        else:
            reply = f"{prefix}已执行 {call['tool']}({target}) 并记录审计。"
        return self._result(reply, chain, verdict=v)

    # --- 经 MCP 协议把工具调用交给服务端(双手),由服务端护栏裁决并执行 ---
    def _handle_tool_via_mcp(self, call, chain, root_cause=None):
        target = call["arguments"].get("path") or call["arguments"].get("name") or ""
        chain.append(("MCP 调用", f"tools/call {call['tool']}({target})"))
        res = self.mcp.call_tool(call["tool"], call["arguments"])
        v = res.get("verdict")
        decision = (v or {}).get("decision") or ("deny" if res.get("isError") else "allow")
        chain.append(("安全校验", f"{decision}(经 MCP 服务端护栏)"))

        prefix = f"诊断到根因「{root_cause}」," if root_cause else ""
        if decision == "deny":
            reply = (f"{prefix}{call['tool']}({target}) 已被服务端安全护栏拦截:{v['message']}。"
                     + (f"建议改用:{v['suggestion']}" if v and v.get("suggestion") else ""))
        elif decision == "require_confirm":
            reply = (f"{prefix}{call['tool']}({target}) 为高风险操作,需要确认。"
                     f"原因:{v['message']};前置:{v['requires']}。回复『确认』继续。")
        else:
            reply = f"{prefix}已通过 MCP 协议交最小权限执行器处理 {call['tool']}({target})。"
        return self._result(reply, chain, verdict=v)

    def _mk_call(self, intent):
        return {"call_id": "direct", "tool": intent["tool"],
                "arguments": intent.get("arguments", {}), "reason": "用户直接请求"}

    def _find_playbook(self, pid):
        for p in glob.glob(os.path.join(self.root, "playbooks", "*.yaml")):
            if yaml.safe_load(open(p, encoding="utf-8")).get("id") == pid:
                return p
        raise FileNotFoundError(pid)

    def _result(self, reply, chain, verdict=None):
        return {"reply": reply, "chain": chain, "verdict": verdict}
