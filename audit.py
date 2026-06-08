"""
审计哈希链 (Audit Hash Chain) —— 对应评分项"推理链路溯源"与"可追溯/异常回溯"。

思路借鉴区块链 / Git commit 链:每条记录都包含上一条记录的哈希,首尾相扣成链。
任何一条记录被事后篡改,它自身的哈希会对不上;若攻击者重算了它的哈希,
则它与下一条记录的 prev_hash 链接又会断裂——因此整条链不可被悄悄修改。

存储用 JSONL(每行一条 JSON),零依赖、可直接 cat 查看,正式版可平滑换 SQLite/openGauss。
"""

import hashlib
import json
import os
import time

GENESIS = "0" * 64


def _hash(core: dict) -> str:
    """对记录的核心字段做规范化序列化后取 SHA-256。"""
    blob = json.dumps(core, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        self._last_hash, self._seq = self._tail()

    def _tail(self):
        """读取现有日志,定位最后一条的哈希与序号(支持续写)。"""
        last_hash, seq = GENESIS, 0
        if os.path.exists(self.path):
            for rec in self.records():
                last_hash, seq = rec["hash"], rec["seq"]
        return last_hash, seq

    def append(self, event: dict) -> dict:
        """追加一条审计记录,自动串上上一条的哈希。"""
        self._seq += 1
        core = {
            "seq": self._seq,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prev_hash": self._last_hash,
            "event": event,
        }
        rec = dict(core, hash=_hash(core))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._last_hash = rec["hash"]
        return rec

    def records(self) -> list:
        recs = []
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        recs.append(json.loads(line))
        return recs

    def verify(self) -> dict:
        """完整性校验:逐条重算哈希并检查链接。返回是否完好,断裂则指出位置。"""
        prev = GENESIS
        recs = self.records()
        for rec in recs:
            core = {k: rec[k] for k in ("seq", "timestamp", "prev_hash", "event")}
            if rec["prev_hash"] != prev:
                return {"ok": False, "broken_seq": rec["seq"],
                        "reason": "链接断裂:prev_hash 与上一条哈希不符"}
            if _hash(core) != rec["hash"]:
                return {"ok": False, "broken_seq": rec["seq"],
                        "reason": "内容被篡改:记录哈希与重算结果不符"}
            prev = rec["hash"]
        return {"ok": True, "count": len(recs), "head_hash": prev[:12]}
