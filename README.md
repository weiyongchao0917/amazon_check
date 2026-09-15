# 跨境电商工具集

本仓库集中管理多个相互独立的跨境电商桌面工具。每个工具拥有自己的源码、依赖、测试、模板和运行说明，可以单独运行及打包。

## 工具列表

| 工具 | 目录 | 功能 |
|---|---|---|
| 妙手配送异常 SKU 处理台 | [`tools/miaoshou-delivery`](tools/miaoshou-delivery) | 读取配送异常记录，通过妙手 ERP 接口删除异常规格或下架单 SKU 商品 |
| 订单转花轮采购单 | [`tools/order-to-hualun`](tools/order-to-hualun) | 查询 Amazon 美国站单件价格、检查 SKU 和规格，并生成花轮采购模板 |
| Amazon SKU 检查工具 | [`tools/amazon-checker`](tools/amazon-checker) | 读取 TikTok Shop SKU 表，查询 Amazon 美国站价格与配送时效，并生成检查报告 |

## 妙手配送异常工具

```powershell
cd tools\miaoshou-delivery
python -m pip install -r requirements.txt
python app.py
```

运行测试：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

详细说明参见 [`tools/miaoshou-delivery/README.md`](tools/miaoshou-delivery/README.md)。

## 订单转花轮工具

```powershell
cd tools\order-to-hualun
python -m pip install -r requirements.txt
python order_app.py
```

运行测试：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

生成 Windows 文件夹版程序：

```powershell
pyinstaller --noconfirm --clean order_app.spec
```

详细说明参见 [`tools/order-to-hualun/README.md`](tools/order-to-hualun/README.md)。

## Amazon SKU 检查工具

```powershell
cd tools\amazon-checker
python -m pip install -r requirements.txt
python app.py
```

命令行运行：

```powershell
python -m sku_checker.main "导出SKU.xlsx"
```

运行测试：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

详细说明参见 [`tools/amazon-checker/README.md`](tools/amazon-checker/README.md)。

## 仓库约定

- 每个工具放在 `tools/<tool-name>` 下，并保持独立运行和测试。
- 通用代码只有在多个工具确认需要同一实现后，才提取到 `shared/`。
- `build`、`dist`、运行配置、运行状态、订单输入和生成结果不提交到 Git。
- EXE 建议通过 GitHub Releases 发布，不直接提交到源码目录。
