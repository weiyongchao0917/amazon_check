"""Amazon SKU 检查工具的 Windows Tkinter 图形界面。"""
from __future__ import annotations

import contextlib
import io
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import yaml

from sku_checker.paths import CONFIG_PATH, DEFAULT_REPORT_PATH, PROJECT_DIR, RUNTIME_DIR, STATE_PATH
from sku_checker.proxy_pool import ProxyPool, diagnose_proxy

_PROGRESS_RE = re.compile(r"^\[(\d+)\s*/\s*(\d+)\]\s*(.*)$")


def percent_to_ratio(value: str | float | int) -> float:
    """将界面百分数（例如 10 或 10%）转换为 0-1 小数。"""
    text = str(value).strip().removesuffix("%").strip()
    ratio = float(text) / 100.0
    if not 0 <= ratio <= 1:
        raise ValueError("差异阈值必须在 0% 到 100% 之间")
    return ratio


def parse_cli_progress_line(line: str) -> dict[str, Any] | None:
    """解析主流程打印的 [当前/总数] 行。"""
    match = _PROGRESS_RE.match(line.strip())
    if not match:
        return None
    current, total, detail = match.groups()
    asin = detail.split(maxsplit=1)[0] if detail else ""
    if asin == "-":
        asin = ""
    return {
        "current": int(current),
        "total": int(total),
        "asin": asin,
        "detail": detail,
    }


