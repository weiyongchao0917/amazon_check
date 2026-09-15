# Amazon SKU 检查工具

默认**直连**采集 Amazon 美国站公开商品页，按 TikTok 导出表生成检查报告。  
出现限流/验证码时，可在配置中**自行填写代理**后断点续跑。

## 功能

- 读取 TikTok Shop SKU 导出 Excel（`平台SKU` 作为 ASIN）
- 默认不使用代理；可选：
  - 手动代理列表
  - 动态提取 API（如青果 `share.proxy.qg.net/get`）
- 价格：Amazon 当前可见购买价优先，`建议本地价 = Amazon价 × 配置倍率`，差异达到配置阈值时标记需检查
- 配送：默认邮编 `91730`（可配置）、美国太平洋时间、默认最晚 1 个工作日送达（可配置），日期范围取第一天
- 输出新 Excel（不修改原表）：检查报告 / 异常汇总 / 运行信息
- 断点续跑、单条失败不中断、连续拦截熔断提示
- GUI 可只重试上次取数失败项，成功项不会重复检查

## 安装

```bash
cd tools/amazon-checker
pip install -r requirements.txt
```

Windows 建议 Python 3.10+。

## 快速开始（无代理）

```bash
python -m sku_checker.main "导出SKU.xlsx"
```

只测前 5 条：

```bash
python -m sku_checker.main "导出SKU.xlsx" -n 5
```

指定配置与输出：

```bash
python -m sku_checker.main "导出SKU.xlsx" -c config.yaml -o output/report.xlsx
```

## 配置代理（可选）

复制 `config.example.yaml` 为 `config.yaml`。

### 1）手动列表

```yaml
proxy:
  enabled: true
  mode: list
  list_text: |
    http://1.2.3.4:8080
    http://user:pass@1.2.3.5:8080
    1.2.3.6:9000
```

支持格式：

```text
http://主机:端口
http://用户名:密码@主机:端口
socks5://主机:端口
主机:端口
用户名:密码@主机:端口
```

### 2）提取 API（青果示例）

```yaml
proxy:
  enabled: true
  mode: api
  api:
    url: "https://share.proxy.qg.net/get?key={key}&num={num}&isp=0&distinct=true"
    key: "你的KEY"
    auth_user: ""          # 若走代理需要账密再填
    auth_password: ""
    num: 50
    parser: auto
    max_uses_per_proxy: 2
```

不查余额；池空或失败时自动再提取。

### 3）711Proxy 提取 URL 示例

在 GUI 的“代理设置”中选择“代理 API”，把服务商给出的完整提取 URL 填入 `API URL`：

```text
http://global.rotgbapi.711proxy.com:8089/gen?zone=custom&ptype=1&count=1&proto=http&stype=text&split=\r\n&sessType=rotating
```

程序支持两种数量写法：

```text
count={num}
count=1
```

- 使用 `{num}` 时会替换为界面中的“提取数量”。
- URL 已包含固定 `count` 或 `num` 参数时，程序会用界面中的“提取数量”覆盖。
- 旋转网关套餐通常只需提取 1 个入口；普通 IP 池可按服务商限制填写多条。

如果代理服务商使用 IP 白名单认证，需要先把运行电脑的公网出口 IP 加入白名单；如果使用账密认证，则把代理用户名、密码分别填到“认证用户名”和“认证密码”。`Key` 只用于 URL 中的 `{key}` 占位符，不等于代理认证密码。

保存前可点击“测试代理”。程序会依次检查：

1. 提取 API 与响应解析；
2. HTTPS CONNECT；
3. Amazon 首页连通性。

常见提示：

- `代理认证失败（407）`：认证用户名或密码错误。
- `代理网关拒绝连接`：检查服务商 IP 白名单或认证方式。
- `代理连接超时`：当前节点不可达，需更换节点或检查网络。

### 严格代理保证

启用任意代理模式后，Amazon 请求采用严格代理策略：代理池为空、提取失败或重试时没有新代理，当前 SKU 会明确失败并停止，不会静默回退到本机直连。报告和运行日志只记录脱敏后的 `协议://主机:端口`，不会记录代理用户名和密码。

### 4）固定网关账号密码认证（推荐用于 711Proxy）

如果服务商提供的测试命令类似：

```text
curl -L -x 代理主机:端口 -U "代理用户名:代理密码" ipinfo.io
```

请选择 GUI 中的“固定账密代理”，不要选择“代理 API”。按服务商命令拆分填写：

```text
固定代理协议：http
固定代理主机：命令中 -x 后、冒号前的主机
固定代理端口：命令中 -x 后、冒号后的端口
认证用户名：命令中 -U 引号内、冒号前的完整用户名
认证密码：命令中 -U 引号内、冒号后的密码
```

此模式下 `API URL`、`Key` 和“提取数量”均不参与运行，可以留空或保持默认值。固定网关不会因为使用次数达到上限而被移出代理池，网络重试仍使用同一网关；启用代理后依旧不会退回直连。

点击“测试代理”后，成功结果为：

```text
[成功] 固定代理配置有效
[成功] HTTPS CONNECT
[成功] Amazon 首页连接
```

用户名和密码仅保存在当前电脑 `%LOCALAPPDATA%\AmazonSKUChecker\config.yaml`，不会进入报告或运行日志。不要公开该配置文件；凭据一旦发到聊天、邮件或截图中，建议到服务商后台重置密码。

### 熔断后继续

连续访问受限会停止并保存进度：

```bash
# 先在 config.yaml 启用代理，再：
python -m sku_checker.main "导出SKU.xlsx" -c config.yaml
```

（默认 `resume: true`，已完成的行会跳过。）

重新全量跑：

```bash
python -m sku_checker.main "导出SKU.xlsx" --no-resume
```

只重新检查断点中因代理、网络或页面取数失败的条目：

```bash
python -m sku_checker.main "导出SKU.xlsx" --retry-failed
```

## 输出说明

| 工作表 | 内容 |
|--------|------|
| 检查报告 | 全部 SKU 明细 |
| 异常汇总 | 总状态 ≠ 正常 |
| 需要重新查 | 本次未取得完整数据、可使用“重试失败项”再次检查的 SKU |
| 运行信息 | 规则与统计 |

## 说明与边界

- 不使用 Playwright 浏览器主路径（`curl_cffi` HTTP）
- 不自动破解验证码
- 中国出口代理访问 amazon.com 成功率可能低于美国住宅代理
- 配送「需检查」多数是货源时效超过配置的工作日上限，不一定是采集错误

## 目录

```text
amazon_sku_checker/
  config.example.yaml
  requirements.txt
  README.md
  sku_checker/
    main.py
    config.py
    proxy_pool.py
    amazon_client.py
    rules.py
    excel_io.py
    report.py
```
