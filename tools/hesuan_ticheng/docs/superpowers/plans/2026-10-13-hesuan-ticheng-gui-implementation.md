# 订单利润核算桌面界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为现有利润核算程序增加标准 Windows Tkinter 界面，并通过 PyInstaller 构建可双击运行的 `订单利润核算.exe`。

**Architecture:** 保留 `hesuan_ticheng.py` 作为纯数据处理和 Excel 输出核心；新增 `hesuan_gui.py` 负责目录选择、文件状态、后台线程和结果展示。GUI 通过 `queue.Queue` 将后台核算结果安全地交回 Tk 主线程，统计展示使用纯函数，便于无窗口单元测试。

**Tech Stack:** Python 3.9+, Tkinter, pandas, openpyxl, pytest, PyInstaller。

---

### Task 1: 增加 GUI 统计和文件展示的可测试纯函数

**Files:**
- Create: `tools/hesuan_ticheng/hesuan_gui.py`
- Create: `tools/hesuan_ticheng/test_hesuan_gui.py`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path
from hesuan_ticheng import CalculationResult
from hesuan_gui import format_input_status, summarize_result


def test_format_input_status_marks_optional_product_file():
    status = format_input_status(Path("main.xlsx"), Path("purchase.xlsx"), None)
    assert status == {"主表": "main.xlsx", "采购表": "purchase.xlsx", "商品表": "未提供（可选）"}


def test_summarize_result_counts_profit_loss_and_pending():
    result = CalculationResult(
        rows=[
            {"订单利润": 12.5},
            {"订单利润": -2.0},
            {"订单利润": None},
        ],
        summary={"主订单数": 3, "利润合计": 10.5},
    )
    assert summarize_result(result) == {
        "主订单数": 3,
        "盈利订单数": 1,
        "亏损订单数": 1,
        "待核对订单数": 1,
        "利润合计": 10.5,
    }
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `python -m pytest tools/hesuan_ticheng/test_hesuan_gui.py -q`

Expected: FAIL because `hesuan_gui.py` and its pure functions do not exist.

- [ ] **Step 3: Implement the pure functions**

Add `format_input_status(main_path, purchase_path, product_path)` and `summarize_result(result)` to `hesuan_gui.py`. Keep imports of Tkinter below these functions or otherwise importable in a headless test environment.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tools/hesuan_ticheng/test_hesuan_gui.py -q`

Expected: 2 passed.

### Task 2: Implement the Tkinter window and background calculation

**Files:**
- Modify: `tools/hesuan_ticheng/hesuan_gui.py`
- Modify: `tools/hesuan_ticheng/test_hesuan_gui.py`

- [ ] **Step 1: Add the GUI behavior test seam**

Test that a worker helper calls `find_input_files`, reads the three input tables, calls `calculate_profit`, exports to the selected folder’s `输出` directory, and returns the output path plus result. Use `unittest.mock.patch` so the test does not create a Tk window or read real workbooks.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python -m pytest tools/hesuan_ticheng/test_hesuan_gui.py -q`

Expected: FAIL because the worker helper is not implemented.

- [ ] **Step 3: Implement the worker and window**

Implement:

```python
def run_calculation(folder: Path) -> tuple[CalculationResult, Path]: ...
class ProfitGui:
    def __init__(self, root: tk.Tk): ...
    def refresh_files(self) -> None: ...
    def choose_folder(self) -> None: ...
    def start_calculation(self) -> None: ...
    def poll_worker(self) -> None: ...
    def open_output(self) -> None: ...
```

Use `threading.Thread(daemon=True)` for `run_calculation`, a `queue.Queue` for completion/error messages, and `root.after(100, self.poll_worker)` while running. The window must disable the start button while running, show missing-file errors in the log, update five statistics after success, and enable “打开结果文件” only after `export_result` succeeds. `main()` creates the Tk root, initializes the selected directory to `Path(__file__).resolve().parent`, and starts `mainloop()`.

- [ ] **Step 4: Run all focused tests**

Run: `python -m pytest tools/hesuan_ticheng/test_hesuan_gui.py tools/hesuan_ticheng/test_hesuan_ticheng.py -q`

Expected: all existing calculation tests plus GUI tests pass.

### Task 3: Add Windows launchers and PyInstaller configuration

**Files:**
- Create: `tools/hesuan_ticheng/运行利润核算界面.bat`
- Create: `tools/hesuan_ticheng/build_exe.bat`
- Modify: `tools/hesuan_ticheng/requirements.txt`
- Modify: `tools/hesuan_ticheng/README.md`
- Modify: `tools/hesuan_ticheng/.gitignore`

- [ ] **Step 1: Add the launcher and build commands**

`运行利润核算界面.bat` changes to its own directory and runs `python hesuan_gui.py`. `build_exe.bat` installs/uses PyInstaller from the active environment and runs:

```bat
python -m PyInstaller --noconfirm --clean --onefile --windowed --name 订单利润核算 hesuan_gui.py
```

- [ ] **Step 2: Add the PyInstaller dependency and usage instructions**

Add `pyinstaller>=6.0` to `requirements.txt`. Document the GUI launcher, the build command, the expected `dist/订单利润核算.exe`, and the four output sheets in `README.md`.

- [ ] **Step 3: Ignore build caches and generated binaries**

Add `build/`, `dist/`, and `*.spec` to the folder `.gitignore`; keep source files and batch launchers tracked.

### Task 4: Verify the executable workflow and commit

**Files:**
- Modify: `tools/hesuan_ticheng/test_hesuan_gui.py` if verification finds a defect.

- [ ] **Step 1: Run syntax and focused tests**

Run:

```powershell
python -m py_compile tools/hesuan_ticheng/hesuan_gui.py tools/hesuan_ticheng/hesuan_ticheng.py
python -m pytest tools/hesuan_ticheng/test_hesuan_gui.py tools/hesuan_ticheng/test_hesuan_ticheng.py -q
```

Expected: syntax succeeds and all focused tests pass.

- [ ] **Step 2: Run the GUI calculation entry against real files**

Run the worker or CLI-equivalent calculation against `E:\project\crossborder-tools\tools\hesuan_ticheng`, then verify the generated workbook has `汇总`, `盈利订单`, `亏损订单`, and `待核对订单` sheets and that the loss sheet contains negative numeric `订单利润` values.

- [ ] **Step 3: Build the exe**

Run: `tools\hesuan_ticheng\build_exe.bat`

Expected: `tools/hesuan_ticheng/dist/订单利润核算.exe` exists.

- [ ] **Step 4: Commit the implementation**

```powershell
git add tools/hesuan_ticheng/hesuan_gui.py tools/hesuan_ticheng/test_hesuan_gui.py tools/hesuan_ticheng/运行利润核算界面.bat tools/hesuan_ticheng/build_exe.bat tools/hesuan_ticheng/requirements.txt tools/hesuan_ticheng/README.md tools/hesuan_ticheng/.gitignore
git commit -m "feat: add desktop gui for profit calculator"
```
