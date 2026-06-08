"""
根因引擎 (RCA Engine) —— 执行一条剧本:取证 → 匹配根因 → 产出候选修复动作。

输出里特意带一条 chain(思维链),对应评分项"推理链路溯源":
完整记录"取了哪些证据 → 命中哪条结论 → 提议什么动作"。
候选修复动作以结构化 tool_call 形式产出,随后交给安全护栏裁决(不直接执行)。
"""

import fnmatch
import yaml
from pathlib import Path
from perception import run_tool


def _check_condition(key: str, expected, evidence: dict) -> bool:
    """单个 when 条件求值。支持路径通配(key 以 _matches 结尾)和数值比较(>=,<=,>,<,==)。"""
    if key.endswith("_matches"):
        field = key[: -len("_matches")]
        val = evidence.get(field)
        return val is not None and fnmatch.fnmatch(str(val), str(expected))

    val = evidence.get(key)
    if val is None:
        return False
    s = str(expected)
    for op in (">=", "<=", ">", "<", "=="):
        if s.startswith(op):
            num = float(s[len(op):])
            return {">=": val >= num, "<=": val <= num, ">": val > num,
                    "<": val < num, "==": val == num}[op]
    return val == expected


def _subst(value, evidence: dict):
    """把 "{largest_file}" 这种占位符替换成证据集里的真实值。"""
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return evidence.get(value[1:-1], value)
    return value


def diagnose(playbook_path: str) -> dict:
    pb = yaml.safe_load(Path(playbook_path).read_text(encoding="utf-8"))

    # 1) 按顺序取证,边取边记入思维链
    evidence, chain = {}, []
    for step in pb.get("steps", []):
        out = run_tool(step["tool"], step.get("args"))
        evidence.update(out)
        chain.append({"step": step["id"], "tool": step["tool"],
                      "note": step.get("note", ""), "output": out})

    # 2) 匹配根因结论:第一个 when 全满足的胜出
    chosen = None
    for c in pb.get("conclusions", []):
        when = c.get("when") or {}
        if all(_check_condition(k, v, evidence) for k, v in when.items()):
            chosen = c
            break

    # 3) 把候选修复组装成结构化 tool_call(占位符替换),留给护栏裁决
    remediation_call = None
    if chosen and chosen.get("remediation"):
        rem = chosen["remediation"]
        args = {k: _subst(v, evidence) for k, v in (rem.get("arguments") or {}).items()}
        remediation_call = {"call_id": "rca-fix", "tool": rem["tool"],
                            "arguments": args, "reason": chosen.get("root_cause", "")}

    return {"playbook": pb["id"], "title": pb.get("title", ""),
            "evidence": evidence, "chain": chain,
            "conclusion": chosen, "remediation_call": remediation_call}
