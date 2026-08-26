# Pet Product Ranking Workbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a new, auditable workbook that filters unsuitable pet products and sorts eligible products from most to least suitable using the approved balanced scoring model.

**Architecture:** Import the supplied workbook without changing its source sheets. Classify every row with deterministic title/category rules, calculate normalized performance and safety scores, then add separate recommendation, exclusion, and rule sheets to a copied workbook. Keep the source data intact and verify both calculations and rendered layout before export.

**Tech Stack:** Node.js, `@oai/artifact-tool`, JavaScript unit tests with `node:test`, Excel formulas, XLSX output.

---

### Task 1: Define and test deterministic classification rules

**Files:**
- Create: `.codex-sheet-read/product-ranking-rules.mjs`
- Create: `.codex-sheet-read/product-ranking-rules.test.mjs`

- [ ] **Step 1: Write failing tests for hard exclusions and risk tiers**

Cover pet food/treats, vitamins and supplements, medicine and therapeutic products, clearly bulky products, high-IP character/franchise terms, legitimate generic products, and false-positive boundaries such as `plain`, `sunbath`, and `palatability`.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `node --test .codex-sheet-read/product-ranking-rules.test.mjs`

Expected: FAIL because the classification module does not exist yet.

- [ ] **Step 3: Implement `classifyProduct(row)` and scoring helpers**

The classifier returns `eligible`, `exclusionReasons`, `ipRisk`, `ipReason`, `weightRisk`, and `weightReason`. Use category rules before title rules, word boundaries for English terms, and explicit handling for dimensions, weights, large furniture, branded franchises, branded compatibility, generic electronics, and consumable/therapeutic wording.

- [ ] **Step 4: Implement metric normalization and approved score weights**

For each numeric metric, calculate `0.7 * globalPercentile + 0.3 * thirdCategoryPercentile`. The 100-point score uses clicks 25, GMV 25, rank rise 15, current rank 10, CTR 10, rating 4, review count 3, IP safety 5, and weight friendliness 3. Treat rank-change `1000` as a new listing at the 90th percentile of ordinary positive rank changes.

- [ ] **Step 5: Run tests and confirm all cases pass**

Run: `node --test .codex-sheet-read/product-ranking-rules.test.mjs`

Expected: all tests pass with no skipped cases.

### Task 2: Build the ranked workbook

**Files:**
- Create: `.codex-sheet-read/build_ranked_workbook.mjs`
- Create: `outputs/01a03d6e-a983-77d2-9397-b3c5d290471b/tiktok_category_ranking_20260826_rescored.xlsx`

- [ ] **Step 1: Render the source workbook before editing**

Render the top rows of `商品排行榜` and visually confirm its existing header/table style.

- [ ] **Step 2: Import the source workbook and classify all 1,564 data rows**

Do not modify the source sheets. Store classifications and score components in memory, sort eligible rows by final score descending, and sort exclusions by primary reason then descending market strength.

- [ ] **Step 3: Add the `推荐排序` sheet**

Include source category/name/image URL/metrics, overall rank, score components, IP risk, weight risk, and concise reasons. Freeze the header, add filters, apply currency/percentage/decimal formats, wrap long titles, and use conditional formatting for total score and risk columns.

- [ ] **Step 4: Add the `排除清单` sheet**

Include every excluded source row with one or more explicit exclusion reasons. Preserve original metrics so users can audit why high-performing rows were removed.

- [ ] **Step 5: Add the `评分规则` sheet**

Document source workbook, date, no-Amazon/no-price-filter scope, hard exclusions, weights, percentile method, new-listing treatment, IP-risk definitions, weight-estimation limits, and output counts.

- [ ] **Step 6: Export one XLSX file**

Export only `tiktok_category_ranking_20260826_rescored.xlsx` to the required output folder.

### Task 3: Verify calculations and visual quality

**Files:**
- Modify: `.codex-sheet-read/build_ranked_workbook.mjs`
- Verify: `outputs/01a03d6e-a983-77d2-9397-b3c5d290471b/tiktok_category_ranking_20260826_rescored.xlsx`

- [ ] **Step 1: Assert data reconciliation**

Verify `recommended rows + excluded rows = 1,564`, scores are descending, all recommended rows are eligible, every excluded row has a reason, no food/medicine hard-exclusion category remains in recommendations, and no score is outside 0–100.

- [ ] **Step 2: Inspect key ranges and scan formulas**

Inspect headers plus representative top recommendation/exclusion/rule rows. Scan for `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, and `#N/A`.

- [ ] **Step 3: Render every sheet**

Render the original sheets and all three new sheets. Confirm headers, titles, metric formats, filters, risk colors, wrapped names, and notes are legible without clipping.

- [ ] **Step 4: Apply focused visual repairs and re-export**

Adjust only affected column widths, row heights, wraps, and colors, then rerun compact value/formula and render checks.

- [ ] **Step 5: Deliver the single verified workbook**

Report the retained/excluded counts, summarize the model, and cite only the final XLSX output.
