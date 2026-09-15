"""Miaoshou delivery-exception SKU desktop operator."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from delivery_processor import DELIVERY_STATUSES, _read_workbooks, process_delivery, normalize


BG = "#0f172a"
PANEL = "#172033"
PANEL_2 = "#1e293b"
TEXT = "#e5eefb"
MUTED = "#91a4bf"
ACCENT = "#38bdf8"
GREEN = "#34d399"
ORANGE = "#fbbf24"


def analyze_paths(report: str, source: str) -> dict:
    rows, _ = _read_workbooks(Path(report), Path(source))
    groups = {}
    for row in rows:
        key = (normalize(row.get("全球产品ID")), normalize(row.get("_shopId")))
        groups.setdefault(key, []).append(row)
    return {"rows": rows, "groups": groups, "row_count": len(rows), "group_count": len(groups)}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Miaoshou · 配送异常 SKU 处理台")
        self.geometry("1180x780")
        self.minsize(980, 680)
        self.configure(bg=BG)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.analysis = None
        self._styles()
        self._build()
        self.after(120, self._drain_events)

    def _styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("App.TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 23, "bold"))
        s.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 10))
        s.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Accent.TButton", background=ACCENT, foreground="#082f49", padding=(16, 9), font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("disabled", "#475569"), ("active", "#7dd3fc")])
        s.configure("Ghost.TButton", background=PANEL_2, foreground=TEXT, padding=(12, 8))
        s.configure("Treeview", background=PANEL_2, fieldbackground=PANEL_2, foreground=TEXT, rowheight=30, borderwidth=0)
        s.configure("Treeview.Heading", background="#263449", foreground="#cfe3fa", font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", "#075985")])
        s.configure("Horizontal.TProgressbar", troughcolor=PANEL_2, background=ACCENT, bordercolor=PANEL_2, lightcolor=ACCENT, darkcolor=ACCENT)

    def _build(self):
        root = ttk.Frame(self, style="App.TFrame", padding=28)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="配送异常 SKU 处理台", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="按商品分组核对妙手详情，安全删除无法次日达的规格", style="Sub.TLabel").pack(anchor="w", pady=(3, 18))

        input_panel = ttk.Frame(root, style="Panel.TFrame", padding=18)
        input_panel.pack(fill="x")
        self.report_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.cookie_var = tk.StringVar()
        self._file_row(input_panel, 0, "异常汇总表", self.report_var, "选择包含“异常汇总”sheet 的 Excel")
        self._file_row(input_panel, 1, "SKU 源表", self.source_var, "用于通过 SKU ID 找店铺 ID")
        self._file_row(input_panel, 2, "结果文件", self.output_var, "默认生成在异常汇总表同目录")
        ttk.Label(input_panel, text="妙手 Cookie / Token", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(input_panel, textvariable=self.cookie_var, show="•", width=85).grid(row=3, column=1, sticky="ew", padx=12, pady=(10, 0))
        ttk.Label(input_panel, text="仅运行时使用，不会保存", style="Muted.TLabel").grid(row=3, column=2, sticky="w", pady=(10, 0))
        input_panel.columnconfigure(1, weight=1)
        ttk.Button(input_panel, text="加载并预览", style="Accent.TButton", command=self.load_preview).grid(row=4, column=1, sticky="w", pady=(18, 0))

        summary = ttk.Frame(root, style="App.TFrame")
        summary.pack(fill="x", pady=16)
        self.cards = {}
        for key, label in [("groups", "商品组"), ("rows", "异常 SKU"), ("multi", "多 SKU 组"), ("single", "单 SKU 组")]:
            card = ttk.Frame(summary, style="Panel.TFrame", padding=(18, 12)); card.pack(side="left", fill="x", expand=True, padx=(0, 10))
            value = ttk.Label(card, text="—", style="Title.TLabel", font=("Segoe UI", 18, "bold")); value.pack(anchor="w")
            ttk.Label(card, text=label, style="Muted.TLabel").pack(anchor="w")
            self.cards[key] = value

        table_panel = ttk.Frame(root, style="Panel.TFrame", padding=14)
        table_panel.pack(fill="both", expand=True)
        ttk.Label(table_panel, text="执行预览", style="Panel.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        cols = ("product", "shop", "count", "skus", "action")
        self.tree = ttk.Treeview(table_panel, columns=cols, show="headings", selectmode="none")
        headers = {"product":"全球产品 ID", "shop":"店铺 ID", "count":"异常数", "skus":"平台 SKU", "action":"拟执行操作"}
        widths = {"product":180, "shop":110, "count":70, "skus":430, "action":150}
        for c in cols:
            self.tree.heading(c, text=headers[c]); self.tree.column(c, width=widths[c], anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_panel, orient="vertical", command=self.tree.yview); scroll.pack(side="right", fill="y"); self.tree.configure(yscrollcommand=scroll.set)

        bottom = ttk.Frame(root, style="App.TFrame"); bottom.pack(fill="x", pady=(16, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate"); self.progress.pack(fill="x", pady=(0, 8))
        self.status_var = tk.StringVar(value="请先选择文件并加载预览")
        ttk.Label(bottom, textvariable=self.status_var, style="Sub.TLabel").pack(side="left")
        self.run_btn = ttk.Button(bottom, text="确认并开始处理", style="Accent.TButton", command=self.start_run, state="disabled"); self.run_btn.pack(side="right")
        self.stop_btn = ttk.Button(bottom, text="停止后续任务", style="Ghost.TButton", command=self.stop_run, state="disabled"); self.stop_btn.pack(side="right", padx=8)

    def _file_row(self, parent, row, label, variable, hint):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=12, pady=5)
        ttk.Button(parent, text="选择文件", style="Ghost.TButton", command=lambda: self._choose(variable, label)).grid(row=row, column=2, pady=5)
        ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=row, column=3, sticky="w", padx=10)

    def _choose(self, variable, label):
        path = filedialog.askopenfilename(title=f"选择{label}", filetypes=[("Excel 文件", "*.xlsx *.xls"), ("所有文件", "*.*")])
        if path:
            variable.set(path)
            if label == "异常汇总表" and not self.output_var.get(): self.output_var.set(str(Path(path).with_name(Path(path).stem + "_配送处理结果.xlsx")))

    def load_preview(self):
        try:
            if not self.report_var.get() or not self.source_var.get(): raise ValueError("请先选择异常汇总表和 SKU 源表")
            self.analysis = analyze_paths(self.report_var.get(), self.source_var.get())
            groups = self.analysis["groups"]
            for item in self.tree.get_children(): self.tree.delete(item)
            multi = single = 0
            for (product, shop), rows in list(groups.items())[:500]:
                if len(rows) > 1: multi += 1
                else: single += 1
                skus = "、".join(normalize(r.get("平台SKU")) for r in rows)
                action = "下架商品" if len(rows) == 1 else "删除 SKU"
                self.tree.insert("", "end", values=(product, shop, len(rows), skus, action))
            self.cards["groups"].configure(text=f"{len(groups):,}"); self.cards["rows"].configure(text=f"{len(self.analysis['rows']):,}")
            self.cards["multi"].configure(text=f"{multi:,}"); self.cards["single"].configure(text=f"{single:,}")
            self.status_var.set(f"已加载 {len(groups):,} 个商品组。仅包含配送相关异常，价格不会修改。")
            self.run_btn.configure(state="normal")
        except Exception as exc: messagebox.showerror("无法加载", str(exc))

    def start_run(self):
        if not self.analysis or not self.cookie_var.get(): messagebox.showwarning("还缺少信息", "请先加载预览，并输入妙手 Cookie / Token。"); return
        if not messagebox.askyesno("确认执行", "即将调用妙手接口删除异常 SKU 或下架单 SKU 商品。是否继续？"): return
        self.run_btn.configure(state="disabled"); self.stop_btn.configure(state="normal"); self.stop_event.clear(); self.progress.configure(value=0, maximum=len(self.analysis["groups"]))
        threading.Thread(target=self._run_worker, daemon=True).start()

    def _run_worker(self):
        def callback(info): self.events.put(("progress", info))
        try:
            result = process_delivery(Path(self.report_var.get()), Path(self.source_var.get()), Path(self.output_var.get()), self.cookie_var.get(), "23126fe530956cc04977660b5b177e06", callback, self.stop_event)
            self.events.put(("done", result))
        except Exception as exc: self.events.put(("error", str(exc)))

    def stop_run(self): self.stop_event.set(); self.status_var.set("已请求停止，正在等待当前请求结束…"); self.stop_btn.configure(state="disabled")

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.progress.configure(value=payload["progress"]); self.status_var.set(f"处理 {payload['progress']}/{payload['total']}：{payload['productId']} · {payload['status']}")
                elif kind == "done":
                    self.stop_btn.configure(state="disabled"); self.status_var.set("处理完成：" + json.dumps(payload, ensure_ascii=False)); messagebox.showinfo("处理完成", "结果已写入新的 Excel 文件。")
                elif kind == "error":
                    self.stop_btn.configure(state="disabled"); self.run_btn.configure(state="normal"); messagebox.showerror("执行失败", payload)
        except queue.Empty: pass
        self.after(120, self._drain_events)


if __name__ == "__main__":
    App().mainloop()
