"""订单利润核算的 Windows 桌面界面。"""

from __future__ import annotations

import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from hesuan_ticheng import (
    CalculationResult,
    calculate_profit,
    export_result,
    find_input_files,
    read_input_table,
)


def format_input_status(main_path: Optional[Path], purchase_path: Optional[Path], product_path: Optional[Path]) -> dict[str, str]:
    """把文件识别结果转换成适合显示在界面上的文字。"""

    def name_or_missing(path: Optional[Path], optional: bool = False) -> str:
        if path is not None:
            return Path(path).name
        return "未提供（可选）" if optional else "未找到"

    return {
        "主表": name_or_missing(main_path),
        "采购表": name_or_missing(purchase_path),
        "商品表": name_or_missing(product_path, optional=True),
    }


def summarize_result(result: CalculationResult) -> dict[str, Any]:
    """生成 GUI 统计区需要的五项指标。"""
    profits = [row.get("订单利润") for row in result.rows]
    return {
        "主订单数": result.summary.get("主订单数", len(profits)),
        "盈利订单数": sum(value is not None and value >= 0 for value in profits),
        "亏损订单数": sum(value is not None and value < 0 for value in profits),
        "待核对订单数": sum(value is None for value in profits),
        "利润合计": result.summary.get("利润合计"),
    }


def run_calculation(folder: Path, logistics_fee: float = 1.0) -> tuple[CalculationResult, Path]:
    """读取指定目录并生成结果文件，供 GUI 后台线程调用。"""
    folder = Path(folder).resolve()
    files = find_input_files(folder)
    main_df = read_input_table(files.main)
    purchase_df = read_input_table(files.purchase)
    product_df = read_input_table(files.product) if files.product else None
    result = calculate_profit(main_df, purchase_df, product_df, logistics_fee)
    output_path = folder / "输出" / f"订单利润核算-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    export_result(result, output_path)
    return result, output_path


