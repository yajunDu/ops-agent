"""
感知层 (Perception) —— Agent 的只读取证工具,对应赛题第一功能"OS 环境深度感知"。

双模式,由环境变量 OPS_AGENT_REAL 切换:
  默认(仿真)  : 返回"磁盘被 binlog 占满"的患病快照,便于无故障环境下演示/单测。
  OPS_AGENT_REAL=1 : 真实调用 df / find / ps / journalctl 并解析为结构化字段。

无论哪种模式,函数接口与返回字段都一致,因此上层根因引擎无需改动——
这正是把感知层独立成模块的回报。所有工具均只读、无副作用,不经过安全护栏。
真实采集一律不用 shell 字符串拼接(命令以参数列表传入),从源头杜绝命令注入。
"""

import os
import shutil
import subprocess

USE_REAL = os.environ.get("OPS_AGENT_REAL", "0") == "1"


def _run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------- get_disk_usage ----------------
def get_disk_usage():
    return _real_disk_usage() if USE_REAL else {
        "mount": "/", "disk_usage_pct": 95, "free_gb": 1.2, "_source": "sim"}


def _real_disk_usage():
    out = _run(["df", "-P"]).stdout
    best = {"mount": "/", "disk_usage_pct": 0, "free_gb": 0.0}
    for line in out.splitlines()[1:]:
        p = line.split()
        if len(p) < 6:
            continue
        try:
            cap, avail_kb = int(p[4].rstrip("%")), int(p[3])
        except ValueError:
            continue
        if cap >= best["disk_usage_pct"]:
            best = {"mount": p[5], "disk_usage_pct": cap, "free_gb": round(avail_kb / 1048576, 2)}
    best["_source"] = "real"
    return best


# ---------------- find_large_files ----------------
def find_large_files(top: int = 5, path: str = "/"):
    return _real_find_large_files(top, path) if USE_REAL else {
        "largest_file": "/var/lib/mysql/binlog.001", "largest_file_size_mb": 8200,
        "top_files": [
            {"path": "/var/lib/mysql/binlog.001", "size_mb": 8200},
            {"path": "/var/log/nginx/access.log", "size_mb": 1400},
            {"path": "/tmp/app_cache.log", "size_mb": 600},
        ], "_source": "sim"}


def _real_find_large_files(top=5, path="/"):
    res = _run(["find", path, "-xdev", "-type", "f", "-printf", "%s\t%p\n"])
    files = []
    for line in res.stdout.splitlines():
        if "\t" not in line:
            continue
        size_s, fp = line.split("\t", 1)
        try:
            files.append((int(size_s), fp))
        except ValueError:
            continue
    files.sort(key=lambda x: -x[0])
    files = files[:top]
    if not files:
        return {"largest_file": None, "largest_file_size_mb": 0, "top_files": [], "_source": "real"}
    top_files = [{"path": fp, "size_mb": round(sz / 1048576, 1)} for sz, fp in files]
    return {"largest_file": top_files[0]["path"], "largest_file_size_mb": top_files[0]["size_mb"],
            "top_files": top_files, "_source": "real"}


# ---------------- list_processes ----------------
def list_processes():
    return _real_list_processes() if USE_REAL else {
        "process_count": 142, "zombie_count": 3,
        "top_cpu": [{"pid": "881", "stat": "R", "pcpu": 87.0, "comm": "stress"}],
        "_source": "sim"}


def _real_list_processes():
    res = _run(["ps", "-eo", "pid,ppid,stat,pcpu,comm", "--no-headers"])
    procs, zombies = [], 0
    for line in res.stdout.splitlines():
        p = line.split(None, 4)
        if len(p) < 5:
            continue
        if "Z" in p[2]:
            zombies += 1
        try:
            procs.append({"pid": p[0], "stat": p[2], "pcpu": float(p[3]), "comm": p[4]})
        except ValueError:
            continue
    procs.sort(key=lambda x: -x["pcpu"])
    return {"process_count": len(procs), "zombie_count": zombies,
            "top_cpu": procs[:5], "_source": "real"}


# ---------------- get_logs ----------------
def get_logs(lines: int = 50):
    return _real_get_logs(lines) if USE_REAL else {
        "lines_scanned": 50, "error_count": 4,
        "sample_errors": ["[ERROR] disk write failed: No space left on device"],
        "_source": "sim"}


def _real_get_logs(lines=50):
    if shutil.which("journalctl"):
        out = _run(["journalctl", "-n", str(lines), "--no-pager"]).stdout
    elif os.path.exists("/var/log/syslog"):
        out = _run(["tail", "-n", str(lines), "/var/log/syslog"]).stdout
    else:
        out = ""
    errs = [l for l in out.splitlines()
            if any(k in l.lower() for k in ("error", "fail", "critical"))]
    return {"lines_scanned": len(out.splitlines()), "error_count": len(errs),
            "sample_errors": errs[:5], "_source": "real"}


_TOOLS = {
    "get_disk_usage": get_disk_usage,
    "find_large_files": find_large_files,
    "list_processes": list_processes,
    "get_logs": get_logs,
}


def run_tool(name: str, args: dict | None = None):
    if name not in _TOOLS:
        raise KeyError(f"未知的感知工具: {name}")
    return _TOOLS[name](**(args or {}))
