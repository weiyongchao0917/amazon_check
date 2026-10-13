from pathlib import Path
from unittest.mock import patch

import pandas as pd

from hesuan_ticheng import CalculationResult, InputFiles
from hesuan_gui import format_input_status, run_calculation, summarize_result


def test_format_input_status_marks_optional_product_file():
    status = format_input_status(Path("main.xlsx"), Path("purchase.xlsx"), None)
    assert status == {
        "主表": "main.xlsx",
        "采购表": "purchase.xlsx",
        "商品表": "未提供（可选）",
    }


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


def test_run_calculation_uses_core_pipeline_and_output_folder(tmp_path):
    files = InputFiles(tmp_path / "main.xlsx", tmp_path / "purchase.xlsx", tmp_path / "product.xlsx")
    expected = CalculationResult(rows=[{"订单利润": 3}], summary={"利润合计": 3})
    with (
        patch("hesuan_gui.find_input_files", return_value=files) as find_files,
        patch("hesuan_gui.read_input_table", side_effect=[pd.DataFrame(), pd.DataFrame(), pd.DataFrame()]) as read_table,
        patch("hesuan_gui.calculate_profit", return_value=expected) as calculate,
        patch("hesuan_gui.export_result") as export,
    ):
        result, output_path = run_calculation(tmp_path)

    assert result is expected
    assert output_path.parent == tmp_path / "输出"
    assert output_path.suffix == ".xlsx"
    find_files.assert_called_once_with(tmp_path.resolve())
    assert read_table.call_count == 3
    calculate.assert_called_once()
    export.assert_called_once_with(expected, output_path)
