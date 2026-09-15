# 订单转花轮采购单

这是一个独立运行的 Windows 桌面程序：读取订单导出 Excel，按平台 SKU 查询 Amazon 美国站商品单价和规格，检查 SKU 一致性，再按照内置的 `花轮模板.xlsx` 生成采购单。

## 直接运行

打包后的程序位于：

```text
dist/OrderToHualun/OrderToHualun.exe
```

请保持 `OrderToHualun.exe` 与旁边的 `_internal` 文件夹在同一目录。

## 输入表格

输入 Excel 需要包含以下表头：

- `订单编号`
- `平台SKU`
- `产品数量`
- `产品规格(原文)`

相同订单包含多个 SKU 时，程序会按顺序生成 `订单号-1`、`订单号-2` 等订单号；单 SKU 订单保留原订单号。收货人名称使用配置的前缀加订单号后 5 位。

## 界面配置

程序内置花轮模板，不需要选择模板文件。界面可以配置店铺名称、收货人前缀、电话、地址、城市、州/省和邮编，配置会自动保存。

默认值：

```text
收货人前缀：Claire-wyc
电话：6265225687
地址：10136 Stafford St
城市：Rancho Cucamonga
州：CA
邮编：91730
```

程序默认直连 Amazon，串行处理，不使用代理池。邮编会用于设置 Amazon 配送区域；查询失败时仍保留订单行，价格留空并把原因写入备注。

## 从源码运行或重新打包

```powershell
python order_app.py
pyinstaller --noconfirm --clean order_app.spec
```

模板文件位于 `templates/花轮模板.xlsx`，输出文件默认保存到输入 Excel 所在目录。
