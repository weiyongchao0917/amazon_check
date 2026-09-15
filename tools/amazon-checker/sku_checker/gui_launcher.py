"""Windows GUI 启动包装器：任何初始化异常都写日志并显示错误框。"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime

from sku_checker.paths import LOG_PATH


def write_crash_log(text: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(
        f"时间: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Python: {sys.executable}\n版本: {sys.version}\n\n{text}",
        encoding="utf-8",
    )


def main() -> int:
    try:
        from sku_checker.gui import SkuCheckerGui

        app = SkuCheckerGui()
        app.run()
        return 0
    except BaseException:
        details = traceback.format_exc()
        try:
            write_crash_log(details)
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Amazon SKU 检查工具启动失败",
                f"GUI 启动失败，详细信息已保存到：\n{LOG_PATH}\n\n"
                f"{details[-1200:]}",
                parent=root,
            )
            root.destroy()
        except Exception:
            pass
        print(details, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
