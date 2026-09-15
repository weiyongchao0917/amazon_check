"""Tkinter GUI for converting order exports into the bundled Hualun template."""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from typing import Any

from sku_checker.order_config import default_order_config, load_order_config, save_order_config
from sku_checker.order_runner import run_order_conversion
from sku_checker.paths import ORDER_CONFIG_PATH, ORDER_TEMPLATE_PATH


def validate_order_config(config: dict[str, Any]) -> None:
    if not str(config.get("input_excel") or "").strip():
        raise ValueError("请选择订单 Excel 文件")
    if not Path(str(config["input_excel"])).is_file():
        raise ValueError("订单 Excel 文件不存在")
    zip_code = str(config.get("zip_code") or "").strip()
    if not (zip_code.isdigit() and len(zip_code) == 5):
        raise ValueError("邮编必须是 5 位数字")
    for key, label in (("phone", "联系电话"), ("address1", "收货地址1"), ("city", "城市"), ("state", "州/省")):
        if not str(config.get(key) or "").strip():
            raise ValueError(f"请填写{label}")
    if not Path(str(config.get("template_path") or ORDER_TEMPLATE_PATH)).is_file():
        raise FileNotFoundError("程序内置的花轮模板不存在")


class OrderGui:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.root.title("订单转花轮采购单")
        self.root.geometry("880x700")
        self.root.minsize(760, 600)
        self.vars: dict[str, Any] = {}
        loaded = load_order_config(ORDER_CONFIG_PATH)
        for key, value in loaded.items():
            self.vars[key] = tk.StringVar(value=str(value or ""))
        self.vars["template_path"] = tk.StringVar(value=str(ORDER_TEMPLATE_PATH))
        self.status = tk.StringVar(value="就绪")
        self.progress_text = tk.StringVar(value="0 / 0")
        self.current = tk.StringVar(value="—")
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cancel_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.last_output: Path | None = None
        self._configure_style()
        self._build()
        self.root.after(100, self._drain)

    def _configure_style(self) -> None:
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", padding=5)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Muted.TLabel", foreground="#5b6875")
        style.configure("Accent.TButton", padding=(12, 7))

    def _build(self) -> None:
        from tkinter import filedialog
        frame = self.ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        self.ttk.Label(frame, text="订单转花轮采购单", style="Title.TLabel").pack(anchor="w")
        self.ttk.Label(frame, text="选择订单表后查询 Amazon 价格并生成内置模板", style="Muted.TLabel").pack(anchor="w", pady=(2, 14))
        paths = self.ttk.LabelFrame(frame, text="文件")
        paths.pack(fill="x", pady=(0, 10))
        paths.columnconfigure(1, weight=1)
        self._path_row(paths, 0, "订单 Excel", "input_excel", lambda: self._browse_input(filedialog))
        self._path_row(paths, 1, "内置花轮模板", "template_path", None)
        self._path_row(paths, 2, "输出目录", "output_dir", lambda: self._browse_output(filedialog))
        fields = self.ttk.LabelFrame(frame, text="收货地址配置（可修改，自动保存）")
        fields.pack(fill="x", pady=(0, 10))
        for col in range(4): fields.columnconfigure(col, weight=1)
        labels = [("store_name", "店铺名称"), ("recipient_prefix", "收货人前缀"), ("phone", "联系电话"), ("zip_code", "邮编"), ("address1", "收货地址1"), ("address2", "收货地址2"), ("city", "城市"), ("state", "州/省")]
        for i, (key, label) in enumerate(labels):
            row, col = divmod(i, 4)
            self.ttk.Label(fields, text=label).grid(row=row * 2, column=col, sticky="w", padx=5, pady=(5, 0))
            self.ttk.Entry(fields, textvariable=self.vars[key]).grid(row=row * 2 + 1, column=col, sticky="ew", padx=5, pady=(0, 5))
        actions = self.ttk.Frame(frame)
        actions.pack(fill="x", pady=(2, 8))
        self.start_button = self.ttk.Button(actions, text="开始处理", style="Accent.TButton", command=lambda: self._start(False))
        self.start_button.pack(side="left")
        self.retry_button = self.ttk.Button(actions, text="只重试失败项", command=lambda: self._start(True))
        self.retry_button.pack(side="left", padx=8)
        self.stop_button = self.ttk.Button(actions, text="停止", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")
        self.open_button = self.ttk.Button(actions, text="打开输出", command=self._open, state="disabled")
        self.open_button.pack(side="right")
        status = self.ttk.LabelFrame(frame, text="运行状态")
        status.pack(fill="x", pady=(0, 8))
        self.ttk.Label(status, textvariable=self.status).pack(anchor="w")
        self.ttk.Label(status, textvariable=self.progress_text).pack(anchor="e")
        self.ttk.Label(status, text="当前 SKU：").pack(side="left")
        self.ttk.Label(status, textvariable=self.current).pack(side="left")
        self.progress = self.ttk.Progressbar(status, mode="determinate")
        self.progress.pack(fill="x", padx=5, pady=6)
        self.log = self.tk.Text(frame, height=15, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

    def _path_row(self, parent, row: int, label: str, key: str, command) -> None:
        self.ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=6)
        entry = self.ttk.Entry(parent, textvariable=self.vars[key], state="readonly" if key == "template_path" else "normal")
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=6)
        if command:
            self.ttk.Button(parent, text="浏览…", command=command).grid(row=row, column=2, padx=6, pady=6)

    def _browse_input(self, dialog) -> None:
        path = dialog.askopenfilename(title="选择订单 Excel", filetypes=[("Excel", "*.xlsx *.xlsm"), ("所有文件", "*.*")])
        if path:
            self.vars["input_excel"].set(path)
            if not self.vars["output_dir"].get(): self.vars["output_dir"].set(str(Path(path).parent))

    def _browse_output(self, dialog) -> None:
        path = dialog.askdirectory(title="选择输出目录")
        if path: self.vars["output_dir"].set(path)

    def _config(self) -> dict[str, str]:
        return {key: var.get().strip() for key, var in self.vars.items()}

    def _start(self, retry: bool) -> None:
        if self.worker and self.worker.is_alive(): return
        from tkinter import messagebox
        config = self._config()
        try: validate_order_config(config)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc), parent=self.root); return
        save_order_config(ORDER_CONFIG_PATH, config)
        self.cancel_event = threading.Event(); self.status.set("正在处理…"); self._set_running(True)
        self.worker = threading.Thread(target=self._work, args=(config, retry), daemon=True); self.worker.start()

    def _work(self, config: dict[str, str], retry: bool) -> None:
        try:
            output = run_order_conversion(config["input_excel"], config, cancel_event=self.cancel_event, retry_failed_only=retry, progress_callback=lambda event: self.messages.put(("progress", event)))
            self.messages.put(("finished", output))
        except BaseException as exc:
            self.messages.put(("error", exc))

    def _drain(self) -> None:
        from tkinter import messagebox
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == "progress":
                    current, total = int(data.get("current", 0)), int(data.get("total", 0)); self.progress["maximum"] = max(total, 1); self.progress["value"] = current; self.progress_text.set(f"{current} / {total}"); self.current.set(data.get("sku", "")); self._append(str(data))
                elif kind == "finished":
                    self.last_output = Path(data); self.status.set("完成"); self._set_running(False); self._append(f"输出：{data}")
                elif kind == "error":
                    self.status.set("运行失败"); self._set_running(False); self._append(str(data)); messagebox.showerror("运行失败", str(data), parent=self.root)
        except queue.Empty: pass
        self.root.after(100, self._drain)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal"); self.log.insert("end", text + "\n"); self.log.see("end"); self.log.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state); self.retry_button.configure(state=state); self.stop_button.configure(state="normal" if running else "disabled"); self.open_button.configure(state="normal" if self.last_output and self.last_output.exists() else "disabled")

    def _stop(self) -> None:
        if self.cancel_event: self.cancel_event.set(); self.status.set("正在停止…")

    def _open(self) -> None:
        if self.last_output and self.last_output.exists(): os.startfile(str(self.last_output))

    def run(self) -> None:
        self.root.mainloop()
