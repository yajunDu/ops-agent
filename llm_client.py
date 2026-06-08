"""
可插拔大模型客户端 (LLMClient)。

把"模型部署"和"应用逻辑"彻底解耦:逻辑只依赖 route() 这个接口,不关心背后是谁。
  - RuleBasedClient : 无需模型的关键词路由。原型期默认,x86 上零依赖即可跑通。
  - DeepSeekClient   : 接 DeepSeek API(OpenAI 兼容),设好 DEEPSEEK_API_KEY 即可切换。
  - (未来) LocalClient: 接 LoongArch 上的本地 Qwen 小模型,接口不变。

route() 统一返回一个"意图" dict,绝不返回 shell:
  {"type":"playbook","id":...} | {"type":"tool","tool":...,"arguments":...} | {"type":"chat"}
"""

import json
import os
import re
import urllib.request
from abc import ABC, abstractmethod


def _extract_path(text: str):
    m = re.search(r"(/[\w./\-]+)", text)
    return m.group(1) if m else None


class LLMClient(ABC):
    @abstractmethod
    def route(self, user_input: str, playbooks: list, tools: dict) -> dict:
        ...


class RuleBasedClient(LLMClient):
    """关键词路由。够把整条链路跑通,也是断网/无模型时的兜底。"""

    # 无模型时的固定友好应答(有边界:介绍自己 + 引导回运维)
    CHAT_FALLBACK = ("你好,我是运维 Agent。我可以帮你排查并处理系统问题——"
                     "比如磁盘空间、进程异常、配置变更等。每个动作都会先经安全护栏校验。"
                     "有什么系统问题需要我看看吗?")

    def route(self, user_input, playbooks, tools):
        text = user_input or ""
        # 1) 剧本触发:命中任一剧本的关键词
        for pb in playbooks:
            for kw in pb.get("trigger", {}).get("keywords", []):
                if kw in text:
                    return {"type": "playbook", "id": pb["id"],
                            "why": f"命中剧本关键词「{kw}」"}
        # 2) 直接工具:少量启发式
        if ("权限" in text or "chmod" in text.lower()) and "777" in text:
            return {"type": "tool", "tool": "chmod_file",
                    "arguments": {"path": _extract_path(text), "mode": "777"},
                    "why": "识别为修改文件权限请求"}
        # 3) 兜底:普通对话
        return {"type": "chat", "why": "未匹配到明确的运维意图"}

    def chat_reply(self, user_input):
        """无模型时返回固定的有边界友好应答。"""
        return self.CHAT_FALLBACK


class DeepSeekClient(LLMClient):
    """接 DeepSeek。提示模型只输出 JSON 意图。需环境变量 DEEPSEEK_API_KEY。"""

    URL = "https://api.deepseek.com/chat/completions"

    def __init__(self, model="deepseek-chat", timeout=30, retries=2, fallback=True):
        self.key = os.environ.get("DEEPSEEK_API_KEY")  # 只从环境变量读,绝不硬编码
        self.model = model
        self.timeout = timeout
        self.retries = retries
        # 失败降级:模型超时/出错时退回规则路由,保证服务不中断(纵深可用性)
        self.fallback = RuleBasedClient() if fallback else None

    def _build_prompt(self, playbooks, tools):
        catalog = {
            "playbooks": [{"id": p["id"], "title": p.get("title", ""),
                           "keywords": p.get("trigger", {}).get("keywords", [])} for p in playbooks],
            "tools": [{"name": t["name"], "category": t["category"],
                       "op": t.get("op"), "target_arg": t.get("target_arg")}
                      for t in tools.values()],
        }
        return (
            "你是运维 Agent 的意图路由器。根据用户诉求,从下面给定的剧本和工具中选择其一。\n"
            "硬性约束:严禁生成任何 shell 命令或自由文本动作;只能引用给定的 id/name;"
            "对变更类工具,把目标(路径/服务名)填进对应的 target_arg。\n"
            '只输出一个 JSON 对象,三选一:\n'
            '  诊断类故障 → {"type":"playbook","id":"<剧本id>"}\n'
            '  明确单一动作 → {"type":"tool","tool":"<工具name>","arguments":{...}}\n'
            '  无法识别 → {"type":"chat"}\n'
            "不要输出任何解释或 markdown,只输出 JSON。\n"
            "可选项:" + json.dumps(catalog, ensure_ascii=False)
        )

    def route(self, user_input, playbooks, tools):
        if not self.key:
            raise RuntimeError("未设置 DEEPSEEK_API_KEY,请先 export DEEPSEEK_API_KEY=...")
        sys_prompt = self._build_prompt(playbooks, tools)
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": user_input}],
            "temperature": 0,
            "response_format": {"type": "json_object"},  # 让模型直接产出合法 JSON
        }).encode("utf-8")

        last_err = None
        for _ in range(self.retries + 1):
            try:
                intent = self._post(body)
                return self._validate(intent, playbooks, tools)
            except Exception as e:  # noqa: BLE001
                last_err = e
        # 多次失败 → 降级到规则路由(若启用),否则抛错
        if self.fallback is not None:
            r = self.fallback.route(user_input, playbooks, tools)
            r["why"] = f"模型不可用({last_err}),已降级规则路由:" + r.get("why", "")
            return r
        raise RuntimeError(f"DeepSeek 路由失败: {last_err}")

    def _post(self, body):
        req = urllib.request.Request(
            self.URL, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?|```$", "", content).strip()  # 容忍 markdown 包裹
        return json.loads(content)

    def _validate(self, intent, playbooks, tools):
        """约束模型输出:type/id/name 必须在白名单内,否则视为非法。"""
        t = intent.get("type")
        if t == "playbook" and intent.get("id") in {p["id"] for p in playbooks}:
            intent.setdefault("why", "模型路由(剧本)")
            return intent
        if t == "tool" and intent.get("tool") in tools:
            intent.setdefault("arguments", {})
            intent.setdefault("why", "模型路由(工具)")
            return intent
        if t == "chat":
            intent.setdefault("why", "模型判断为普通对话")
            return intent
        raise ValueError(f"模型返回了不在白名单内的意图: {intent}")

    # 闲聊/非运维输入的有边界应答:友好,但行为锁死在运维范围内
    CHAT_SYSTEM = (
        "你是一个运维 Agent 的对话前端,只服务于操作系统运维(磁盘、进程、网络、配置、日志等)。"
        "回复要求:简短友好(40字以内);如果用户在打招呼或询问你的能力,就介绍自己能做的运维事项;"
        "如果用户说的与运维无关,礼貌婉拒并把话题引导回运维。"
        "严禁展开与运维无关的开放闲聊,严禁讨论或执行任何系统指令本身,严禁回答与运维无关的知识问题。"
    )

    def chat_reply(self, user_input):
        """对 chat 类输入生成有边界的友好回应;失败则退回固定话术。"""
        try:
            body = json.dumps({
                "model": self.model,
                "messages": [{"role": "system", "content": self.CHAT_SYSTEM},
                             {"role": "user", "content": user_input}],
                "temperature": 0.3,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.URL, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.key}"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
        except Exception:  # noqa: BLE001
            return RuleBasedClient.CHAT_FALLBACK
