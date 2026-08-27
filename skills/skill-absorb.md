---
name: skill-absorb
description: "要生成新技能时，先扫全部老技能，能吸收就不新建。配合 Skill Factory 使用。"
version: 1.0.0
author: Hermes Maintenance Expert
license: MIT
platforms: [windows, linux, macos]
tags: [meta, skill-factory, consolidation, merge]
related_skills: [Skill Factory]
---

# Skill Absorb — 技能吸收器

**每次要生成新技能时先加载这个 skill。** 在新建文件之前，先全面扫描已有的技能，找到最适合吸收新内容的那个老技能。尽量不新建，保持技能总数精简。

## 和 Skill Factory 的关系

Skill Factory 负责**发现**可生成技能的工作流。
Skill Absorb 负责**决定**：把这个内容塞进已有技能里，还是真的需要新建一个文件。

```
Skill Factory 发现新技能  →  Skill Absorb 扫描老技能  →  能吸收？更新老技能 / 不能吸收？新建
```

## 什么时候加载

| 触发场景 | 动作 |
|---------|------|
| Skill Factory 显示 "🏭 SKILL FACTORY — New Skill Detected" | **立即加载本 skill**，在生成文件前 |
| 用户说 "把这个记下来"、"保存成技能" | 先加载本 skill 扫描 |
| 你发现一个值得保存的工作流 | 先扫描，再决定新建还是吸收 |

## 核心逻辑：三步走

### 第一步：获取新技能的"画像"

拿到拟创建技能的：
- **名称**（比如 `docker-debug-cycle`）
- **分类**（比如 `devops`）
- **描述**（一句话：它干什么的）
- **核心步骤摘要**（3-5 个关键词/短语，描述它做的事）

### 第二步：扫描所有已有技能，逐一打分

用 `skills_list()` 获取所有技能，对每一个计算"吸收匹配分"：

| 匹配信号 | 加权 | 例子 |
|---------|:----:|------|
| 分类相同 | +30 | 都是 `devops` |
| 描述关键词重叠 | +25/个 | 新技能有 "docker"，老技能也有 "docker" |
| 核心步骤重叠 | +20/个 | 新技能有 "build image"，老技能也有 |
| 名称关键词重叠 | +15/个 | 都含 "deploy" |
| 领域一致 | +10 | 都关于容器/部署 |

**得分解读：**
- **≥70 分** → 直接吸收！不需要新文件
- **40~69 分** → 部分重叠。把独特的内容吸进去，独特的留下
- **<40 分** → 不重叠，新建

### 第三步：执行

#### 吸收模式（≥70 分）

目标：用 `skill_view()` 加载老技能的完整内容，把新内容**插入老技能中合适的位置**，然后：

```bash
skill_manage(action='patch', name='老技能名',
  old_string='最相关的段落',
  new_string='最相关的段落\n\n## ...\n\n新内容'
)
```

**吸收原则：**
- 保持老技能的目录结构不变
- 新内容加在最相关的章节下
- 如果老技能没有合适章节，在末尾新增章节
- 更新老技能的 tags（加新关键词）
- 更新 description（如果新内容扩展了范围）
- **不要**修改老技能的 name 和 version（版本号等 curator 决定）

#### 新建模式（<40 分）

正常创建新技能文件：

```bash
skill_manage(action='create', name='新技能名', category='分类', content='...')
```

#### 混合模式（40~69 分）

先吸收重叠部分进老技能，再把不重叠的部分创建为独立技能。

也可以问用户：
> "这个新技能和 `xxx` 技能有部分重叠。我可以把重叠部分合并进去，剩下的单独留着。要我这么做吗？"

## 实际例子

### 场景：Skill Factory 发现了一个 "清理 Docker 容器" 的工作流

```
🏭 SKILL FACTORY — New Skill Detected

Proposed Skill:   docker-container-cleanup
Category:         devops
Description:      Remove stopped containers and dangling images
```

**加载 skill-absorb，扫描：**

| 已有技能 | 得分 | 理由 |
|---------|:----:|:-----|
| `docker-debug-cycle` | **85** | 同在 devops，都有 docker + 清理操作 |
| `server-maintenance` | 45 | 同在运维领域，但侧重系统级 |
| `python-env-setup` | 5 | 无关 |

**结论：** 85分，吸收进 `docker-debug-cycle`，不新建。

→ 用 `skill_view(name='docker-debug-cycle')` 查看，在清理章节追加新步骤。

### 场景：Skill Factory 发现了一个 "GitHub Release" 工作流

```
Proposed Skill:   github-release-packaging
Category:         devops
Description:      Tag, build, and publish GitHub releases
```

| 已有技能 | 得分 | 理由 |
|---------|:----:|:-----|
| `github-workflow` | **92** | 同在 github，已有 PR/issue/CI，release 是自然扩展 |
| `docker-debug-cycle` | 10 | 无关 |

**结论：** 92分，吸进 `github-workflow`。

### 场景：Skill Factory 发现了一个 "Midjourney 提示词" 工作流

```
Proposed Skill:   midjourney-prompt-guide
Category:         creative
Description:      Iterate on Midjourney prompts for better image generation
```

| 已有技能 | 得分 | 理由 |
|---------|:----:|:-----|
| `comfyui` | 35 | 同在 creative，都有 image gen，但 ComfyUI 是 stable diffusion |
| `sketch` | 15 | 同在 creative，但侧重 HTML 原型 |
| 其他 | <10 | 无关 |

**结论：** 没有够格的，新建 `midjourney-prompt-guide`。

## 专业性原则（重要！）

**宁新建，不硬塞。** 如果匹配分在 40-69 的灰色地带，优先问用户，不要自作主张。

吸收时要保持老技能的**专业纯度**。例如：
- 不要把 "Docker 容器清理" 塞进 "Python 环境配置" — 即使有 30 分重叠（都是命令行工具），但领域不同
- 不要把 "GitHub Actions" 塞进 "Photoshop 批处理" — 即使名字里都有 "action"

## 汇报格式

### 吸收模式

```
📥 技能吸收完成

  新内容：docker container 自动清理
  吸收进：docker-debug-cycle
  位置：Cleanup > Docker 容器清理章节
  改动：+8 行步骤，+2 个 tag (cleanup, dangling)

  节省：少建 1 个新技能 ✅
```

### 新建模式

```
🆕 新建技能

  名称：midjourney-prompt-guide
  分类：creative
  原因：没有已有技能能吸收（最高匹配 35 分 — comfyui 领域不同）
```

## 预防陷阱

- ❌ 不要把一个新技能的内容分散到多个老技能里 — 只找**最匹配的一个**
- ❌ 不要让老技能变得"大杂烩" — 每个技能应该有一个清晰的核心职责
- ❌ 不要把用户的个人偏好设定吸收进通用技能
- ✅ 吸收后更新 skill 的 description 和 tags，但 name 保持不变
- ✅ 吸收前用 `skill_view()` 加载完整内容，不要盲猜
