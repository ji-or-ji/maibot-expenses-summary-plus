# maibot-expenses-summary-plus - 麦麦财务总结优化

> ⚠️ **与原版互斥**：本插件是 [davidblackcn/maibot-expenses-summary-plugin](https://github.com/DavidBlackCN/maibot-expenses-summary-plugin)（麦麦财务总结插件）的优化分支，两者共用相同的统计能力、指令与数据目录（`data/ledger.json`），**请勿同时安装**，否则会发生指令冲突与账本数据互相覆盖。安装前请先卸载原版。

一个 MaiBot 1.0.9+ / sdk2.6.0+ 插件，使用最新版本sdk新增的 `self.ctx.statistics` 能力代理，统计当天模型调用次数、回复量和回复成本，并生成财报文本与图片。在保留原版全部功能的基础上，新增订阅制/混合计费口径。

## 功能

- 统计今日模型请求、回复和成本
- 支持 `/expenses` 和 `/今日财报` 指令立即生成财报
- 支持作为 Tool 被麦麦主动调用
- 支持“默认”和“麦晨风”两种财报模式
- 支持管理员命令在线切换财报模式
- 支持合并转发消息或普通消息发送
- 使用 HTML 渲染图片展示累计请求次数、回复成本和各模型回复成本
- 支持三种计费口径：纯按量、固定月费订阅、订阅+按量混合（`[billing]` 配置）
- 可选定时发送

## 财报模式

### 默认模式

默认模式文案偏正常，适合日常查看成本。默认情况下：

1. 第一条消息：今日财报开头语
2. 第二条消息：图片，包含累计请求、回复消息、回复成本、各模型成本

默认模式第一条文本可通过 `report.default_opening` 修改，支持 `{date}` 占位符。

### 麦晨风模式

让你的麦麦化身“户晨风本风”，每天用咬牙切齿的语气公开处刑自己（并顺带感谢股东）：

1. 第一条消息：麦晨风风格开头语
2. 第二条消息：图片，包含累计请求、回复消息、回复成本、各模型成本
3. 第三条消息：感谢文案

## 计费模式（Billing）

财报成本默认来自 MaiBot 统计接口的按量估算口径（token 用量 × 单价）。当模型通过固定月费订阅渠道（如 OpenCode Go 等按计划订阅的模型）使用时，按量估算会失真。`[billing]` 配置节提供三种计费口径：

| 模式 | 说明 |
|------|------|
| `usage` | 纯按量（默认，原口径）：成本直接取统计接口的按量估算值 |
| `subscription` | 纯订阅：所有模型成本按固定月费折算，不使用按量估算 |
| `hybrid` | 混合（推荐）：订阅模型走月费折算，其余模型按量照算 |

配置示例：

```toml
[billing]
mode = "hybrid"                # usage | subscription | hybrid
currency = "usd"               # 订阅支付币种：usd=美元计费 | cny=人民币计费（不换算汇率）
renew_day = 14                 # 每月续费日（扣款日，1-31；大于当月天数时取月末）
renew_amount = 10.0            # 每期续费金额（续费日到下个续费日扣款额）
share_daily = true             # true=按本期天数均摊为日均成本；false=整期费用一次展示
subscription_models = ["-go"]  # hybrid 模式：模型名包含任一关键词即视为订阅（子串匹配）
exchange_rate = 7.2            # 兜底汇率：usd 计费且自动获取汇率失败时使用
```

### 订阅周期

每期从续费日（`renew_day`）到下个续费日。今日订阅成本 = 每期续费金额折算人民币 ÷ 本期天数。续费日为 31 号时，无 31 号的月份自动取月末（如 2 月取 2/28），下一期从下月实际续费日接续。

### 汇率获取

USD 计费时，汇率优先取**续费当天的历史牌价**（frankfurter 公开接口），同一订阅周期内缓存快照、不重复请求；历史接口不可用时回退实时接口，再失败用 `exchange_rate` 兜底。CNY 计费完全不参与汇率换算。

### 财报展示

- 订阅模型在图片中显示橙色「订阅包」标签，横条为橙色，按量成本置零
- 按量模型照常显示金额（深青色横条）
- 「回复成本」卡片下方显示口径说明小字（币种、汇率、周期、均摊方式）
- 净收入、账本累计开销均按新口径记账

## 指令

```text
/expenses
/今日财报
```

管理员可切换模式：

```text
/财报模式 默认
/财报模式 麦晨风
/expensesmode default
/expensesmode maichenfeng
```

`/财报模式` 和 `/expensesmode` 始终仅管理员可用。管理员通过 `permission.admins` 配置 QQ 号。

### 记账（账本）

记录群友投喂 / 开销，数据存 JSON 账本（`data/ledger.json`）：

```text
/记账 <金额> [备注]      # 例：/记账 71 抹茶味兽兽
/投喂 <金额> [备注]      # 记账的别名
/账本                   # 查看累计账本
/查账                   # 查看累计账本（别名）
```

- 成功回复格式：`已记账：71.00 元（抹茶味兽兽）。累计投喂 X 元，累计开销 X 元，净收入 X 元。`
- 默认**仅管理员可用**（`ledger.admin_only` 可配，`permission.admins` 指定管理员 QQ）
- 账本功能总开关：`ledger.enabled`（默认开）

## 配置

仓库提供 `config.example.toml`，可作为默认配置参考。主要配置如下：

```toml
[plugin]
config_version = "1.0.2"

[report]
mode = "default"
title = "今日模型调用财报"
llm_task = "utils"
use_forward_message = true
default_opening = "{date}模型调用财报已生成，以下是今日请求次数、回复量与模型成本汇总。"

[permission]
query_admin_only = false
admins = []

[scheduler]
enabled = false
time = "23:30"
group_ids = []
private_ids = []

[fallback]
xiao_names = ["孙笑川"]
locations = ["家里", "直播间", "工作室", "走廊"]
poems = [
  "今天也是嘴硬的一天。",
  "气冷抖，孙狗什么时候才能站起来？",
  "我永远喜欢孙笑川！",
  "大家都很聪明，就是有点笨。"
]
thanks_list = ["孙笑川：114514", "抽象带篮：1919810", "吃花椒的喵酱：810"]

[billing]
# 计费模式：usage=纯按量(原口径) | subscription=固定月费 | hybrid=订阅模型+其余按量混合
mode = "usage"
# 订阅支付币种：usd=美元计费（按续费日汇率折人民币）| cny=人民币计费（不换算汇率）
currency = "usd"
# 每月续费日（扣款日，1-31；大于当月天数时取月末）
renew_day = 14
# 每期续费金额（续费日到下个续费日扣款额，配合 currency 使用）
renew_amount = 10.0
# true=把本期订阅费按周期天数均摊为日均成本；false=整期费用一次展示
share_daily = true
# hybrid 模式生效：模型名包含任一关键词即视为订阅（子串匹配）
subscription_models = ["-go"]
# usd 计费且自动获取汇率失败时使用的兜底 USD→CNY 汇率
exchange_rate = 7.2

# BGM 音频功能自 1.0.1 起暂停启用：当前 sdk2.x 暂未提供 send.audio 能力。
```

`report.use_forward_message = true` 时使用合并转发消息发送；设为 `false` 时会按普通消息逐条发送文本和图片。

`report.llm_task` 用于配置麦晨风模式生成地点、“我去了……”和诗句时使用的任务名，插件会通过 SDK 公共接口 `ctx.llm.generate(..., model=report.llm_task)` 调用，默认使用 `utils`。小名不会交给 LLM 生成，只会从 `fallback.xiao_names` 中选择。

`permission.query_admin_only = true` 时，`/expenses` 和 `/今日财报` 仅管理员可用；模式切换命令始终仅管理员可用。

`scheduler.enabled = true` 时会按 `scheduler.time` 每天定时发送。`scheduler.group_ids` 填 QQ 群号，`scheduler.private_ids` 填私聊 QQ 号，插件会通过 `ctx.chat.get_stream_by_group_id()` / `ctx.chat.get_stream_by_user_id()` 解析目标会话后发送。

## Tool

麦麦在需要“生成今日财报”“公开模型调用成本”“麦晨风式收入汇报”等场景下可以调用 `expenses_summary`。

## 安装

将插件目录放入 MaiBot 的插件目录，确认 `_manifest.json` 与 `plugin.py` 位于同一目录后重启 MaiBot。

## 兼容性

- MaiBot 最低版本：`1.0.9`
- SDK 版本：`2.6`

插件使用 `ctx.statistics.local.*` 获取统计数据，使用 `ctx.render.html2png()` 生成图片。合并转发模式使用 `ctx.send.forward()`，普通消息模式使用 `ctx.send.text()` 和 `ctx.send.image()`。

统计口径：MaiBot 统计 API 的 `days` 参数表示最近 N 天数据；插件会使用小时粒度趋势数据，并按本地日期过滤为当天 0 点至当前时间，避免新的一天继续计入前一日的 24H 数据。

## 更新日志

### 1.0.3（优化分支）

- 新增 `[billing]` 计费配置节，支持纯按量 / 固定月费订阅 / 订阅+按量混合三种口径。
- 订阅周期以续费日（`renew_day`）为边界，今日成本按本期天数均摊；续费日为 31 号时自动取月末。
- USD 计费自动获取续费日历史牌价并缓存，接口失败回退配置汇率；支持 CNY 直接计费（不换算汇率）。
- 财报图片中订阅模型显示橙色「订阅包」标签与横条，按量模型保持原展示。

### 1.0.2（原版基线）

- 精简 manifest 统计能力声明，移除顶层 `statistics` 和 `statistics.local`，仅保留实际使用的方法级能力。
- 修复定时发送目标解析逻辑，按 QQ 群号/QQ 号解析会话后发送定时财报。

### 1.0.1

- 暂停启用麦晨风模式下的 BGM 音频发送功能。
- 移除 `send.audio` 能力声明，避免当前 sdk2.x 无该能力时加载或审查失败。
- 从默认配置示例中移除 `[audio]` 配置节，后续 SDK 提供公共音频发送能力后再恢复。

### 1.0.0

- 移植到 MaiBot 1.0.9+ 与 sdk2.6.0+。
- 新增默认模式与麦晨风模式。
- 新增合并转发消息/普通消息发送配置。
- 新增 `/expenses`、`/今日财报` 查询命令。
- 新增 `/财报模式`、`/expensesmode` 管理员模式切换命令。
- 新增管理员列表与查询命令权限配置。
- 新增 `config.example.toml` 默认配置示例。
- 使用 `ctx.statistics.local.*` 统计当天模型调用与成本，按本地日期过滤当天 0 点后的小时数据。
- 使用 `ctx.llm.generate(..., model=report.llm_task)` 生成麦晨风模式短素材。
- 使用 HTML 转图片展示累计请求、回复消息、回复成本和各模型回复成本。

## 鸣谢

[Kmaj1st/expenses_summary](https://github.com/Kmaj1st/expenses_summary) - 一个 MaiBot 插件，让你的麦麦化身“户晨风本风”，每天用咬牙切齿的语气公开处刑自己（并顺带感谢股东），麦晨风模式取自此插件。

[davidblackcn/maibot-expenses-summary-plugin](https://github.com/DavidBlackCN/maibot-expenses-summary-plugin)（麦麦财务总结插件） - 本插件基于此插件修改。

## 许可证
MIT
