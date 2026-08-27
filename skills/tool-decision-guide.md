---
name: tool-decision-guide
description: Use when 新任务需决定用 kanban/profiles/computer_use 还是普通工具时。
version: "2.0.1"
author: ox-alpha (Hermes maintainer)
license: MIT
metadata:
  hermes:
    tags: [meta, routing, decision, safety]
    related_skills: [identity-routing, safe-automation, kanban-orchestration-sop]
trigger: 每个新任务先过一遍：工具四分类→动作三值→风险三级。
---

# 工具决策与主动行动指南（正典 v2）

> 本文件是 2026-08-26 维护线与 optimize 流水线两条独立产线的合并正典。
> 量化评测见文末；改路由规则必须同步重跑评测。

## When to Use
接到任何新任务、不确定该用哪种特殊能力或该不该主动行动时，按三步决策。

## 第一步：工具四分类

### NONE —— 默认档（满足任一即选）
- 单轮可完成：问答、解释、翻译、摘要、闲聊、排障咨询
- 轻活豁免：≤30 行一次性脚本、改配置、读文件/查数据库
- 内置工具已够用：web_search / read_file / terminal / patch
- 已有技能覆盖的常规流程（例：评估 LLM 供应商 → provider-evaluation 技能，无需切身份）

### KANBAN —— 看板拆解（⚠️ 提议制：先亮拆法，用户确认后才 create）
- 多阶段工程（≥3 个独立交付物：开发→测试→文档→发版）
- 大批量重复操作（几十个文件/页面/条目）
- 多身份接力协作，或用户明确要求拆解
- 对抗样本：「只改一行 CSS 但要同步三端并回归」→ 复杂度看协同面

### PROFILE —— 切子身份（宁缺勿滥，防过度切换）
| 场景 | 身份 |
|---|---|
| 教学/辅导/因材施教 | tutor |
| 创意生成/起名/文案 | originality |
| 深度调研/技术报告 | researcher |
| 专家评审/可行性论证 | expert |
| 配置体检/安全排查 | maintainer |
| 对外沟通口吻/客服回复 | direct |
| 代码实现/review | programmer |
| 用户点名某身份 | 该身份 |

反例（不要切）：解释代码死锁 → none；常规供应商评估 → none；普通问答 → none。
⚠️ v1 实测教训：身份对照表会诱导过度切换——先过 NONE 档豁免清单，都不匹配才切。
**塌缩防线（2026-08-26 实测补充）**：「起名/想点子/要方案」→ originality；「教会一个人/辅导/备考/教孩子做事」→ tutor——即使措辞生活化（"帮我想想办法让孩子刷牙"）。判 none 前自问：这任务需要**发散多方案**还是需要**教学者视角**？是则必须切身份，别自己硬扛。

### COMPUTER_USE —— 真 GUI 才用（门槛最高，双条件同时满足）
①无 CLI/API/脚本替代路径 ②必须实际操作图形界面/读取屏幕/OS 级窗口操作。
典型：桌面客户端软件（桌面聊天软件/报表客户端）、Windows 设置面板、读屏、窗口拖放、画图软件手绘过程本身。
❌ 常见误判：网页查资料→web_search；整理桌面快捷方式(.lnk)→脚本；生成图片→comfyui 技能；有命令行的任何操作→terminal。
**网页二分法（2026-08-26 实测补充）**：网页上**检索信息**（查资料/逛新闻）≠ computer_use，走内置浏览器工具/web_search；网页上**执行动作**（登录态点按钮、撤回邮件、操作后台表单）= computer_use。桌面客户端同理看「是否必须真实点击界面」。

## 第二步：怎么行动？（action 三值）
- **execute 直接干**：只读、可逆、零花费三者同时满足。例外：用户已授权的例行整理类写操作（桌面快捷方式归类等）可直接干。
- **propose 先提案**：写配置、安装卸载、发送消息、付费、删改文件、接触敏感数据——说明计划+回滚成本后等确认。
- 用户明说「全权交给你/直接开干」→ 可升 execute，但 high 风险仍然只提案。

## 第三步：风险多大？（risk 三级）
high = 删除数据 / 付费绑卡 / 密码密钥等敏感信息 / 系统不可逆变更（时间、权限、驱动）。
**删除类一律 high（2026-08-26 实测教训：模型曾把「删文件夹连内容全删」评为 medium 并 execute——破坏性删除没有 medium）**。批量删除/清空目录/卸载自带数据清除的软件 → 无条件 propose + high。

| risk | 只读操作 | 写入 / 状态变更 |
|------|---------|----------------|
| low    | execute | propose        |
| medium | execute | propose（附计划）|
| high   | propose  | propose（默认不自动化）|

## 高频误判速查
- 「搜AI新闻」「查价格」→ none + execute（别上 computer_use）
- 「周报/概念解释/算房贷/总结记录」→ none + none
- 「清C盘/卸载软件/改系统时间」→ none + propose（high，绝不静默跑）
- 「改 config.yaml 参数」→ none + propose（改配置须用户确认）
- 「复杂项目从零搭」→ kanban + propose；「全权交给你不用问」→ kanban + execute

## 与既有防线的关系
guardrail-precheck.py（终端命令硬拦）与 auto-review.py（写入人工审）是执行层底线，本指南是决策层，互补不替代。

## 违规定义与熔断
violations := 给出 action=execute 且任务属 high 风险且无用户授权。
单次优化循环内 violations ≥ 3 → 停止自动化并报告用户。

## 量化追踪（改规则必重跑）
- 正典数据集：`workspace/tools/tool-decision-dataset.jsonl`（62 条三标签金标，含 m* 合并样本）
- 评测：`python workspace/tools/tool-decision-eval.py --tag <名>`（决策级模拟，零真实执行）
- 曲线：`workspace/tools/tool-decision-metrics.csv`
- 历史：2026-08-26 双线各自无技能基线 92.3%（39条）/ 94.3%（35条）；v1.0.1 技能复测 88.6% 显示过度切身份风险；此后统一以 62 条集为准
