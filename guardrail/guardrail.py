"""
安全护栏 (Safety Guardrail) —— 整个智能运维 Agent 的灵魂模块。

设计血统:
  - tool_call  借鉴 MCP / function calling 的 {tool, arguments}
  - 资产/规则  借鉴 AWS IAM Policy 的 Effect / Resource / Condition
  - verdict    借鉴 Kubernetes 准入控制器 (Admission Controller) 的裁决响应,
               但把 K8s 的二元 allowed 扩展为四档,并新增 suggestion 字段。

核心特性:确定性。裁决来自"查资产清单 + 匹配规则",不依赖大模型的概率判断,
因此可以对"绝不误删库"给出硬保证。verify() 是纯函数,可脱离系统单独单测/演示。
"""

import fnmatch
import yaml
from pathlib import Path

# 裁决档位的"严苛程度",用于多规则命中时取最严
_EFFECT_RANK = {"allow": 0, "warn": 1, "require_confirm": 2, "deny": 3}
_EFFECT_TO_DECISION = {
    "allow": "allow",
    "warn": "warn",
    "require_confirm": "require_confirm",
    "deny": "deny",
}


def load_config(cfg_dir: str):
    """加载三份声明式配置,返回 (tools_dict, assets_list, rules_list)。"""
    d = Path(cfg_dir)
    tools = {t["name"]: t for t in yaml.safe_load((d / "tools.yaml").read_text(encoding="utf-8"))}
    assets = yaml.safe_load((d / "assets.yaml").read_text(encoding="utf-8"))
    rules = yaml.safe_load((d / "rules.yaml").read_text(encoding="utf-8"))
    return tools, assets, rules


def _asset_matches(asset: dict, target) -> bool:
    """判断被操作的目标是否属于某条受保护资产。"""
    if target is None:
        return False
    m = asset.get("match", {})
    for pattern in m.get("path_glob", []):
        if fnmatch.fnmatch(str(target), pattern):
            return True
    if target in m.get("name_in", []):
        return True
    return False


def _rule_matches(rule: dict, op: str, matched_assets: list) -> bool:
    """规则命中条件:操作在 ops 内,且命中资产的等级在 asset_criticality 内。"""
    m = rule.get("match", {})
    if op not in m.get("ops", []):
        return False
    wanted = m.get("asset_criticality", [])
    return any(a["criticality"] in wanted for a in matched_assets)


def verify(tool_call: dict, tools: dict, assets: list, rules: list) -> dict:
    """
    输入一个结构化工具调用,输出裁决 (verdict)。这就是 K8s 准入 webhook 的等价物。
    tool_call 形如: {"call_id":..., "tool":"delete_file", "arguments":{"path":...}, "reason":...}
    """
    call_id = tool_call.get("call_id", "")
    tool_name = tool_call.get("tool")
    tool = tools.get(tool_name)

    # 1) 未知工具 → 安全默认:拒绝 (default-deny)
    if tool is None:
        return _verdict("deny", "critical", [], [],
                        reasons=[f"未注册的工具: {tool_name}"],
                        message="调用了未在工具目录中注册的工具,已拒绝", call_id=call_id)

    # 2) 只读诊断工具 → 自动放行 (感知类操作无副作用)
    if tool["category"] == "readonly":
        return _verdict("allow", "low", [], [],
                        reasons=["只读诊断工具,无副作用,自动放行"], call_id=call_id)

    # 3) 变更类工具:解析操作与目标,匹配资产与规则
    op = tool["op"]
    target = tool_call.get("arguments", {}).get(tool.get("target_arg"))
    matched_assets = [a for a in assets if _asset_matches(a, target)]
    matched_rules = [r for r in rules if _rule_matches(r, op, matched_assets)]

    asset_ids = [a["id"] for a in matched_assets]
    risk = matched_assets[0]["criticality"] if matched_assets else tool.get("risk_base", "low")

    # 4) 无规则命中:未触及受保护资产,按低风险变更"放行并记录"
    if not matched_rules:
        return _verdict("warn", risk, asset_ids, [],
                        reasons=["未命中受保护资产,视为低风险变更,放行并记录审计"],
                        call_id=call_id)

    # 5) 多规则命中取最严
    chosen = max(matched_rules, key=lambda r: _EFFECT_RANK[r["effect"]])
    decision = _EFFECT_TO_DECISION[chosen["effect"]]
    reasons = [f"目标命中资产 {a['id']} ({a['type']}/{a['criticality']}): {a.get('note','')}"
               for a in matched_assets]
    reasons.append(f"触发规则 {chosen['id']}: {chosen.get('message','')}")

    return _verdict(
        decision, risk, asset_ids, [r["id"] for r in matched_rules],
        reasons=reasons,
        message=chosen.get("message", ""),
        suggestion=chosen.get("suggest"),
        requires=chosen.get("requires", []),
        call_id=call_id,
    )


def _verdict(decision, risk_level, matched_assets, matched_rules,
             reasons=None, message="", suggestion=None, requires=None, call_id=""):
    """组装标准裁决结构。字段对齐 K8s AdmissionResponse 的语义并做了运维场景扩展。"""
    return {
        "decision": decision,            # allow | warn | require_confirm | deny  (K8s 是二元 allowed)
        "risk_level": risk_level,        # low | medium | high | critical
        "matched_assets": matched_assets,
        "matched_rules": matched_rules,
        "reasons": reasons or [],
        "message": message,
        "suggestion": suggestion,        # K8s 没有此字段:拦截的同时给出正确做法
        "requires": requires or [],      # 需确认时还需满足的前置(confirm / snapshot ...)
        "call_id": call_id,
    }
