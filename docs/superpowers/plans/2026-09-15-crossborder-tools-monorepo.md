# Cross-border Tools Monorepo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the existing `amazon_check` repository as a multi-tool repository and add the standalone order-to-Hualun application while preserving both projects' Git history.

**Architecture:** Keep each desktop tool self-contained under `tools/<tool-name>` with its own source, dependencies, tests, templates, packaging definition, and README. Keep repository-wide design records under `docs/superpowers` and use the root README as a tool index. Do not introduce shared packages until multiple tools genuinely need the same implementation.

**Tech Stack:** Python 3, Tkinter, openpyxl, curl_cffi, PyInstaller, Git subtree.

---

### Task 1: Move the existing Miaoshou delivery tool

**Files:**
- Move: `app.py` → `tools/miaoshou-delivery/app.py`
- Move: `delivery_processor.py` → `tools/miaoshou-delivery/delivery_processor.py`
- Move: `requirements.txt` → `tools/miaoshou-delivery/requirements.txt`
- Move: `tests/` → `tools/miaoshou-delivery/tests/`
- Move: `README.md` → `tools/miaoshou-delivery/README.md`

- [ ] Create `tools/miaoshou-delivery`.
- [ ] Use `git mv` for every tracked file so history remains traceable.
- [ ] Run `python -m unittest discover -s tools/miaoshou-delivery/tests -p "test_*.py" -v` from `tools/miaoshou-delivery` and expect 9 passing tests.
- [ ] Commit with `refactor: move delivery tool into monorepo layout`.

### Task 2: Import the order-to-Hualun tool with history

**Files:**
- Add: `tools/order-to-hualun/**`

- [ ] Add `E:/project/order_template` as temporary remote `order-template`.
- [ ] Fetch its `main` branch.
- [ ] Run `git subtree add --prefix=tools/order-to-hualun order-template main` without `--squash` so its commit history remains available.
- [ ] Run `python -m unittest discover -s tools/order-to-hualun/tests -p "test_*.py" -v` from `tools/order-to-hualun` and expect 7 passing tests.

### Task 3: Add repository-level navigation and ignore rules

**Files:**
- Create: `README.md`
- Modify: `.gitignore`

- [ ] Write a root README that lists both tools, their locations, run commands, test commands, and packaging notes.
- [ ] Extend `.gitignore` with recursive Python cache, PyInstaller build, generated workbook, GUI runtime, editor, and Office lock-file patterns.
- [ ] Verify `git status --short` contains only intended source/documentation changes.
- [ ] Commit with `docs: add monorepo navigation and ignore rules`.

### Task 4: Verify and publish the branch

**Files:**
- Verify: all tracked project files

- [ ] Run both tools' complete unit-test suites.
- [ ] Compile all Python source files with `python -m py_compile`.
- [ ] Confirm no `build`, `dist`, generated workbook, runtime configuration, or cache files are tracked.
- [ ] Push `codex/monorepo-layout` to `origin`.
