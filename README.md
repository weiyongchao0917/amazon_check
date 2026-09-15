# Miaoshou 配送异常 SKU 处理台

这是一个 Windows 桌面工具，用于处理妙手 ERP TikTok 商品的配送异常规格。

## 功能

- 选择 `异常汇总` 工作簿和 SKU 源表，自动通过 `SKU ID` 关联店铺 ID。
- 只处理总状态为 `配送需检查`、`价格和配送均需检查` 的记录。单独 `价格需检查` 不处理。
- 按“全球产品 ID + 店铺 ID”分组，一次查询和提交一个商品组。
- 多 SKU 商品删除异常规格；单 SKU 商品下架商品。
- 详情查询失败或 SKU 核对不一致时标记为手动处理。
- 结果写入新文件的 `处理结果` sheet，不覆盖原始文件。

## 安装与启动

```powershell
python -m pip install -r requirements.txt
python app.py
```

需要 Python 3.10 或更高版本。Tkinter 通常随 Windows Python 安装包提供。

## 使用注意

Cookie/token 只在程序运行期间保存在内存中，不会写入文件、日志或仓库。执行前请检查预览表和输出路径。程序不会修改价格；如果接口返回失败，结果表会保留失败原因供人工处理。
