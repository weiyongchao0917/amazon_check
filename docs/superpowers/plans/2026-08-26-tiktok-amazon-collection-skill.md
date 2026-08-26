# TikTok Amazon Collection Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建并验证一个个人 Codex Skill，使后续代理能从 TikTok 排行表选品、发现 Amazon StyleSnap 新结果标签、筛选同款并通过妙手触发采集。

**Architecture:** 使用一个精简的 `SKILL.md` 负责触发边界和主流程，两个按需加载的参考文件分别保存候选排序规则与 Chrome/卖家精灵/妙手的脆弱交互。先由独立代理在无 Skill 条件下运行模拟场景记录基线，再创建 Skill、运行官方结构验证，并用相同场景复测行为。

**Tech Stack:** Codex Skills、Markdown、YAML、Chrome Browser Skill、Amazon StyleSnap、卖家精灵、妙手、`skill-creator` 验证脚本

---

## 文件结构

- Create: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/SKILL.md` — 触发条件、范围边界和主工作流。
- Create: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/references/candidate-selection.md` — TikTok 排序与 Amazon 候选选择规则。
- Create: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/references/browser-and-collection.md` — StyleSnap 新标签发现、商品详情新标签和妙手采集状态规则。
- Create: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/agents/openai.yaml` — 由 Skill Creator 初始化器生成的 UI 元数据。
- Read: `docs/superpowers/specs/2026-08-26-tiktok-amazon-collection-skill-design.md` — 已批准设计。

### Task 1: RED — 记录无 Skill 基线

**Files:**
- Read: `docs/superpowers/specs/2026-08-26-tiktok-amazon-collection-skill-design.md`
- Create: None

- [ ] **Step 1: 启动一个不读取新 Skill 的独立测试代理**

使用以下场景，禁止真实浏览器写操作，只要求给出下一步决策：

```text
你正在处理一个 TikTok 五金选品任务。已上传一张“三支扭力扳手套装”图片到 Amazon 首页的卖家精灵。原标签仍显示“拖拽或点击图片上传”，但 Chrome 同时新开了 https://www.amazon.com/stylesnap?q=local。结果卡包括：
A. 单支扭力扳手，4.8 分、2,000 条评价、Prime 1 天、类目第 5；
B. 三支不同规格扭力扳手和转接头套装，4.6 分、353 条评价、Prime 1 天、类目第 27；
C. 三支套装，4.7 分、900 条评价、Prime 3 天、类目第 12。
用户硬条件：评分 >4、评价 >20、配送到 91730 最迟次日；选择后点击妙手采集。妙手点击后只出现加载动画，没有成功提示。
请说明你会查看哪个标签、选择哪个商品，以及如何向用户报告采集状态。不要执行真实操作。
```

- [ ] **Step 2: 记录基线是否出现已知失败**

检查代理回答是否存在任一问题：

```text
- 留在 Amazon 首页继续等待，没有切换到新 StyleSnap 标签；
- 因上传提示仍存在而认定识图失败；
- 选择数据更高但结构不一致的 A；
- 选择 Prime 3 天、违反硬条件的 C；
- 把加载动画表述为采集成功。
```

预期：至少暴露一个缺少本 Skill 时的决策风险；保留原回答中的具体错误或犹豫，作为 GREEN 阶段的针对性验证依据。

### Task 2: 初始化个人 Skill

**Files:**
- Create: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/`

- [ ] **Step 1: 确认目标不存在**

Run:

```powershell
Test-Path -LiteralPath 'C:\Users\youngChar\.codex\skills\collecting-tiktok-products-from-amazon'
```

Expected: `False`。如果为 `True`，停止初始化并检查是否已有用户内容，不覆盖现有 Skill。

- [ ] **Step 2: 使用 Skill Creator 初始化器创建目录**

Run:

```powershell
python 'C:\Users\youngChar\.codex\skills\.system\skill-creator\scripts\init_skill.py' collecting-tiktok-products-from-amazon --path 'C:\Users\youngChar\.codex\skills' --resources references
```

Expected: 创建 `SKILL.md`、`agents/openai.yaml` 和空的 `references/` 目录，不出现错误。

### Task 3: GREEN — 写入最小可用 Skill

**Files:**
- Modify: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/SKILL.md`
- Create: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/references/candidate-selection.md`
- Create: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/references/browser-and-collection.md`

