"""
最小权限执行器 (Least-Privilege Executor) —— Agent 的"双手",对应赛题"最小权限代理执行"。

设计原则:
  1. 双手不盲信大脑:执行前【独立再过一次安全护栏】,上游漏判也兜得住。
  2. 绝不 shell 拼接:命令以参数列表传入,并用 `--` 终止选项解析;
     即便路径里藏了 ; $() `` -rf,也只会被当成普通字符串,从源头杜绝命令注入。
  3. 非必要不 root:仅当操作确需特权时才通过 `sudo -n <白名单命令>` 提权;
     其余一律以受限服务账户身份直接运行。检测到以 root 跑非必要操作会告警。
  4. 破坏性/配置类操作执行前自动备份,支持回滚(兼顾"配置漂移"治理)。

dry_run 默认开启:只构造并展示"将以什么身份、什么权限、跑什么命令",不产生副作用,
便于演示与测试;在真机上把 dry_run=False 即真正执行。
"""

import getpass
import os
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "guardrail"))
from guardrail import verify  # noqa: E402

# 需要特权的路径前缀:落在这些目录下的写操作要提权
_PRIV_PREFIXES = ("/etc", "/boot", "/usr", "/var", "/lib")
# 服务账户名(真机上 Agent 应以此身份运行,而非 root)
SERVICE_USER = os.environ.get("OPS_AGENT_USER", "opsagent")
BACKUP_DIR = os.environ.get("OPS_AGENT_BACKUP", os.path.join(_HERE, ".backups"))


def _needs_root(path):
    return bool(path) and str(path).startswith(_PRIV_PREFIXES)


def build_command(tool: str, args: dict):
    """工具 → 具体命令(参数列表,无 shell)。返回 (argv, needs_root)。纯函数,可单测。"""
    path, name, mode = args.get("path"), args.get("name"), args.get("mode")
    table = {
        "delete_file":   (["rm", "--", str(path)], _needs_root(path)),
        "truncate_file": (["truncate", "-s", "0", "--", str(path)], _needs_root(path)),
        "chmod_file":    (["chmod", str(mode or "644"), "--", str(path)], _needs_root(path)),
        "kill_process":  (["pkill", "-x", str(name)], True),
        "restart_service": (["systemctl", "restart", str(name)], True),
    }
    return table.get(tool, (None, False))


def _privilege_wrap(argv, needs_root):
    """决定以什么身份执行。非必要不 root;需提权则 sudo -n 调白名单命令。"""
    if not needs_root:
        return getpass.getuser(), argv
    return f"root(via sudo,需 sudoers 授权 {SERVICE_USER})", ["sudo", "-n"] + argv


def _maybe_backup(tool, args, dry_run):
    """破坏性/配置类操作先备份原文件,支持回滚。"""
    path = args.get("path")
    if tool not in ("delete_file", "truncate_file", "edit_config") or not path:
        return None
    if not os.path.isfile(path):
        return None
    dst = os.path.join(BACKUP_DIR, f"{os.path.basename(path)}.{int(time.time())}.bak")
    if dry_run:
        return f"将备份到 {dst}"
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copy2(path, dst)
    return dst


def execute(tool_call, tools, assets, rules, dry_run=True):
    name, args = tool_call["tool"], tool_call.get("arguments", {})

    # 1) 双手独立复核(不信任上游裁决)
    v = verify(tool_call, tools, assets, rules)
    if v["decision"] == "deny":
        return {"executed": False, "blocked": True, "reason": v["message"]}
    if v["decision"] == "require_confirm" and not args.get("_confirmed"):
        return {"executed": False, "pending": True, "reason": "高风险,待人工确认"}

    # 2) 构造命令(无 shell 拼接)
    argv, needs_root = build_command(name, args)
    if argv is None:
        return {"executed": False, "reason": f"无 {name} 的执行器映射"}

    # 3) 最小权限决策
    ran_as, final_argv = _privilege_wrap(argv, needs_root)
    warnings = []
    if os.geteuid() == 0 and not needs_root:
        warnings.append("当前以 root 运行非必要操作,真机上应降权到服务账户")

    # 4) 备份
    backup = _maybe_backup(name, args, dry_run)

    result = {"tool": name, "command": final_argv, "ran_as": ran_as,
              "needs_root": needs_root, "backup": backup, "warnings": warnings}

    # 5) 执行 or dry-run
    if dry_run:
        result.update({"executed": False, "dry_run": True})
        return result
    try:
        r = subprocess.run(final_argv, capture_output=True, text=True, timeout=30)
        result.update({"executed": r.returncode == 0, "rc": r.returncode,
                       "stdout": r.stdout.strip(), "stderr": r.stderr.strip()})
    except Exception as e:  # noqa: BLE001
        result.update({"executed": False, "error": str(e)})
    return result
