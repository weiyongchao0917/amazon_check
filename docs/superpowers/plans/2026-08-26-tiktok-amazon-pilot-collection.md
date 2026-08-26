# TikTok 到 Amazon 妙手首轮采集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 TikTok 五金工具排行榜中筛选一个合格测试品，在 Amazon 美国站找到满足硬性条件的商品，并通过妙手插件完成一次成功采集。

**Architecture:** 先在本地只读分析 TikTok 排行榜并生成去重候选池，再在用户的 Chrome 中逐个执行 Amazon 图片搜索和商品页验证。只有满足邮编、次日达、评分、评价、库存和图片对应条件的商品才进入妙手采集；妙手正式采集前向用户展示关键字段并获得最终确认。

**Tech Stack:** `@oai/artifact-tool`、Excel `.xlsx`、Chrome、Amazon.com、妙手浏览器插件

---

### Task 1: 生成 TikTok 候选池

**Files:**
- Read: `D:/wechat Files/xwechat_files/wxid_xifny229u0qp22_24fd/temp/RWTemp/2026-08/68192d64cff150893a57f8744b48f0b2/tiktok_category_ranking_20260824_035703(1).xlsx`
- Read: `docs/superpowers/specs/2026-08-26-tiktok-amazon-pilot-collection-design.md`

- [ ] **Step 1: 只读导入排行榜**

确认“商品排行榜”范围为 `A1:M2937`，源表不保存、不覆盖、不导出。

- [ ] **Step 2: 对数据指标做类目内归一化**

按三级类目分别计算当前排名、最高点击量、最高成交金额、最高点击率、TikTok 评分和评价数量的百分位，避免不同细分类目规模差异造成偏差。

- [ ] **Step 3: 计算综合候选分**

使用以下权重：当前排名 30%、最高点击量 30%、最高成交金额 20%、最高点击率 10%、TikTok 评分和评价数量 5%、图片可识别度和结构简单程度 5%。其中当前排名使用反向分值，排名越靠前得分越高。

- [ ] **Step 4: 去重和风险初筛**

按商品原图 URL 去重；排除武器或自卫用品、刀具、开锁工具、危险化学品、喷雾、高功率激光、隐藏摄像、焊枪、烙铁、电池和复杂电器。品牌和 Logo 不作为淘汰条件。

- [ ] **Step 5: 选择首个图片清晰的测试候选**

保留候选的工作表行号、三级类目、图片 URL、当前排名、最高点击量、最高成交金额和综合选择理由，供 Amazon 搜索和最终核对。

### Task 2: 准备 Amazon 搜索环境

**Files:**
- Read: Task 1 选中候选的公开商品图片 URL

- [ ] **Step 1: 连接用户指定的 Chrome**

复用已连接的 Chrome，不切换到其他浏览器。确认 Amazon 和妙手插件页面可用；如果登录失效，停下并请用户完成登录。

- [ ] **Step 2: 设置配送邮编**

在 Amazon.com 将配送位置设置为 `91730`，并从页面顶部位置控件确认显示该邮编。

- [ ] **Step 3: 执行 Amazon 图片搜索**

将候选的公开商品图片用于 Amazon 图片搜索。若 Amazon 页面没有图片搜索入口或搜索不可用，报告当前状态和可选替代方案，等待用户决定，不擅自改用其他识别流程。

### Task 3: 验证 Amazon 商品

**Files:**
- None

- [ ] **Step 1: 检查图片和用途对应**

确认 Amazon 主图、商品结构和用途与 TikTok 候选图片一致；本轮不要求先做商品名对比。

- [ ] **Step 2: 检查评分和评价数**

评分必须严格高于 `4.0`，评价数量必须严格大于 `20`。任何一项不满足都淘汰该 Amazon 商品。

- [ ] **Step 3: 检查库存和规格**

确认当前选择的规格和卖家可售且有库存。切换规格或卖家后重新执行评分、评价数和送达日期检查。

- [ ] **Step 4: 检查次日达硬门槛**

以 Amazon 针对邮编 `91730` 显示的实际日期为准。最晚送达日期不得超过查询日的下一个日历日；日期范围按范围终点判断。不满足时返回 Task 1 的下一候选，不放宽条件。

- [ ] **Step 5: 记录过去一个月购买量**

记录 `bought in past month` 数值作为软性加分项。未显示该字段时不单独淘汰；多个合格 Amazon 商品之间优先选择月购买量更高者。

- [ ] **Step 6: 固化采集前证据**

记录商品标题、ASIN、价格、评分、评价数、过去一个月购买量、库存状态、针对 `91730` 的最晚送达日期和商品页 URL。

### Task 4: 通过妙手完成首轮采集

**Files:**
- None

- [ ] **Step 1: 展示采集前摘要并获取确认**

向用户展示 Task 3 的商品证据以及对应 TikTok 行号、图片和数据指标。妙手正式采集会创建插件侧数据，因此在点击“采集此商品”前获得用户确认。

- [ ] **Step 2: 点击妙手采集**

在当前 Amazon 商品页使用妙手插件的“采集此商品”功能。本轮不执行商品优化，不删除品牌词或 Logo。

- [ ] **Step 3: 验证成功状态**

以妙手界面的明确成功提示、成功状态或已生成的采集记录作为完成依据。若出现店铺选择、重复商品、字段覆盖或异常提示，停止并向用户展示该状态，不猜测关键选项。

- [ ] **Step 4: 报告结果**

报告采集成功的 Amazon 商品、ASIN、关键验证数据和妙手成功状态；如果失败，报告停留步骤、页面提示和下一项安全操作。
