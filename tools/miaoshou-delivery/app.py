"""Miaoshou delivery-exception SKU desktop operator."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from delivery_processor import DELIVERY_STATUSES, _read_report, parse_curl_command, process_delivery, normalize


BG = "#0f172a"
PANEL = "#172033"
PANEL_2 = "#1e293b"
TEXT = "#e5eefb"
MUTED = "#91a4bf"
ACCENT = "#38bdf8"
GREEN = "#34d399"
ORANGE = "#fbbf24"


def workspace_layout(window_width: int) -> str:
    """The supported desktop width always keeps activity beside the preview."""
    return "side_by_side" if window_width >= 900 else "stacked"


def analyze_paths(report: str) -> dict:
    rows = _read_report(Path(report))
    groups = {}
    for row in rows:
        key = (normalize(row.get("全球产品ID")), normalize(row.get("_shopId")))
        groups.setdefault(key, []).append(row)
    return {"rows": rows, "groups": groups, "row_count": len(rows), "group_count": len(groups)}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Miaoshou · 配送异常 SKU 处理台")
        self.geometry("1180x900")
        self.minsize(980, 760)
        self.configure(bg=BG)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
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
        self.output_var = tk.StringVar()
        self._file_row(input_panel, 0, "异常汇总表", self.report_var, "选择包含“异常汇总”sheet 的 Excel")
        self._file_row(input_panel, 1, "结果文件", self.output_var, "默认生成在异常汇总表同目录")
        ttk.Label(input_panel, text="妙手接口 cURL", style="Panel.TLabel").grid(row=2, column=0, sticky="nw", pady=(10, 0))
        curl_box = ttk.Frame(input_panel, style="Panel.TFrame")
        curl_box.grid(row=2, column=1, columnspan=2, sticky="ew", padx=12, pady=(10, 0))
        self.curl_text = tk.Text(curl_box, height=7, width=85, wrap="none", bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", padx=8, pady=7)
        self.curl_text.pack(side="left", fill="both", expand=True)
        curl_scroll = ttk.Scrollbar(curl_box, orient="vertical", command=self.curl_text.yview)
        curl_scroll.pack(side="right", fill="y")
        self.curl_text.configure(yscrollcommand=curl_scroll.set)
        ttk.Label(input_panel, text="从浏览器复制完整 cURL，程序会自动读取 URL、Cookie、请求头和会话信息", style="Muted.TLabel").grid(row=3, column=1, columnspan=2, sticky="w", padx=12, pady=(4, 0))
        input_panel.columnconfigure(1, weight=1)
        ttk.Button(input_panel, text="加载并预览", style="Accent.TButton", command=self.load_preview).grid(row=4, column=1, sticky="w", pady=(18, 0))

        summary = ttk.Frame(root, style="App.TFrame")
        summary.pack(fill="x", pady=16)
        self.cards = {}
        for key, label in [("groups", "商品组"), ("rows", "异常 SKU"), ("multi", "待详情判断"), ("single", "价格不处理")]:
            card = ttk.Frame(summary, style="Panel.TFrame", padding=(18, 12)); card.pack(side="left", fill="x", expand=True, padx=(0, 10))
            value = ttk.Label(card, text="—", style="Title.TLabel", font=("Segoe UI", 18, "bold")); value.pack(anchor="w")
            ttk.Label(card, text=label, style="Muted.TLabel").pack(anchor="w")
            self.cards[key] = value

        activity_bar = ttk.Frame(root, style="App.TFrame")
        activity_bar.pack(fill="x", pady=(0, 12))
        self.status_var = tk.StringVar(value="请先选择文件并加载预览")
        ttk.Label(activity_bar, textvariable=self.status_var, style="Sub.TLabel").pack(anchor="w", pady=(0, 6))
        self.progress = ttk.Progressbar(activity_bar, mode="determinate")
        self.progress.pack(fill="x")

        workspace = ttk.Frame(root, style="App.TFrame")
        workspace.pack(fill="both", expand=True)
        workspace.columnconfigure(0, weight=3)
        workspace.columnconfigure(1, weight=1, minsize=360)
        workspace.rowconfigure(0, weight=1)
        table_panel = ttk.Frame(workspace, style="Panel.TFrame", padding=14)
        table_panel.grid(row=0, column=0, sticky="nsew")
        table_header = ttk.Frame(table_panel, style="Panel.TFrame")
        table_header.pack(fill="x", pady=(0, 8))
        ttk.Label(table_header, text="执行预览", style="Panel.TLabel", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.preview_run_btn = ttk.Button(table_header, text="确认并开始处理", style="Accent.TButton", command=self.start_run, state="disabled")
        self.preview_run_btn.pack(side="right")
        cols = ("product", "shop", "count", "skus", "action")
        self.tree = ttk.Treeview(table_panel, columns=cols, show="headings", selectmode="none")
        headers = {"product":"全球产品 ID", "shop":"店铺 ID", "count":"异常数", "skus":"平台 SKU", "action":"拟执行操作"}
        widths = {"product":180, "shop":110, "count":70, "skus":430, "action":150}
        for c in cols:
            self.tree.heading(c, text=headers[c]); self.tree.column(c, width=widths[c], anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_panel, orient="vertical", command=self.tree.yview); scroll.pack(side="right", fill="y"); self.tree.configure(yscrollcommand=scroll.set)

        log_panel = ttk.Frame(workspace, style="Panel.TFrame", padding=14, width=390)
        log_panel.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        log_panel.pack_propagate(False)
        log_header = ttk.Frame(log_panel, style="Panel.TFrame")
        log_header.pack(fill="x", pady=(0, 6))
        ttk.Label(log_header, text="处理日志", style="Panel.TLabel", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.pause_btn = ttk.Button(log_header, text="暂停", style="Ghost.TButton", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="right", padx=(6, 0))
        self.stop_btn = ttk.Button(log_header, text="停止", style="Ghost.TButton", command=self.stop_run, state="disabled")
        self.stop_btn.pack(side="right")
        self.log_text = tk.Text(log_panel, wrap="word", bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", padx=10, pady=8, state="disabled")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("success", foreground=GREEN)
        self.log_text.tag_configure("manual", foreground=ORANGE)

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
            if not self.report_var.get(): raise ValueError("请先选择异常汇总表")
            self.analysis = analyze_paths(self.report_var.get())
            groups = self.analysis["groups"]
            for item in self.tree.get_children(): self.tree.delete(item)
            for (product, shop), rows in list(groups.items())[:500]:
                skus = "、".join(normalize(r.get("平台SKU")) for r in rows)
                action = "查询详情后判断"
                self.tree.insert("", "end", values=(product, shop, len(rows), skus, action))
            self.cards["groups"].configure(text=f"{len(groups):,}"); self.cards["rows"].configure(text=f"{len(self.analysis['rows']):,}")
            self.cards["multi"].configure(text=f"{len(groups):,}"); self.cards["single"].configure(text="不执行")
            self.status_var.set(f"已加载 {len(groups):,} 个商品组。仅包含配送相关异常，价格不会修改。")
            self._clear_log()
            self._append_log(f"已加载 {len(groups):,} 个商品组，等待确认执行。")
            self.preview_run_btn.configure(state="normal")
        except Exception as exc: messagebox.showerror("无法加载", str(exc))

    def start_run(self):
        curl_text = self.curl_text.get("1.0", "end").strip()
        if not self.analysis or not curl_text: messagebox.showwarning("还缺少信息", "请先加载预览，并粘贴完整的妙手 cURL。"); return
        try:
            parse_curl_command(curl_text)
        except ValueError as exc:
            messagebox.showerror("cURL 无法使用", str(exc)); return
        if not messagebox.askyesno("确认执行", "即将调用妙手接口删除异常 SKU 或下架单 SKU 商品。是否继续？"): return
        self.active_curl = curl_text
        self.preview_run_btn.configure(state="disabled"); self.stop_btn.configure(state="normal"); self.pause_btn.configure(state="normal", text="暂停"); self.stop_event.clear(); self.pause_event.set(); self.progress.configure(value=0, maximum=len(self.analysis["groups"]))
        self._append_log("开始执行。每个商品组完成后会记录最终动作和结果。")
        threading.Thread(target=self._run_worker, daemon=True).start()

    def _run_worker(self):
        def callback(info): self.events.put(("progress", info))
        try:
            result = process_delivery(Path(self.report_var.get()), Path(self.output_var.get()), self.active_curl, "", callback, self.stop_event, self.pause_event)
            self.events.put(("done", result))
        except Exception as exc: self.events.put(("error", str(exc)))

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear(); self.pause_btn.configure(text="继续"); self.status_var.set("已暂停：当前商品组完成后等待继续"); self._append_log("已暂停，当前请求完成后不会开始下一个商品组。")
        else:
            self.pause_event.set(); self.pause_btn.configure(text="暂停"); self.status_var.set("已继续执行"); self._append_log("已继续执行后续商品组。")

    def stop_run(self):
        self.stop_event.set(); self.pause_event.set(); self.status_var.set("已请求停止，正在等待当前请求结束…"); self.stop_btn.configure(state="disabled"); self.pause_btn.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal"); self.log_text.delete("1.0", "end"); self.log_text.configure(state="disabled")

    def _append_log(self, message, tag=None):
        self.log_text.configure(state="normal"); self.log_text.insert("end", message + "\n", tag or ()); self.log_text.see("end"); self.log_text.configure(state="disabled")

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.progress.configure(value=payload["progress"]); self.status_var.set(f"处理 {payload['progress']}/{payload['total']}：{payload['productId']} · {payload['status']}")
                    if payload.get("status") == "已完成" or payload.get("status") == "需手动处理":
                        tag = "success" if payload.get("result") == "成功" else "manual"
                        self._append_log(f"[{payload['progress']}/{payload['total']}] 商品 {payload['productId']} / 店铺 {payload['shopId']}\n{payload.get('action', '')} · {payload.get('result', '')}\n{payload.get('detail', '')}\n", tag)
                elif kind == "done":
                    self.stop_btn.configure(state="disabled"); self.pause_btn.configure(state="disabled"); self.preview_run_btn.configure(state="normal"); self.status_var.set("处理完成：" + json.dumps(payload, ensure_ascii=False)); self._append_log("处理线程结束，结果文件已写出。\n" + json.dumps(payload, ensure_ascii=False)); messagebox.showinfo("处理完成", "结果已写入新的 Excel 文件。")
                elif kind == "error":
                    self.stop_btn.configure(state="disabled"); self.pause_btn.configure(state="disabled"); self.preview_run_btn.configure(state="normal"); self._append_log("执行失败：" + str(payload), "manual"); messagebox.showerror("执行失败", payload)
        except queue.Empty: pass
        self.after(120, self._drain_events)


if __name__ == "__main__":
    App().mainloop()
