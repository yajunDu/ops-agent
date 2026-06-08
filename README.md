# ops-agent —— 智能运维 Agent(原型)

通过对话高效运维,同时用"安全护栏 + 抗注入"杜绝误删库、危险操作、提示词注入。

## 已实现(原型骨架,均可运行)
- guardrail/        安全护栏:结构化工具调用 + 资产清单 + 规则引擎,确定性四档裁决
- playbooks/        根因剧本(声明式),首条:磁盘空间不足
- perception.py     感知层只读工具:双模式,默认仿真 / 设 OPS_AGENT_REAL=1 取真数据(df/find/ps/journalctl)
- rca_engine.py     根因引擎:取证→匹配根因→产候选修复+思维链
- injection_guard.py 提示词注入检测(确定性,扫描用户输入与环境文本)
- llm_client.py     可插拔 LLMClient:RuleBased(默认零依赖)+ DeepSeek(生产可用:超时/重试/JSON容错/白名单校验/失败降级)
- orchestrator.py   编排器:接收→注入检查→意图路由→引擎/工具→护栏→回话
- executor.py       最小权限执行器(双手):无shell拼接/非必要不root/sudo白名单/独立复核/备份回滚
- deploy/           部署产物:sudoers 白名单 + 服务账户创建脚本
- audit.py          审计哈希链:每次对话落一条防篡改记录,可检测任意篡改
- mcp_server.py     MCP 协议层(自实现,零重依赖):initialize/tools/list/tools/call + stdio,变更操作服务端独立过护栏
- mcp_client.py     MCP 客户端:进程内 / stdio 两种传输
- web_server.py     Web 后端(B/S):stdlib http.server,暴露 /api/chat
- web/index.html    单页控制台:对话 + 实时执行链路时间线 + 安全裁决可视化

## 真实采集开关
    OPS_AGENT_REAL=1 python3 demo_rca.py    # 用真实命令取证(默认仿真,便于演示杀手 demo)

## 跑起来(x86 即可,无需模型/VM)
    cd guardrail && python3 demo.py          # 护栏五档裁决
    cd ..        && python3 demo_rca.py       # 根因引擎闭环
    python3 demo_orchestrator.py             # 编排器多轮对话(含抗注入)
    python3 demo_audit.py                    # 审计哈希链:落账与篡改检测
    python3 demo_mcp.py                      # MCP 协议:握手/列工具/调用/服务端拦截/stdio
    python3 demo_deepseek.py                 # DeepSeek 路由全链路(用模拟返回,无需真 key)
    python3 demo_executor.py                 # 最小权限执行器:注入硬化/真实执行+备份/按需提权
    python3 demo_integrated.py               # ★总闭环(命令行版)
    python3 web_server.py                    # ★启动 B/S 控制台,浏览器开 http://127.0.0.1:8000

## 切换到真实大模型(DeepSeek)
    export DEEPSEEK_API_KEY="你的key"      # 只放环境变量,绝不写进代码/git
    # 代码里: Orchestrator(llm=DeepSeekClient())  接口不变,逻辑零改动
    # 模型不可用时自动降级规则路由,服务不中断

## 设计血统(答辩用)
- tool_call  借鉴 MCP / function calling
- 资产+规则  借鉴 AWS IAM Policy(Effect/Resource/Condition)
- verdict    借鉴 Kubernetes 准入控制器(扩展为四档 + suggestion)
- 剧本       借鉴 故障排查决策树 + Ansible playbook

## 待办
- 本地 Qwen 小模型接入(LoongArch,挂后台)
- 最小权限执行落地 / 9 项文档
