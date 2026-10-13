# 利润核算自动化程序 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建一个可在 `E:\project\crossborder-tools\tools\hesuan_ticheng` 中重复运行的 Python 利润核算程序，自动读取最新主表、采购表和可选商品表，生成带汇总与核对状态的 Excel。

**Architecture:** 用 `hesuan_ticheng.py` 分离文件发现、字段识别、归并匹配、利润计算和 Excel 导出。主订单是输出粒度；采购订单按去掉末尾 `-数字` 的基础订单号归并，退款/拒绝/驳回记录进入排除审计列；有效采购成本优先，商品表成本作为兜底。`test_hesuan_ticheng.py` 使用内存 DataFrame 测试核心规则，真实文件验证作为单独命令执行。

**Tech Stack:** Python 3.9+, pandas, openpyxl, pytest（测试）；Windows `.bat` 启动脚本。

---

## 文件结构

- Create: `hesuan_ticheng.py` — 主程序、数据清洗、匹配、计算、Excel 输出。
- Create: `test_hesuan_ticheng.py` — 核心规则测试，不依赖工作区文件。
- Create: `运行利润核算.bat` — 在当前目录启动脚本并保留错误窗口。
- Create: `README.md` — 安装、运行、输入文件命名、输出列和状态说明。
- Create: `requirements.txt` — `pandas`, `openpyxl` 和 `pytest` 版本下限。
- Output: `输出/订单利润核算-YYYYMMDD-HHMMSS.xlsx` — 程序运行产生，输入文件不覆盖。

### Task 1: 建立失败测试和核心数据接口

**Files:**
- Create: `test_hesuan_ticheng.py`
- Create: `hesuan_ticheng.py`

- [ ] **Step 1: 写会失败的测试，先固定核心 API**

```python
import pandas as pd
from hesuan_ticheng import calculate_profit


def main_row(order="100", revenue=50, product_id="P1", quantity=2):
    return {
        "订单总金额": revenue,
        "平台回款金额": revenue,
        "下单时间": "2026-10-13 10:00:00",
        "订单编号": order,
        "店铺名称": "shop",
        "订单状态": "已完成",
        "产品ID": product_id,
        "产品数量": quantity,
        "产品描述(原始)": "item",
        "产品描述(中文)": "商品",
    }


def purchase_row(order, cost, status="已收货", quantity=1):
    return {"买家订单号": order, "采购总金额": cost, "采购状态": status, "采购数量": quantity}


def test_suborders_are_merged_and_each_suborder_costs_one_dollar():
    main = pd.DataFrame([main_row()])
    purchase = pd.DataFrame([
        purchase_row("100-1", 10),
        purchase_row("100-2", 12),
    ])
    result = calculate_profit(main, purchase, None)
    row = result.rows[0]
    assert row["采购成本"] == 22
    assert row["物流成本"] == 2
    assert row["订单利润"] == 26


def test_product_cost_fallback_uses_unit_cost_times_quantity_and_one_dollar_logistics():
    main = pd.DataFrame([main_row(order="200", revenue=30, product_id="P2", quantity=3)])
    purchase = pd.DataFrame(columns=["买家订单号", "采购总金额", "采购状态", "采购数量"])
    products = pd.DataFrame([{"产品id": "P2", "产品成本($)": 4}])
    result = calculate_profit(main, purchase, products)
    row = result.rows[0]
    assert row["商品成本合计"] == 12
    assert row["物流成本"] == 1
    assert row["订单利润"] == 17
    assert row["核对状态"] == "已匹配商品成本"
```

- [ ] **Step 2: 运行测试，确认因模块和函数不存在而失败**

Run: `python -m pytest test_hesuan_ticheng.py -q`

Expected: FAIL with an import error for `hesuan_ticheng` or missing `calculate_profit`.

- [ ] **Step 3: 建立最小接口，使测试从导入错误变成断言失败**

```python
from dataclasses import dataclass


@dataclass
class CalculationResult:
    rows: list[dict]
    summary: dict


def calculate_profit(main_df, purchase_df, product_df=None, logistics_fee=1.0):
    return CalculationResult(rows=[], summary={})
```

- [ ] **Step 4: 运行测试，确认断言按预期失败**

Run: `python -m pytest test_hesuan_ticheng.py -q`

Expected: FAIL because `result.rows` is empty. This confirms the tests exercise the new behavior rather than existing code.

### Task 2: 实现清洗、订单归并和成本匹配

**Files:**
- Modify: `hesuan_ticheng.py`
- Test: `test_hesuan_ticheng.py`

