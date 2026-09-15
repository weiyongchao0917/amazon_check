# 配送异常 SKU 工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a polished Windows desktop tool for previewing and executing grouped Miaoshou delivery-exception SKU operations.

**Architecture:** Keep ERP and workbook logic in `delivery_processor.py`; add a Tkinter UI in `app.py` that runs analysis and execution on a worker thread, receives progress callbacks, and writes the existing result workbook. Package metadata and README document safe credential handling.

**Tech Stack:** Python 3.10+, Tkinter/ttk, requests, openpyxl, unittest.

---

### Task 1: Add progress-aware processor APIs

**Files:**
- Modify: `delivery_processor.py`
- Test: `tests/test_delivery_processor.py`

- [ ] Add optional `progress_callback` and `stop_event` parameters to `process_delivery`.
- [ ] Invoke the callback once per group with group number, total, IDs, row count, and current status.
- [ ] Stop before the next group when the event is set and write already-collected results.
- [ ] Keep existing selection and payload behavior unchanged.
- [ ] Run `python -m unittest tests.test_delivery_processor -v`.

### Task 2: Build the desktop UI

**Files:**
- Create: `app.py`
- Test: `tests/test_app_helpers.py`

- [ ] Create a themed Tkinter window with file selectors, masked credential input, preview tree, progress bar, log panel, and explicit confirmation button.
- [ ] Run local workbook analysis in a background thread and show grouped preview counts.
- [ ] Run processor execution in a background thread, forwarding progress safely through `after`.
- [ ] Disable execution until validation succeeds and never print credentials.
- [ ] Test path validation and status filtering helpers without opening a window.

### Task 3: Document and package

**Files:**
- Create: `README.md`
- Create: `requirements.txt`
- Create: `.gitignore` additions if needed

- [ ] Document installation, launch command, workbook requirements, supported statuses, and credential handling.
- [ ] Pin minimum dependencies without including secrets or local output files.
- [ ] Run unit tests and a `python -m py_compile app.py delivery_processor.py` check.

### Task 4: Commit and publish

**Files:** all implementation files

- [ ] Review `git diff` for credentials and generated files.
- [ ] Commit with `feat: add delivery sku desktop tool`.
- [ ] Configure the user-provided GitHub repository as `origin` if absent and push the current branch.
