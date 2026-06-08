"""
端到端杀手 demo:管理员说"清理系统垃圾" → 感知磁盘满 → 定位大日志
→ 识别是 MySQL binlog → 护栏拒绝直删 → 给出正确做法。
打印顺序刻意对齐评分项要求的五段闭环。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "guardrail"))

from guardrail import load_config, verify   # noqa: E402  (guardrail/guardrail.py)
from rca_engine import diagnose             # noqa: E402

_MARK = {"allow": "放行", "warn": "放行+记录", "require_confirm": "需人工确认", "deny": "拒绝"}


def main():
    user_request = "帮我清理一下系统垃圾,磁盘快满了"
    print("=" * 64)
    print(f"① 接收指令   管理员: \u300c{user_request}\u300d")

    tools, assets, rules = load_config(os.path.join(HERE, "guardrail"))
    result = diagnose(os.path.join(HERE, "playbooks", "disk_full.yaml"))

    print(f"\n② 感知环境   选中剧本【{result['title']}】,按序取证:")
    for c in result["chain"]:
        print(f"   - {c['tool']}: {c['note']}")
        print(f"       证据 → {c['output']}")

    concl = result["conclusion"]
    print(f"\n③ 推理决策   根因假设(置信度 {concl['confidence']}):")
    print(f"       {concl['root_cause']}")

    call = result["remediation_call"]
    if not call:
        print("\n   无候选修复动作,需人工进一步排查。")
        return
    print(f"   候选修复动作: {call['tool']}(path={call['arguments'].get('path')})")

    print("\n④ 安全校验   把候选动作提交安全护栏:")
    v = verify(call, tools, assets, rules)
    print(f"       裁决 → 【{_MARK[v['decision']]}】 风险:{v['risk_level']}")
    for r in v["reasons"]:
        print(f"       · {r}")

    print("\n⑤ 执行结果")
    if v["decision"] == "deny":
        print("       动作被拦截,未执行。系统安全无虞。")
        if v["suggestion"]:
            print(f"       Agent 改为建议: {v['suggestion']}")
    elif v["decision"] == "require_confirm":
        print(f"       暂缓执行,等待人工确认;前置要求: {v['requires']}")
    else:
        print("       动作放行并记录审计。")
    print("=" * 64)


if __name__ == "__main__":
    main()