- [ ] **Step 1: 添加失败测试覆盖排除状态、采购优先级和异常状态**

```python
def test_refunded_rejected_and_rejected_orders_are_excluded():
    main = pd.DataFrame([main_row(order="300", revenue=40)])
    purchase = pd.DataFrame([
        purchase_row("300-1", 9, status="已退款"),
        purchase_row("300-2", 8, status="已拒绝"),
        purchase_row("300-3", 7, status="已驳回订单"),
    ])
    result = calculate_profit(main, purchase, None)
    row = result.rows[0]
    assert row["采购成本"] is None
    assert row["排除采购行数"] == 3
    assert row["排除采购金额"] == 24
    assert row["核对状态"] == "仅有退款/拒绝采购记录，待商品表"


def test_valid_purchase_cost_has_priority_over_product_cost():
    main = pd.DataFrame([main_row(order="400", revenue=50, product_id="P4", quantity=2)])
    purchase = pd.DataFrame([purchase_row("400", 20)])
    products = pd.DataFrame([{"产品id": "P4", "产品成本($)": 3}])
    result = calculate_profit(main, purchase, products)
    row = result.rows[0]
    assert row["采用成本"] == 20
    assert row["成本来源"] == "采购订单"
    assert row["订单利润"] == 29


def test_blank_order_id_is_not_matched():
    main = pd.DataFrame([main_row(order="", revenue=20)])
    purchase = pd.DataFrame([purchase_row("", 5)])
    result = calculate_profit(main, purchase, None)
    assert result.rows[0]["核对状态"] == "订单编号为空，待核对"
```

- [ ] **Step 2: 运行新增测试，确认它们失败**

Run: `python -m pytest test_hesuan_ticheng.py -q`

Expected: FAIL on the new assertions while the test file imports successfully.

- [ ] **Step 3: 实现确定性的清洗和匹配函数**

在 `hesuan_ticheng.py` 中加入以下职责明确的函数：

```python
def normalize_text(value):
    """Convert empty-like values to '', remove BOM/zero-width chars and trim."""


def normalize_id(value):
    """Normalize identifiers for joins without changing the displayed source value."""


def parse_number(value):
    """Return float or None; never convert invalid or blank amounts to zero."""


def base_order_number(value):
    """Remove only a final -digits suffix, e.g. 100-2 -> 100."""


def is_excluded_purchase_status(value):
    """Return True when status contains 退款, 拒绝 or 驳回."""


def calculate_profit(main_df, purchase_df, product_df=None, logistics_fee=1.0):
    """Return CalculationResult with one output row per main-table order."""
```

`calculate_profit` 必须按以下顺序执行：清洗字段 → 按基础订单号分组采购记录 → 分离排除记录 → 计算有效采购成本 → 商品 ID 匹配商品表 → 计算物流费和利润。有效采购子订单数大于 0 时物流费为 `有效采购子订单数 * logistics_fee`；没有有效采购子订单但使用商品表成本时物流费为 `logistics_fee`；成本仍缺失时物流费为 0 且利润留空。

- [ ] **Step 4: 运行全部单元测试，确认核心规则通过**

Run: `python -m pytest test_hesuan_ticheng.py -q`

Expected: PASS with all tests green.

### Task 3: 实现文件自动发现、Excel 读取和结果导出

**Files:**
- Modify: `hesuan_ticheng.py`
- Test: `test_hesuan_ticheng.py`

- [ ] **Step 1: 写失败测试固定文件发现和输出规则**

```python
import os
from pathlib import Path
from hesuan_ticheng import find_input_files


def test_find_input_files_prefers_latest_main_without_copy_suffix(tmp_path):
    old = tmp_path / "导出订单包裹-旧.xlsx"
    new = tmp_path / "导出订单包裹-新.xlsx"
    copy = tmp_path / "导出订单包裹-新 - 副本.xlsx"
    old.touch()
    new.touch()
    copy.touch()
    os.utime(old, (100, 100))
    os.utime(copy, (200, 200))
    os.utime(new, (200, 200))
    (tmp_path / "采购订单.xlsx").touch()
    found = find_input_files(tmp_path)
    assert found.main.name == "导出订单包裹-新.xlsx"
    assert found.purchase.name == "采购订单.xlsx"
    assert found.product is None
```

- [ ] **Step 2: 运行测试，确认函数不存在或行为失败**

Run: `python -m pytest test_hesuan_ticheng.py::test_find_input_files_prefers_latest_main_without_copy_suffix -q`

