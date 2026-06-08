"""最小权限执行器演示:覆盖注入硬化、真实执行+备份、双手独立拦截、按需提权。"""
import os
import sys

from executor import build_command, execute

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "guardrail"))
from guardrail import load_config  # noqa: E402

tools, assets, rules = load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "guardrail"))


def call(tool, **args):
    return {"call_id": "demo", "tool": tool, "arguments": args, "reason": "demo"}


print("=== 1) 命令构造(无 shell):工具 → 参数列表 + 是否需提权 ===")
for t, a in [("delete_file", {"path": "/tmp/x.log"}),
             ("chmod_file", {"path": "/etc/ssh/sshd_config", "mode": "777"}),
             ("restart_service", {"name": "mysqld"})]:
    print(f"  {t:<16} {build_command(t, a)}")

print("\n=== 2) 注入硬化:路径里藏 ; rm -rf /,因无 shell 而沦为普通字符串 ===")
argv, _ = build_command("delete_file", {"path": "/tmp/x; rm -rf /"})
print(f"  构造结果: {argv}")
print("  整段 '/tmp/x; rm -rf /' 是单个参数,rm 只会找这个文件名,分号不会被解释执行。")

print("\n=== 3) 真实执行 + 备份:在 /tmp 建测试文件,执行器删除并先备份 ===")
test = "/tmp/opsagent_test.log"
open(test, "w").write("hello")
print(f"  执行前 exists={os.path.exists(test)}")
r = execute(call("delete_file", path=test), tools, assets, rules, dry_run=False)
print(f"  执行结果: executed={r['executed']} ran_as={r['ran_as']} backup={r.get('backup')}")
print(f"  执行后 exists={os.path.exists(test)}  (备份保留可回滚)")
if r.get("warnings"):
    print(f"  告警: {r['warnings']}")

print("\n=== 4) 双手独立拦截:直接喂删 binlog,执行器自己再过护栏 → 拒绝 ===")
r = execute(call("delete_file", path="/var/lib/mysql/binlog.001"), tools, assets, rules, dry_run=False)
print(f"  结果: {r}")

print("\n=== 5) 按需提权:重启服务需 root → 包装成 sudo -n 白名单命令(dry-run)===")
r = execute(call("restart_service", name="mysqld"), tools, assets, rules, dry_run=True)
# restart 属 require_confirm,演示带上已确认
r2 = execute({"call_id": "d", "tool": "restart_service",
              "arguments": {"name": "mysqld", "_confirmed": True}, "reason": "demo"},
             tools, assets, rules, dry_run=True)
print(f"  未确认: {r.get('reason', r)}")
print(f"  已确认(dry-run): 将以 {r2['ran_as']} 执行 {r2['command']}")
