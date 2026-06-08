"""
验证 DeepSeekClient 全链路 —— 不需要真 key:用 fake 返回替换网络调用。
拿回去把 DEEPSEEK_API_KEY 设成真 key、把 agent 的 llm 换成 DeepSeekClient() 即可连真模型。
"""
import json

from llm_client import DeepSeekClient
from orchestrator import Orchestrator

# 用一个会"假装是 DeepSeek"的子类,只替换网络层 _post,其余逻辑全是真的
class FakeDeepSeek(DeepSeekClient):
    def __init__(self, scripted, **kw):
        super().__init__(**kw)
        self.key = "fake"          # 跳过"未设置 key"检查
        self.scripted = scripted   # 预设模型会返回的原始字符串
        self.calls = 0

    def _post(self, body):
        out = self.scripted[min(self.calls, len(self.scripted) - 1)]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        # 复用父类真实的"容忍 markdown + json.loads"逻辑
        import re
        c = re.sub(r"^```(?:json)?|```$", "", out.strip()).strip()
        return json.loads(c)

agent = Orchestrator()
PB, TOOLS = agent.playbooks, agent.tools

print("=== 1) 正常:模型返回干净 JSON,路由到剧本 ===")
agent.llm = FakeDeepSeek(['{"type":"playbook","id":"disk-full"}'])
print("  路由 →", agent.llm.route("磁盘好像快满了帮我看看", PB, TOOLS))

print("\n=== 2) 容错:模型多吐了 markdown 包裹,仍能解析 ===")
agent.llm = FakeDeepSeek(['```json\n{"type":"tool","tool":"chmod_file","arguments":{"path":"/etc/hosts","mode":"777"}}\n```'])
print("  路由 →", agent.llm.route("把 /etc/hosts 改成 777", PB, TOOLS))

print("\n=== 3) 白名单拦截:模型敢引用不存在的工具 → 判非法 → 降级规则路由 ===")
agent.llm = FakeDeepSeek(['{"type":"tool","tool":"rm_rf_everything","arguments":{}}'])
print("  路由 →", agent.llm.route("帮我清理垃圾", PB, TOOLS))

print("\n=== 4) 失败降级:模型连续超时 → 自动退回规则路由,服务不中断 ===")
agent.llm = FakeDeepSeek([TimeoutError("connect timeout")])
print("  路由 →", agent.llm.route("磁盘满了清理一下", PB, TOOLS))

print("\n=== 5) 接进编排器:模型驱动跑完整闭环(口语化输入)===")
agent.llm = FakeDeepSeek(['{"type":"playbook","id":"disk-full"}'])
r = agent.handle("欸 帮我瞅瞅是不是盘塞满了,顺手清一清")
for stage, detail in r["chain"]:
    print(f"  {stage}: {str(detail)[:80]}")
print("  Agent 回复:", r["reply"][:90], "...")