def build_proxy_config(
    *,
    mode: str,
    list_text: str,
    list_file: str,
    api_url: str,
    api_key: str,
    auth_user: str,
    auth_password: str,
    api_num: str | int,
    fixed_protocol: str = "http",
    fixed_host: str = "",
    fixed_port: str | int = "10000",
) -> dict[str, Any]:
    """Build the shared proxy configuration used by runs and diagnostics."""
    normalized_mode = str(mode or "none").strip().lower()
    if normalized_mode not in ("none", "list", "file", "api", "fixed"):
        raise ValueError("代理模式只允许直连、代理列表、代理文件、代理 API 或固定账密代理")
    try:
        amount = int(api_num)
    except (TypeError, ValueError) as exc:
        raise ValueError("代理 API 提取数量必须是整数") from exc
    if amount < 1:
        raise ValueError("代理 API 提取数量必须大于等于 1")
    protocol = str(fixed_protocol or "http").strip().lower()
    host = str(fixed_host or "").strip()
    try:
        port = int(fixed_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("固定代理端口必须是整数") from exc
    if normalized_mode == "fixed":
        if protocol not in ("http", "socks5"):
            raise ValueError("固定代理协议只允许 HTTP 或 SOCKS5")
        if not host:
            raise ValueError("固定账密代理需要填写代理主机")
        if not 1 <= port <= 65535:
            raise ValueError("固定代理端口必须在 1 到 65535 之间")
        if not str(auth_user or "").strip():
            raise ValueError("固定账密代理需要填写认证用户名")
        if not str(auth_password or ""):
            raise ValueError("固定账密代理需要填写认证密码")
    return {
        "enabled": normalized_mode != "none",
        "mode": normalized_mode,
        "protocol_default": "http",
        "list_file": str(list_file or "").strip() if normalized_mode == "file" else "",
        "list_text": str(list_text or "").strip() if normalized_mode == "list" else "",
        "api": {
            "url": str(api_url or "").strip() if normalized_mode == "api" else "",
            "key": str(api_key or "") if normalized_mode == "api" else "",
            "auth_user": str(auth_user or "") if normalized_mode == "api" else "",
            "auth_password": str(auth_password or "") if normalized_mode == "api" else "",
            "num": amount,
            "extra_params": {},
            "parser": "auto",
            "deadline_margin_seconds": 45,
            "min_pool_size": 5,
            "max_uses_per_proxy": 2,
        },
        "fixed": {
            "protocol": protocol,
            "host": host if normalized_mode == "fixed" else "",
            "port": port,
            "username": str(auth_user or "").strip() if normalized_mode == "fixed" else "",
            "password": str(auth_password or "") if normalized_mode == "fixed" else "",
        },
    }


def build_runtime_config(
    *, zip_code: str, price_multiplier: str | float,
    threshold_percent: str | float, latest_allowed_date: str,
    stock_warning_threshold: str | int = 5,
    workers: str | int = 3, request_timeout: str | int = 25,
    total_attempts: str | int = 3, delay_min: str | float = 1.5,
    delay_max: str | float = 3.5, max_consecutive_blocks: str | int = 8,
    proxy_mode: str = "none", proxy_list_text: str = "",
    proxy_list_file: str = "", proxy_api_url: str = "",
    proxy_api_key: str = "", proxy_auth_user: str = "",
    proxy_auth_password: str = "", proxy_api_num: str | int = 50,
    proxy_fixed_protocol: str = "http", proxy_fixed_host: str = "",
    proxy_fixed_port: str | int = "10000",
) -> dict[str, Any]:
    """生成 GUI 运行配置，并统一校验用户输入。"""
    zip_text = str(zip_code).strip()
    if not (zip_text.isdigit() and len(zip_text) == 5):
        raise ValueError("邮编必须是 5 位数字")
    deadline_text = str(latest_allowed_date or "").strip()
    try:
        from datetime import date

        deadline = date.fromisoformat(deadline_text)
        if deadline.isoformat() != deadline_text:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("允许最晚送达日期必须是 YYYY-MM-DD 格式，例如 2026-07-25") from exc
    try:
        multiplier = float(price_multiplier)
        worker_count = int(workers)
        timeout = int(request_timeout)
        attempts = int(total_attempts)
        delay_low, delay_high = float(delay_min), float(delay_max)
        block_limit = int(max_consecutive_blocks)
        api_num = int(proxy_api_num)
        stock_threshold = int(stock_warning_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("高级设置中的数值格式不正确") from exc
    if multiplier <= 0:
        raise ValueError("价格倍率必须大于 0")
    if worker_count not in (1, 3, 5):
        raise ValueError("线程数只允许 1、3、5")
    if timeout < 1:
        raise ValueError("请求超时必须大于等于 1 秒")
    if not 1 <= attempts <= 5:
        raise ValueError("总尝试次数必须在 1 到 5 之间")
    if delay_low < 0 or delay_high < delay_low:
        raise ValueError("请求间隔必须满足 0 <= 最小值 <= 最大值")
    if block_limit < 1:
        raise ValueError("连续拦截熔断值必须大于等于 1")
    if api_num < 1:
        raise ValueError("代理 API 提取数量必须大于等于 1")
    if stock_threshold < 1:
        raise ValueError("库存预警阈值必须大于等于 1")

    mode = str(proxy_mode or "none").strip().lower()
    aliases = {
        "direct": "none", "直连": "none", "list": "list", "file": "file",
        "api": "api", "fixed": "fixed", "固定账密代理": "fixed",
    }
    mode = aliases.get(mode, mode)
    if mode not in ("none", "list", "file", "api", "fixed"):
        raise ValueError("代理模式只允许直连、代理列表、代理文件、代理 API 或固定账密代理")
    list_text = str(proxy_list_text or "").strip()
    list_file = str(proxy_list_file or "").strip()
    api_url = str(proxy_api_url or "").strip()
    if mode == "list" and not list_text:
        raise ValueError("代理列表模式需要填写至少一条代理")
    if mode == "file":
        if not list_file:
            raise ValueError("代理文件模式需要选择 txt 文件")
        path = Path(list_file)
        if not path.is_file() or path.suffix.lower() != ".txt":
            raise ValueError("代理文件必须是存在的 txt 文件")
    if mode == "api" and not api_url:
        raise ValueError("代理 API 模式需要填写 URL")

    return {
        "output_dir": str(RUNTIME_DIR),
        "price_multiplier": multiplier,
        "price_threshold": percent_to_ratio(threshold_percent),
        "stock_warning_threshold": stock_threshold,
        "zip_code": zip_text,
        "delivery": {"latest_allowed_date": deadline.isoformat()},
        "request_timeout": timeout,
        "workers": worker_count,
        "max_retries": attempts - 1,
        "delay_between_requests": [delay_low, delay_high],
        "max_consecutive_blocks": block_limit,
        "impersonate": "chrome",
        "proxy": build_proxy_config(
            mode=mode,
            list_text=list_text,
            list_file=list_file,
            api_url=api_url,
            api_key=proxy_api_key,
            auth_user=proxy_auth_user,
            auth_password=proxy_auth_password,
            api_num=api_num,
            fixed_protocol=proxy_fixed_protocol,
            fixed_host=proxy_fixed_host,
            fixed_port=proxy_fixed_port,
        ),
        "resume": True,
        "state_file": str(STATE_PATH),
    }


class _QueueWriter(io.TextIOBase):
    def __init__(self, messages: queue.Queue[tuple[str, Any]]) -> None:
        self.messages = messages
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line:
                self.messages.put(("log", line))
        return len(text)

    def flush(self) -> None:
        if self.buffer:
            self.messages.put(("log", self.buffer))
            self.buffer = ""


class SkuCheckerGui:
    BG = "#101a29"
    PANEL = "#182538"
    INPUT = "#0d1725"
    TEXT = "#e8f0f7"
    MUTED = "#91a4b7"
    ACCENT = "#20c7e7"
    BLUE = "#1677a8"
    DANGER = "#b84a55"

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("Amazon SKU 检查工具")
        self.root.geometry("980x680")
        self.root.minsize(760, 560)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(DEFAULT_REPORT_PATH))
        self.zip_var = tk.StringVar(value="91730")
        self.multiplier_var = tk.StringVar(value="2.8")
        self.threshold_var = tk.StringVar(value="10")
        self.stock_threshold_var = tk.StringVar(value="5")
        self.deadline_var = tk.StringVar(value="")
        self.workers_var = tk.StringVar(value="3")
        self.proxy_summary_var = tk.StringVar(value="代理模式：直连")
        self.advanced = {
            "request_timeout": "25", "total_attempts": "3",
            "delay_min": "1.5", "delay_max": "3.5",
            "max_consecutive_blocks": "8",
        }
        self.proxy_settings = {
            "mode": "none", "list_text": "", "list_file": "",
            "api_url": "", "api_key": "", "auth_user": "",
            "auth_password": "", "api_num": "50",
            "fixed_protocol": "http", "fixed_host": "", "fixed_port": "10000",
        }
        self.current_var = tk.StringVar(value="—")
        self.status_var = tk.StringVar(value="就绪")
        self.progress_text = tk.StringVar(value="0 / 0")
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cancel_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.last_report: Path | None = None
        self.closing = False

        self._configure_styles()
        self._build()
        self.root.after(100, self._drain_messages)

    def _configure_styles(self) -> None:
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Panel.TLabel", background=self.PANEL, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background=self.BG, foreground=self.TEXT, font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("TEntry", fieldbackground=self.INPUT, foreground=self.TEXT, insertcolor=self.TEXT, bordercolor="#30445b", padding=7)
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(14, 8), background="#293b50", foreground=self.TEXT, borderwidth=0)
        style.map("TButton", background=[("active", "#36516d"), ("disabled", "#202d3c")])
        style.configure("Accent.TButton", background=self.BLUE, foreground="white")
        style.map("Accent.TButton", background=[("active", "#1b94c6")])
        style.configure("Danger.TButton", background=self.DANGER, foreground="white")
        style.configure("Cyan.Horizontal.TProgressbar", troughcolor=self.INPUT, background=self.ACCENT, bordercolor=self.INPUT, lightcolor=self.ACCENT, darkcolor=self.ACCENT)

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Amazon SKU 检查工具", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="TikTok SKU 与 Amazon 价格、配送和货源状态核验", style="Muted.TLabel").pack(anchor="w", pady=(2, 18))

        panel = ttk.Frame(outer, style="Panel.TFrame", padding=18)
        panel.pack(fill="x")
        panel.columnconfigure(1, weight=1)
        self._path_row(panel, 0, "TikTok Excel", self.input_var, self._choose_input)
        self._path_row(panel, 1, "输出报告", self.output_var, self._choose_output)

        params = ttk.Frame(panel, style="Panel.TFrame")
        params.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        for i in range(6):
            params.columnconfigure(i, weight=1)
        fields = [
            ("美国邮编", self.zip_var, "91730"),
            ("价格倍率", self.multiplier_var, "2.8"),
            ("差异阈值（%）", self.threshold_var, "10"),
            ("库存预警阈值", self.stock_threshold_var, "5"),
            ("允许最晚送达日期（YYYY-MM-DD）", self.deadline_var, ""),
        ]
        for col, (label, var, _default) in enumerate(fields):
            box = ttk.Frame(params, style="Panel.TFrame")
            box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
            ttk.Label(box, text=label, style="Panel.TLabel").pack(anchor="w", pady=(0, 5))
            ttk.Entry(box, textvariable=var).pack(fill="x")
        worker_box = ttk.Frame(params, style="Panel.TFrame")
        worker_box.grid(row=0, column=5, sticky="ew", padx=(6, 0))
        ttk.Label(worker_box, text="线程数", style="Panel.TLabel").pack(anchor="w", pady=(0, 5))
        self.workers_combo = ttk.Combobox(worker_box, textvariable=self.workers_var, values=("1", "3", "5"), state="readonly", width=6)
        self.workers_combo.pack(fill="x")

        settings_bar = ttk.Frame(panel, style="Panel.TFrame")
        settings_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.advanced_button = ttk.Button(settings_bar, text="高级设置", command=self._show_advanced_settings)
        self.advanced_button.pack(side="left")
        self.proxy_button = ttk.Button(settings_bar, text="代理设置", command=self._show_proxy_settings)
        self.proxy_button.pack(side="left", padx=8)
        ttk.Label(settings_bar, textvariable=self.proxy_summary_var, style="Panel.TLabel").pack(side="left", padx=(8, 0))
        ttk.Label(settings_bar, text="当前线程数：", style="Panel.TLabel").pack(side="left", padx=(18, 0))
        ttk.Label(settings_bar, textvariable=self.workers_var, style="Panel.TLabel").pack(side="left")

        # 主要操作放在参数区下方，确保高 DPI / 小屏幕时也始终可见。
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(12, 0), before=None)
        self.start_button = ttk.Button(buttons, text="开始新检查", style="Accent.TButton", command=lambda: self._start(False))
        self.start_button.pack(side="left")
        self.resume_button = ttk.Button(buttons, text="继续上次", command=lambda: self._start(True))
        self.resume_button.pack(side="left", padx=8)
        self.retry_button = ttk.Button(
            buttons, text="重试失败项", command=lambda: self._start(True, retry_failed_only=True)
        )
        self.retry_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", style="Danger.TButton", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")
        self.open_button = ttk.Button(buttons, text="打开报告", command=self._open_report, state="disabled")
        self.open_button.pack(side="right")

        status = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        status.pack(fill="x", pady=12)
        status.columnconfigure(1, weight=1)
        ttk.Label(status, text="状态", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.status_var, style="Panel.TLabel").grid(row=0, column=1, sticky="w", padx=12)
        ttk.Label(status, textvariable=self.progress_text, style="Panel.TLabel").grid(row=0, column=2, sticky="e")
        ttk.Label(status, text="当前 SKU", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(status, textvariable=self.current_var, style="Panel.TLabel").grid(row=1, column=1, columnspan=2, sticky="w", padx=12, pady=(8, 0))
        self.progress = ttk.Progressbar(status, style="Cyan.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))

        log_frame = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        log_frame.pack(fill="both", expand=True)
        ttk.Label(log_frame, text="运行日志", style="Panel.TLabel").pack(anchor="w", pady=(0, 8))
        self.log = tk.Text(log_frame, bg=self.INPUT, fg="#c9d8e6", insertbackground=self.TEXT, relief="flat", wrap="word", font=("Consolas", 9), padx=10, pady=8, state="disabled")
        self.log.pack(fill="both", expand=True)

    def _path_row(self, parent: Any, row: int, label: str, variable: Any, command: Any) -> None:
        self.ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        self.ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        self.ttk.Button(parent, text="浏览…", command=command).grid(row=row, column=2, padx=(10, 0), pady=5)

    def _show_advanced_settings(self) -> None:
        from tkinter import messagebox
        win = self.tk.Toplevel(self.root)
        win.title("高级设置")
        win.transient(self.root)
        win.grab_set()
        body = self.ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        labels = [
            ("request_timeout", "请求超时（秒）"),
            ("total_attempts", "总尝试次数（1-5）"),
            ("delay_min", "请求间隔最小（秒）"),
            ("delay_max", "请求间隔最大（秒）"),
            ("max_consecutive_blocks", "连续拦截熔断值"),
        ]
        variables = {key: self.tk.StringVar(value=self.advanced[key]) for key, _ in labels}
        for row, (key, label) in enumerate(labels):
            self.ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
            self.ttk.Entry(body, textvariable=variables[key], width=24).grid(row=row, column=1, sticky="ew", pady=5)
        body.columnconfigure(1, weight=1)

        def save() -> None:
            try:
                timeout = int(variables["request_timeout"].get())
                attempts = int(variables["total_attempts"].get())
                low = float(variables["delay_min"].get())
                high = float(variables["delay_max"].get())
                blocks = int(variables["max_consecutive_blocks"].get())
                if timeout < 1 or not 1 <= attempts <= 5 or low < 0 or high < low or blocks < 1:
                    raise ValueError("请检查范围：超时和熔断值 >= 1，总尝试次数 1-5，且 0 <= 最小间隔 <= 最大间隔")
            except ValueError as exc:
                messagebox.showerror("参数错误", str(exc), parent=win)
                return
            self.advanced.update({key: var.get().strip() for key, var in variables.items()})
            win.destroy()

        actions = self.ttk.Frame(body)
        actions.grid(row=len(labels), column=0, columnspan=2, sticky="e", pady=(14, 0))
        self.ttk.Button(actions, text="取消", command=win.destroy).pack(side="left")
        self.ttk.Button(actions, text="确定", style="Accent.TButton", command=save).pack(side="left", padx=(8, 0))

    def _show_proxy_settings(self) -> None:
        from tkinter import filedialog, messagebox
        win = self.tk.Toplevel(self.root)
        win.title("代理设置")
        win.geometry("720x820")
        win.transient(self.root)
        win.grab_set()
        body = self.ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        mode_labels = {
            "直连": "none", "代理列表": "list", "代理文件": "file",
            "代理 API": "api", "固定账密代理": "fixed",
        }
        reverse_labels = {value: key for key, value in mode_labels.items()}
        mode_var = self.tk.StringVar(value=reverse_labels.get(self.proxy_settings["mode"], "直连"))
        self.ttk.Label(body, text="代理模式").grid(row=0, column=0, sticky="w", pady=5)
        mode_combo = self.ttk.Combobox(body, textvariable=mode_var, values=tuple(mode_labels), state="readonly")
        mode_combo.grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)

        self.ttk.Label(body, text="代理列表（每行一条）").grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 5))
        list_text = self.tk.Text(body, height=7, wrap="none")
        list_text.grid(row=2, column=0, columnspan=3, sticky="nsew")
        list_text.insert("1.0", self.proxy_settings["list_text"])

        file_var = self.tk.StringVar(value=self.proxy_settings["list_file"])
        self.ttk.Label(body, text="代理文件（txt）").grid(row=3, column=0, sticky="w", pady=(10, 5))
        self.ttk.Entry(body, textvariable=file_var).grid(row=3, column=1, sticky="ew", pady=(10, 5))
        def browse_file() -> None:
            path = filedialog.askopenfilename(title="选择代理 txt 文件", filetypes=[("文本文件", "*.txt")], parent=win)
            if path:
                file_var.set(path)
        self.ttk.Button(body, text="浏览…", command=browse_file).grid(row=3, column=2, padx=(8, 0), pady=(10, 5))

        api_fields = [
            ("fixed_protocol", "固定代理协议", False),
            ("fixed_host", "固定代理主机", False),
            ("fixed_port", "固定代理端口", False),
            ("api_url", "API URL", False), ("api_key", "Key", False),
            ("auth_user", "认证用户名", False), ("auth_password", "认证密码", True),
            ("api_num", "提取数量", False),
        ]
        api_vars = {key: self.tk.StringVar(value=self.proxy_settings[key]) for key, _, _ in api_fields}
        for offset, (key, label, secret) in enumerate(api_fields, 4):
            self.ttk.Label(body, text=label).grid(row=offset, column=0, sticky="w", pady=5)
            self.ttk.Entry(body, textvariable=api_vars[key], show="*" if secret else "").grid(row=offset, column=1, columnspan=2, sticky="ew", pady=5)
        self.ttk.Label(
            body,
            text="API URL 中的 {num}、count 或 num 会使用“提取数量”覆盖。",
            style="Muted.TLabel",
        ).grid(row=12, column=0, columnspan=3, sticky="w", pady=(8, 3))
        test_result = self.tk.Text(body, height=6, wrap="word", state="disabled")
        test_result.grid(row=13, column=0, columnspan=3, sticky="nsew", pady=(3, 8))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)
        body.rowconfigure(13, weight=1)

        def current_values() -> dict[str, str]:
            return {
                "mode": mode_labels[mode_var.get()],
                "list_text": list_text.get("1.0", "end").strip(),
                "list_file": file_var.get().strip(),
                **{key: var.get().strip() for key, var in api_vars.items()},
            }

        def show_test_result(text: str) -> None:
            test_result.configure(state="normal")
            test_result.delete("1.0", "end")
            test_result.insert("1.0", text)
            test_result.configure(state="disabled")

        def test_proxy() -> None:
            values = current_values()
            try:
                config = build_proxy_config(
                    mode=values["mode"],
                    list_text=values["list_text"],
                    list_file=values["list_file"],
                    api_url=values["api_url"],
                    api_key=values["api_key"],
                    auth_user=values["auth_user"],
                    auth_password=values["auth_password"],
                    api_num=values["api_num"],
                    fixed_protocol=values["fixed_protocol"],
                    fixed_host=values["fixed_host"],
                    fixed_port=values["fixed_port"],
                )
                if not config["enabled"]:
                    raise ValueError("当前是直连模式，请先选择一种代理模式。")
            except ValueError as exc:
                messagebox.showerror("代理配置错误", str(exc), parent=win)
                return
            test_button.configure(state="disabled")
            show_test_result("正在提取代理并测试 HTTPS/Amazon，请稍候……")
            result_queue: queue.Queue[Any] = queue.Queue()

            def work() -> None:
                result_queue.put(diagnose_proxy(ProxyPool(config)))

            def poll() -> None:
                if not win.winfo_exists():
                    return
                try:
                    result = result_queue.get_nowait()
                except queue.Empty:
                    win.after(100, poll)
                    return
                test_button.configure(state="normal")
                show_test_result(result.summary())

            threading.Thread(target=work, name="proxy-diagnostic", daemon=True).start()
            win.after(100, poll)

        def save() -> None:
            values = current_values()
            mode = values["mode"]
            try:
                if int(values["api_num"]) < 1:
                    raise ValueError("提取数量必须大于等于 1")
                if mode == "list" and not values["list_text"]:
                    raise ValueError("代理列表模式需要填写至少一条代理")
                if mode == "file" and (not Path(values["list_file"]).is_file() or Path(values["list_file"]).suffix.lower() != ".txt"):
                    raise ValueError("请选择存在的 txt 代理文件")
                if mode == "api" and not values["api_url"]:
                    raise ValueError("代理 API 模式需要填写 URL")
                build_proxy_config(
                    mode=mode,
                    list_text=values["list_text"],
                    list_file=values["list_file"],
                    api_url=values["api_url"],
                    api_key=values["api_key"],
                    auth_user=values["auth_user"],
                    auth_password=values["auth_password"],
                    api_num=values["api_num"],
                    fixed_protocol=values["fixed_protocol"],
                    fixed_host=values["fixed_host"],
                    fixed_port=values["fixed_port"],
                )
            except ValueError as exc:
                messagebox.showerror("参数错误", str(exc), parent=win)
                return
            self.proxy_settings.update(values)
            summaries = {
                "none": "代理模式：直连", "list": "代理模式：代理列表",
                "file": "代理模式：代理文件", "api": "代理模式：代理 API",
                "fixed": "代理模式：固定账密代理",
            }
            self.proxy_summary_var.set(summaries[mode])
            win.destroy()

        actions = self.ttk.Frame(body)
        actions.grid(row=14, column=0, columnspan=3, sticky="e", pady=(8, 0))
        test_button = self.ttk.Button(actions, text="测试代理", command=test_proxy)
        test_button.pack(side="left", padx=(0, 16))
        self.ttk.Button(actions, text="取消", command=win.destroy).pack(side="left")
        self.ttk.Button(actions, text="确定", style="Accent.TButton", command=save).pack(side="left", padx=(8, 0))

    def _choose_input(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="选择 TikTok Excel", filetypes=[("Excel 文件", "*.xlsx *.xlsm *.xls"), ("所有文件", "*.*")])
        if path:
            self.input_var.set(path)
            if not self.output_var.get().strip():
                self.output_var.set(str(Path(path).with_name("SKU检查报告.xlsx")))

    def _choose_output(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(title="选择报告保存位置", defaultextension=".xlsx", filetypes=[("Excel 报告", "*.xlsx")])
        if path:
            self.output_var.set(path)

    def _validate_and_write_config(self) -> tuple[Path, Path]:
        input_text = self.input_var.get().strip()
        output_text = self.output_var.get().strip()
        input_path = Path(input_text)
        if not input_path.is_file():
            raise ValueError("请选择有效的 TikTok Excel 文件")
        if not output_text:
            raise ValueError("请选择输出报告路径")
        output_path = Path(output_text)
        if output_path.suffix.lower() != ".xlsx":
            output_path = output_path.with_suffix(".xlsx")
            self.output_var.set(str(output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = build_runtime_config(
            zip_code=self.zip_var.get(), price_multiplier=self.multiplier_var.get(),
            threshold_percent=self.threshold_var.get(), latest_allowed_date=self.deadline_var.get(),
            stock_warning_threshold=self.stock_threshold_var.get(),
            workers=self.workers_var.get(), **self.advanced,
            proxy_mode=self.proxy_settings["mode"],
            proxy_list_text=self.proxy_settings["list_text"],
            proxy_list_file=self.proxy_settings["list_file"],
            proxy_api_url=self.proxy_settings["api_url"],
            proxy_api_key=self.proxy_settings["api_key"],
            proxy_auth_user=self.proxy_settings["auth_user"],
            proxy_auth_password=self.proxy_settings["auth_password"],
            proxy_api_num=self.proxy_settings["api_num"],
            proxy_fixed_protocol=self.proxy_settings["fixed_protocol"],
            proxy_fixed_host=self.proxy_settings["fixed_host"],
            proxy_fixed_port=self.proxy_settings["fixed_port"],
        )
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return input_path, output_path

    def _start(self, resume: bool, *, retry_failed_only: bool = False) -> None:
        from tkinter import messagebox
        if self.worker and self.worker.is_alive():
            return
        try:
            input_path, output_path = self._validate_and_write_config()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc), parent=self.root)
            return
        if self.proxy_settings["mode"] == "none" and self.workers_var.get() == "5":
            if not messagebox.askyesno(
                "直连 5 线程提示",
                "当前为直连且使用 5 线程，更容易触发访问限制，推荐改用 3 线程。\n\n仍要继续吗？",
                parent=self.root,
            ):
                return
        self.cancel_event = threading.Event()
        self.last_report = None
        self.progress["value"] = 0
        self.progress_text.set("0 / 0")
        self.current_var.set("—")
        self.status_var.set("正在启动…")
        self._set_running(True)
        self._append_log("=" * 68)
        action = "只重试上次取数失败项" if retry_failed_only else ("继续上次检查" if resume else "开始新的检查")
        self._append_log(action)
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(input_path, output_path, resume, retry_failed_only), daemon=True,
        )
        self.worker.start()

    def _run_worker(
        self, input_path: Path, output_path: Path, resume: bool, retry_failed_only: bool = False,
    ) -> None:
        from sku_checker.main import run
        writer = _QueueWriter(self.messages)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                report = run(
                    str(input_path), config_path=str(CONFIG_PATH), output=str(output_path),
                    resume_override=resume, progress_callback=lambda data: self.messages.put(("progress", data)),
                    cancel_event=self.cancel_event, retry_failed_only=retry_failed_only,
                )
            writer.flush()
            self.messages.put(("finished", report))
        except BaseException as exc:
            writer.flush()
            self.messages.put(("error", exc))

    def _drain_messages(self) -> None:
        from tkinter import messagebox
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(data))
                    parsed = parse_cli_progress_line(str(data))
                    if parsed:
                        self._apply_progress(parsed)
                elif kind == "progress":
                    self._apply_progress(data)
                    if data.get("event") != "cooldown":
                        self.status_var.set(str(data.get("status") or "运行中"))
                    if data.get("report_path"):
                        self.last_report = Path(data["report_path"])
                elif kind == "finished":
                    self.last_report = Path(data)
                    self.status_var.set("已停止，部分报告已生成" if self.cancel_event and self.cancel_event.is_set() else "检查完成")
                    self._set_running(False)
                elif kind == "error":
                    self.status_var.set("运行失败")
                    self._append_log(f"运行失败: {type(data).__name__}: {data}")
                    self._set_running(False)
                    if not self.closing:
                        messagebox.showerror("运行失败", str(data), parent=self.root)
        except queue.Empty:
            pass
        if self.closing and not (self.worker and self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(100, self._drain_messages)

    def _apply_progress(self, data: dict[str, Any]) -> None:
        current, total = int(data.get("current", 0)), int(data.get("total", 0))
        self.progress["value"] = current * 100 / total if total else 0
        self.progress_text.set(f"{current} / {total}")
        if data.get("asin"):
            self.current_var.set(str(data["asin"]))
        if data.get("event") == "cooldown":
            level = int(data.get("cooldown_level", 0))
            remaining = int(data.get("remaining_seconds", 0))
            minutes, seconds = divmod(remaining, 60)
            self.status_var.set(
                f"第{level}次熔断冷却中：已完成 {current}/{total}，剩余 {minutes:02d}:{seconds:02d}"
            )
            self.current_var.set("冷却中，停止按钮可随时终止")

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.resume_button.configure(state=state)
        self.retry_button.configure(state=state)
        self.advanced_button.configure(state=state)
        self.proxy_button.configure(state=state)
        self.workers_combo.configure(state="disabled" if running else "readonly")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.open_button.configure(state="normal" if self.last_report and self.last_report.exists() else "disabled")

    def _stop(self) -> None:
        if self.cancel_event and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.status_var.set("正在停止…冷却会立即结束；当前请求完成后生成部分报告")
            self.stop_button.configure(state="disabled")
            self._append_log("已请求停止；等待当前 SKU 完成并保存断点。")

    def _open_report(self) -> None:
        from tkinter import messagebox
        path = self.last_report or Path(self.output_var.get().strip())
        if not path.exists():
            messagebox.showwarning("报告不存在", "尚未生成报告。", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("无法打开报告", str(exc), parent=self.root)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.closing = True
            self._stop()
            self.root.withdraw()
        else:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    SkuCheckerGui().run()


if __name__ == "__main__":
    main()