def _application_folder() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class ProfitGui:
    def __init__(self, root: Any):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        self.root = root
        self.root.title("订单利润核算")
        self.root.geometry("820x620")
        self.root.minsize(720, 520)
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._closing = False
        self._output_path: Optional[Path] = None

        self.folder_var = tk.StringVar(value=str(_application_folder()))
        self.status_var = tk.StringVar(value="请选择工作目录并刷新文件")
        self.file_vars = {name: tk.StringVar(value="未识别") for name in ("主表", "采购表", "商品表")}
        self.stat_vars = {name: tk.StringVar(value="-") for name in ("主订单数", "盈利订单数", "亏损订单数", "待核对订单数", "利润合计")}

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="订单利润核算", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")

        folder_frame = ttk.LabelFrame(outer, text="工作目录", padding=8)
        folder_frame.pack(fill="x", pady=(12, 8))
        ttk.Entry(folder_frame, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(folder_frame, text="选择目录", command=self.choose_folder).pack(side="left", padx=(0, 6))
        ttk.Button(folder_frame, text="刷新文件", command=self.refresh_files).pack(side="left")

        files_frame = ttk.LabelFrame(outer, text="输入文件", padding=8)
        files_frame.pack(fill="x", pady=8)
        for row_index, name in enumerate(("主表", "采购表", "商品表")):
            ttk.Label(files_frame, text=f"{name}：", width=10).grid(row=row_index, column=0, sticky="w", pady=3)
            ttk.Label(files_frame, textvariable=self.file_vars[name]).grid(row=row_index, column=1, sticky="w", pady=3)
        files_frame.columnconfigure(1, weight=1)

        action_frame = ttk.Frame(outer)
        action_frame.pack(fill="x", pady=8)
        self.start_button = ttk.Button(action_frame, text="开始核算", command=self.start_calculation)
        self.start_button.pack(side="left")
        ttk.Label(action_frame, textvariable=self.status_var).pack(side="left", padx=12)

        stats_frame = ttk.LabelFrame(outer, text="核算结果", padding=8)
        stats_frame.pack(fill="x", pady=8)
        for column, name in enumerate(("主订单数", "盈利订单数", "亏损订单数", "待核对订单数", "利润合计")):
            card = ttk.Frame(stats_frame, padding=(8, 4))
            card.grid(row=0, column=column, sticky="nsew")
            ttk.Label(card, text=name).pack()
            ttk.Label(card, textvariable=self.stat_vars[name], font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(4, 0))
            stats_frame.columnconfigure(column, weight=1)

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=6)
        log_frame.pack(fill="both", expand=True, pady=8)
        self.log_text = ScrolledText(log_frame, height=12, state="disabled", wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True)

        bottom_frame = ttk.Frame(outer)
        bottom_frame.pack(fill="x", pady=(4, 0))
        self.open_button = ttk.Button(bottom_frame, text="打开结果文件", command=self.open_output, state="disabled")
        self.open_button.pack(side="right")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_files()

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def choose_folder(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askdirectory(initialdir=self.folder_var.get(), title="选择订单数据目录")
        if selected:
            self.folder_var.set(selected)
            self.refresh_files()

    def refresh_files(self) -> None:
        try:
            files = find_input_files(Path(self.folder_var.get()))
            statuses = format_input_status(files.main, files.purchase, files.product)
            for name, value in statuses.items():
                self.file_vars[name].set(value)
            self.status_var.set("文件已识别，可以开始核算")
            self.log(f"已识别：主表={files.main.name}，采购表={files.purchase.name}，商品表={files.product.name if files.product else '未提供'}")
        except Exception as exc:
            for name in self.file_vars:
                self.file_vars[name].set("未找到")
            self.status_var.set("未找到完整输入文件")
            self.log(f"文件识别失败：{exc}")

    def start_calculation(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        folder = Path(self.folder_var.get()).expanduser()
        if not folder.is_dir():
            self.status_var.set("工作目录不存在")
            self.log(f"核算失败：目录不存在：{folder}")
            return
        self.start_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.status_var.set("正在核算，请稍候...")
        self.log("开始核算...")
        self._worker = threading.Thread(target=self._worker_entry, args=(folder,), daemon=True)
        self._worker.start()
        self.root.after(100, self.poll_worker)

    def _worker_entry(self, folder: Path) -> None:
        try:
            result, output_path = run_calculation(folder)
            self._messages.put(("success", (result, output_path)))
        except Exception as exc:
            self._messages.put(("error", exc))

    def poll_worker(self) -> None:
        try:
            message_type, payload = self._messages.get_nowait()
        except queue.Empty:
            if self._worker is not None and self._worker.is_alive():
                self.root.after(100, self.poll_worker)
            elif self._closing:
                self.root.destroy()
            return
        self.start_button.configure(state="normal")
        if message_type == "success":
            result, output_path = payload
            self._output_path = output_path
            stats = summarize_result(result)
            for name, value in stats.items():
                if name == "利润合计" and value is not None:
                    self.stat_vars[name].set(f"${value:,.2f}")
                else:
                    self.stat_vars[name].set(str(value))
            self.status_var.set("核算完成")
            self.open_button.configure(state="normal")
            self.log(f"核算完成：{output_path}")
        else:
            self.status_var.set("核算失败")
            self.log(f"核算失败：{payload}")
        if self._closing:
            self.root.destroy()

    def open_output(self) -> None:
        if self._output_path is None or not self._output_path.exists():
            return
        try:
            os.startfile(str(self._output_path))
        except AttributeError:
            import webbrowser

            webbrowser.open(self._output_path.as_uri())

    def close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self._closing = True
            self.status_var.set("正在完成核算，请稍候...")
            self.root.after(100, self.poll_worker)
            return
        self.root.destroy()


def main() -> None:
    import tkinter as tk

    root = tk.Tk()
    ProfitGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