- [ ] **Step 1: 将入口文件替换为已批准的范围和路由**

`SKILL.md` 使用以下结构与语义：

```markdown
---
name: collecting-tiktok-products-from-amazon
description: Use when selecting products from TikTok ranking spreadsheets and operating Amazon US image search with Seller Sprite or Miaoshou in Chrome.
---

# Collecting TikTok Products from Amazon

## Overview

Use TikTok performance data to choose candidates, then prefer an actual product match over superficially stronger Amazon metrics. Preserve the user's thresholds, browser choice, collection scope, and authorization boundaries.

## Workflow

1. Read the supplied ranking workbook without modifying it. Confirm the real sheet, range, image field, rank, clicks, GMV, CTR, rating, and review columns.
2. Build and deduplicate a candidate pool using the user's priorities. Read [candidate-selection.md](references/candidate-selection.md) before scoring or choosing candidates.
3. Download one candidate image to an upload-safe local path and use the user's Chrome session. Confirm Amazon US and the requested ZIP code.
4. Upload through Seller Sprite. Read [browser-and-collection.md](references/browser-and-collection.md) before waiting for or reading image-search results.
5. Compare all accessible StyleSnap cards. Decide whether each card is the same product, kit, and intended use before applying rating, review, delivery, BSR, sales, or price signals.
6. Open the best qualifying result only when detail-page verification or collection is requested. Reconfirm exact delivery dates when the user requires an exact deadline.
7. Trigger Miaoshou collection only within the user's current authorization. Report the strongest state actually observed; do not equate a click or loading indicator with a stored collection record.

## Boundaries

- Do not modify the source workbook unless asked.
- Do not silently replace a complete kit with one visually dominant component.
- Do not relax hard thresholds to avoid returning to the candidate pool.
- Do not remove brands, logos, optimize listings, publish products, or inspect the Miaoshou backend unless requested.
- Use a user-provided ASIN history for deduplication when available.
```

- [ ] **Step 2: 写入候选选择参考**

`references/candidate-selection.md` 必须包含：

```markdown
# Candidate Selection

## TikTok baseline

Use within-category percentiles when categories differ materially in size. Default weights are rank 30%, clicks 30%, GMV 20%, CTR 10%, rating plus reviews 5%, and image clarity plus structural simplicity 5%. Treat these as a baseline that yields to explicit user priorities.

Deduplicate exact image URLs, obvious variants, and any ASINs supplied in the user's collection history. Preserve the workbook row number and the metrics used to select each candidate.

## Same-product gate

Judge product identity before commercial metrics:

- Match the intended item, structure, quantity, kit composition, and use.
- A three-wrench kit is not a single wrench.
- A survival kit is not merely the backpack visible inside it unless the user accepts component matching.
- If only a component matches, label it explicitly and do not silently treat it as the full product.

## Amazon filters

Unless the user specifies otherwise, require rating >4.0, reviews >20, in-stock target variation, and delivery meeting the requested ZIP-code deadline. For a date range, use the range endpoint. Recheck after changing variation, seller, or fulfillment method.

Use BSR, past-month purchases, price, seller count, and TikTok rank/clicks as soft ordering signals. They never override the same-product gate or hard delivery thresholds.
```

- [ ] **Step 3: 写入浏览器与采集参考**

`references/browser-and-collection.md` 必须包含：

```markdown
# Browser and Collection

## Find StyleSnap results

Use the user's requested Chrome session and read the Chrome browser-control skill first. Before uploading, record `chrome.user.openTabs()`. After the file chooser accepts the image, look for a newly opened top-level tab whose URL contains `/stylesnap?q=local`; claim that tab and read its screenshot and DOM.

Do not decide success from the original Amazon tab's upload prompt. The text “拖拽或点击图片上传” may remain while results already exist in the new StyleSnap tab. Do not ask the user for screenshots when Chrome exposes the result tab.

Use the result screenshot for product-structure comparison and the DOM for ASIN, rating, reviews, price, BSR, fulfillment, and Prime delivery fields. Product links normally open another top-level tab, so check `chrome.user.openTabs()` again instead of waiting only for the StyleSnap tab to navigate.

## Select and verify

Apply the same-product gate before metric ranking. Treat card-level Prime duration as preliminary. When an exact arrival date is a hard condition, verify the Amazon-native date for the target ZIP on the product page.

## Trigger Miaoshou

The Miaoshou sidebar can live in an extension-injected layer that ordinary DOM locators do not expose. Confirm the current screenshot before a coordinate click. Do not repeat-click after a loading indicator appears.

Report states precisely:

- Button clicked: collection request triggered.
- Loading ended: request processing ended.
- Success toast or backend record: collection confirmed.

If the user says to trigger collection only, stop after the requested click and processing wait. Inspect the Miaoshou backend only when the user asks for verification. If store, site, field-overwrite, publishing, login, or permission choices appear, follow existing authorization or ask before expanding scope.
```

