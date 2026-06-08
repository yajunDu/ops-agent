#!/usr/bin/env bash
# 创建运维 Agent 的受限服务账户(在目标主机以 root 执行一次)。
set -euo pipefail
USER_NAME="opsagent"

# 1) 建无登录 shell、无家目录登录的系统账户(降低被滥用风险)
id "$USER_NAME" &>/dev/null || useradd --system --shell /usr/sbin/nologin "$USER_NAME"

# 2) 安装最小权限白名单并校验语法
install -m 0440 "$(dirname "$0")/sudoers.opsagent" /etc/sudoers.d/opsagent
visudo -cf /etc/sudoers.d/opsagent

# 3) Agent 进程以该账户启动(示例,具体由 systemd unit 指定 User=opsagent)
echo "完成:Agent 应以 $USER_NAME 身份运行,特权动作经 sudoers 白名单逐条授权。"