Expected: FAIL because `find_input_files` is not implemented.

- [ ] **Step 3: 实现读取和导出**

实现以下接口：

```python
def find_input_files(folder: Path) -> InputFiles:
    """Find main, purchase and optional product files; raise clear FileNotFoundError."""


def read_input_table(path: Path) -> pd.DataFrame:
    """Read xlsx or CSV with encoding fallback and return string-preserving data."""


def export_result(result: CalculationResult, output_path: Path) -> None:
    """Write summary and detail to a styled workbook without overwriting inputs."""
```

主表候选按修改时间排序，并在时间相同的情况下优先不含“副本”的文件。采购表优先 `采购订单.xlsx`，再尝试 `采购订单.csv`；商品表缺失时返回 `None`。导出至少包含“利润核算”工作表，使用文本格式写入长订单号和商品 ID，输出文件名包含当前时间戳。

- [ ] **Step 4: 运行测试并检查导出文件存在**

Run: `python -m pytest test_hesuan_ticheng.py -q`

Expected: PASS with all tests green.

### Task 4: 接入命令行入口、批处理和文档

**Files:**
- Modify: `hesuan_ticheng.py`
- Create: `运行利润核算.bat`
- Create: `requirements.txt`
- Create: `README.md`

- [ ] **Step 1: 写命令行失败测试**

```python
def test_main_returns_nonzero_when_required_file_is_missing(tmp_path, monkeypatch):
    from hesuan_ticheng import main
    monkeypatch.chdir(tmp_path)
    assert main([]) != 0
```

- [ ] **Step 2: 运行测试，确认命令行入口失败**

Run: `python -m pytest test_hesuan_ticheng.py::test_main_returns_nonzero_when_required_file_is_missing -q`

Expected: FAIL because `main` is not implemented.

- [ ] **Step 3: 实现 CLI 和批处理入口**

`hesuan_ticheng.py` 提供：

```python
def main(argv=None) -> int:
    """Discover inputs, calculate, export, print summary, return process code."""
```

`运行利润核算.bat` 内容：

```bat
@echo off
setlocal
cd /d "%~dp0"
python hesuan_ticheng.py
if errorlevel 1 (
  echo.
  echo 利润核算失败，请阅读上面的错误信息。
) else (
  echo.
  echo 利润核算完成，结果已写入 输出 文件夹。
)
pause
```

`requirements.txt` 列出：

```text
pandas>=2.0
openpyxl>=3.1
pytest>=8.0
```

`README.md` 说明双击运行、命令行运行、输入文件命名、物流费规则、子订单归并、排除状态、商品表兜底和状态含义。

- [ ] **Step 4: 运行单元测试**

Run: `python -m pytest test_hesuan_ticheng.py -q`

Expected: PASS with all tests green.

### Task 5: 用当前真实文件进行端到端验证

**Files:**
- Modify: none
- Verify: `hesuan_ticheng.py`, current input workbooks, generated `输出/*.xlsx`

- [ ] **Step 1: 运行当前目录的程序**

Run: `python hesuan_ticheng.py`

Expected: exit code 0 and a new timestamped workbook in `输出`.

- [ ] **Step 2: 检查真实数据关键结果**

Use a short Python verification script to assert:

```python
assert summary["main_rows"] == 211
assert summary["matched_product"] == 1
assert summary["excluded_rows"] == 5
assert product_test_row["商品成本合计"] == 11
assert product_test_row["物流成本"] == 1
assert product_test_row["订单利润"] == 15.57
```

Expected: all assertions pass; the generated workbook opens and contains the summary and detail columns without formula errors.

- [ ] **Step 3: Render the generated workbook for visual inspection**

Open the generated workbook in Excel or use the existing spreadsheet rendering path. Verify long IDs remain readable text, dates display as dates, negative profits are visible, and pending statuses are highlighted.

- [ ] **Step 4: Run the final test suite again**

Run: `python -m pytest test_hesuan_ticheng.py -q`

Expected: PASS with no failures or errors.

## Self-review checklist

- The design covers automatic file discovery, sub-order grouping, exclusion, purchase priority, product fallback, quantity multiplication, per-fulfillment logistics, output and error states.
- The plan includes tests before implementation for every core behavior.
- The exact function names and output keys are reused consistently across tasks.
- No input workbook is overwritten; generated files go to `输出`.
- This directory is not a Git repository, so commit steps are intentionally omitted; verification is done through pytest, exit codes, output inspection and rendering.