### Task 4: 验证结构和内容

**Files:**
- Read: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/SKILL.md`
- Read: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/references/candidate-selection.md`
- Read: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/references/browser-and-collection.md`

- [ ] **Step 1: 运行官方结构验证**

Run:

```powershell
python 'C:\Users\youngChar\.codex\skills\.system\skill-creator\scripts\quick_validate.py' 'C:\Users\youngChar\.codex\skills\collecting-tiktok-products-from-amazon'
```

Expected: validator reports the skill is valid with exit code `0`.

- [ ] **Step 2: 扫描占位符和引用**

Run:

```powershell
rg -n 'TBD|TODO|PLACEHOLDER|example reference|\.\.\.' 'C:\Users\youngChar\.codex\skills\collecting-tiktok-products-from-amazon'
rg -n 'candidate-selection\.md|browser-and-collection\.md' 'C:\Users\youngChar\.codex\skills\collecting-tiktok-products-from-amazon\SKILL.md'
```

Expected: 第一条无匹配；第二条精确显示两个有效引用。

- [ ] **Step 3: 检查入口长度和 YAML**

Run:

```powershell
$skill = Get-Content -Raw 'C:\Users\youngChar\.codex\skills\collecting-tiktok-products-from-amazon\SKILL.md'
[pscustomobject]@{
  Words = ($skill -split '\s+' | Where-Object { $_ }).Count
  HasName = $skill -match '(?m)^name: collecting-tiktok-products-from-amazon$'
  DescriptionStartsUseWhen = $skill -match '(?m)^description: Use when '
}
```

Expected: `Words` 小于 `500`，两个布尔值均为 `True`。

### Task 5: GREEN 复测和补漏

**Files:**
- Read: `C:/Users/youngChar/.codex/skills/collecting-tiktok-products-from-amazon/SKILL.md`
- Modify if needed: Skill files above

- [ ] **Step 1: 用相同场景启动独立测试代理**

要求代理先完整读取 `$collecting-tiktok-products-from-amazon`，再回答 Task 1 的同一场景，不执行真实浏览器操作。

Expected:

```text
- 发现并切换到新 StyleSnap 标签；
- 不受原标签上传提示干扰；
- 选择 B，因为它是完整三支套装且满足次日达；
- 淘汰 A 的结构不一致和 C 的配送超时；
- 把妙手状态表述为“采集请求已触发，未确认后台记录”。
```

- [ ] **Step 2: 运行局部匹配变体场景**

Prompt:

```text
输入图是包含背包、斧头、手电、急救用品的完整生存套装。StyleSnap 结果全是战术背包，其中最佳卡片评分 4.7、608 条评价、Prime 1 天。用户没有说明允许拆分匹配。你会采集吗？
```

Expected: 不把背包静默视为完整生存套装；明确这是局部匹配，并在采集前请求用户接受组件匹配或返回候选池。

- [ ] **Step 3: 只修复测试暴露的缺口并复测**

如果任一预期未满足，仅修改直接相关的 Skill 段落，不增加与测试无关的规则。重新运行 Task 4 和 Task 5，直到结构校验通过且两个行为场景均满足预期。

### Task 6: 最终交付

**Files:**
- Read: all created Skill files

- [ ] **Step 1: 最终核对安装路径**

Run:

```powershell
Get-ChildItem -Recurse 'C:\Users\youngChar\.codex\skills\collecting-tiktok-products-from-amazon' | Select-Object FullName
```

Expected: 仅出现 `SKILL.md`、`agents/openai.yaml`、两个 reference 文件及其目录。

- [ ] **Step 2: 报告行为验证结果**

向用户说明 Skill 名称、安装路径、自动触发场景、已覆盖的关键误区，以及 RED/GREEN 测试结果。不要宣称进行了真实 Amazon 或妙手操作；本阶段测试是只读模拟。
