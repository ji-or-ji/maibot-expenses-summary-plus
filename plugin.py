"""
MaiBot 1.0.2 / sdk2.x expenses summary plugin.
"""

from __future__ import annotations

import asyncio
import base64
import calendar
import html
import json
import random
import re
import shutil
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

try:
    from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
except ImportError:  # Allows local syntax checks without the MaiBot SDK.
    def Command(*_args: Any, **_kwargs: Any) -> Callable:
        return lambda func: func

    def Tool(*_args: Any, **_kwargs: Any) -> Callable:
        return lambda func: func

    def Field(default: Any = None, **_kwargs: Any) -> Any:
        default_factory = _kwargs.get("default_factory")
        if default_factory:
            return default_factory()
        return default

    class PluginConfigBase:
        pass

    class MaiBotPlugin:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.ctx = kwargs.get("ctx")
            self.config = kwargs.get("config")


REPORT_MODE_DEFAULT = "default"
REPORT_MODE_MAICHENFENG = "maichenfeng"


@dataclass
class ModelCost:
    name: str
    requests: int = 0
    replies: int = 0
    cost: float = 0.0
    subscribed: bool = False


@dataclass
class ReportData:
    date_text: str
    total_requests: int
    total_replies: int
    total_cost: float
    model_costs: list[ModelCost]
    cost_note: str = ""


@dataclass
class FunElements:
    xiao_name: str
    location: str
    went_to: str
    poem: str


class ReportConfig(PluginConfigBase):
    mode: str = Field(
        default=REPORT_MODE_DEFAULT,
        title="财报模式",
        description="可选：default/默认、maichenfeng/麦晨风",
    )
    title: str = Field(default="今日模型调用财报", title="默认模式标题")
    llm_task: str = Field(default="utils", title="财报文案模型任务名")
    use_forward_message: bool = Field(default=True, title="使用转发消息发送")
    default_opening: str = Field(
        default="{date}模型调用财报已生成，以下是今日请求次数、回复量与模型成本汇总。",
        title="默认模式开头文本",
        description="可使用 {date} 占位符表示当天日期",
    )


class PermissionConfig(PluginConfigBase):
    query_admin_only: bool = Field(default=False, title="查询命令仅管理员可用")
    admins: list[str] = Field(default=[], title="管理员 QQ 号列表")


class SchedulerConfig(PluginConfigBase):
    enabled: bool = Field(default=False, title="启用定时发送")
    time: str = Field(default="23:30", title="定时发送时间")
    group_ids: list[str] = Field(default=[], title="定时发送群号")
    private_ids: list[str] = Field(default=[], title="定时发送私聊 QQ")


class FallbackConfig(PluginConfigBase):
    xiao_names: list[str] = Field(default=["小麦"], title="麦晨风模式小名")
    locations: list[str] = Field(
        default=["家里", "图书馆", "教室", "自习室"],
        title="麦晨风模式地点",
    )
    poems: list[str] = Field(
        default=[
            "How do you do, you like me and I like you.",
            "Shut up! I read this inside the book I read before.",
        ],
        title="麦晨风模式随机诗句",
    )
    thanks_list: list[str] = Field(default=["810", "艾斯比"], title="感谢名单")
    opening_template: str = Field(
        default="",
        title="麦晨风模式开头模板",
        description="占位符：{xiao_name} {location} {date_text} {total_requests} {total_replies} {total_cost} {went_to}；留空用内置默认",
    )
    thanks_template: str = Field(
        default="",
        title="麦晨风模式感谢模板",
        description="占位符：{date_text} {total_cost} {xiao_name} {poem} {thanks_names} {thanks_amounts} {ledger_line}；留空用内置默认",
    )


class LedgerConfig(PluginConfigBase):
    enabled: bool = Field(default=True, title="账本功能总开关")
    admin_only: bool = Field(default=True, title="记账命令仅管理员可用")


class BillingConfig(PluginConfigBase):
    mode: str = Field(
        default="usage",
        title="计费模式",
        description="usage=纯按量(原口径)；subscription=固定月费；hybrid=订阅模型+其余按量混合",
    )
    currency: str = Field(
        default="usd",
        title="订阅支付币种",
        description="usd=美元计费（按续费日汇率折人民币）；cny=人民币计费（不换算汇率）",
    )
    renew_day: int = Field(default=15, title="每月续费日", description="每月扣款日（1-31，大于当月天数时取月末）")
    renew_amount: float = Field(default=10.0, title="每期续费金额", description="每期（续费日到下个续费日）扣款金额，配合 currency 使用")
    share_daily: bool = Field(
        default=True,
        title="按天均摊",
        description="true=把本期订阅费按周期天数均摊为日均成本；false=整期费用一次展示",
    )
    subscription_models: list[str] = Field(
        default=["-go"],
        title="订阅模型关键词",
        description="hybrid 模式生效：模型名包含任一关键词即视为订阅（子串匹配）",
    )
    exchange_rate: float = Field(
        default=7.2,
        title="兜底汇率",
        description="usd 计费且自动获取汇率失败时使用的 USD→CNY 汇率",
    )


# BGM support is temporarily disabled since 1.0.1 because the current sdk2.x
# public send capability does not expose send.audio.
# class AudioConfig(PluginConfigBase):
#     enabled: bool = Field(default=False, title="启用 BGM 音频")
#     file_location: str = Field(default="audio.mp3", title="音频文件路径")


class PluginMetaConfig(PluginConfigBase):
    config_version: str = Field(default="1.0.2", title="配置文件版本")


class ExpensesSummaryConfig(PluginConfigBase):
    plugin: PluginMetaConfig = Field(default_factory=PluginMetaConfig, title="插件")
    report: ReportConfig = Field(default_factory=ReportConfig, title="财报")
    permission: PermissionConfig = Field(default_factory=PermissionConfig, title="权限")
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig, title="定时发送")
    fallback: FallbackConfig = Field(default_factory=FallbackConfig, title="麦晨风素材")
    ledger: LedgerConfig = Field(default_factory=LedgerConfig, title="账本")
    billing: BillingConfig = Field(default_factory=BillingConfig, title="计费模式")
    # audio: AudioConfig = Field(default_factory=AudioConfig, title="音频")


class ExpensesSummaryPlugin(MaiBotPlugin):
    """Generate daily model usage and cost reports."""

    config_model = ExpensesSummaryConfig

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._scheduler_task: Optional[asyncio.Task] = None
        self._fallback_config = ExpensesSummaryConfig()

    async def on_load(self) -> None:
        try:
            paths = _get_path(self.ctx, "paths")
            data_dir = getattr(paths, "data_dir", None)
            if data_dir:
                _set_data_dir(data_dir)
                _log(self.ctx, "info", f"数据目录已切换: {data_dir}")
            _migrate_legacy_data()
        except Exception:
            pass
        config = self._get_config()
        if config.scheduler.enabled:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            _log(self.ctx, "info", f"定时财报已启用，将在每天 {config.scheduler.time} 发送")

    async def on_unload(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

    async def on_config_update(self, *args: Any, **kwargs: Any) -> None:
        new_config = _extract_updated_config(args, kwargs)
        if new_config is not None:
            self._fallback_config = new_config
        await self.on_unload()
        await self.on_load()

    @Command(
        name="expenses",
        description="生成今日模型调用财报",
        pattern=r"^/(?:expenses|今日财报)$",
    )
    async def expenses_command(
        self,
        ctx: Any = None,
        message: Any = None,
        stream_id: Optional[str] = None,
        chat_stream: Any = None,
        *_args: Any,
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        target_stream_id = _first_present(
            stream_id,
            _get_stream_id(ctx),
            _get_stream_id(message),
            _get_stream_id(chat_stream),
            _get_stream_id(kwargs),
        )
        if not _can_query(self._get_config(), ctx, message, kwargs):
            return True, "你没有权限使用财报查询命令", True
        sent = await self._send_report(self.ctx, target_stream_id)
        return sent, "已发送今日模型调用财报" if sent else "财报发送失败", True

    @Command(
        name="expenses_mode",
        description="切换财报模式",
        pattern=r"^/(?:财报模式|expensesmode)(?:\s+(?P<mode>\S+))?$",
    )
    async def expenses_mode_command(
        self,
        ctx: Any = None,
        message: Any = None,
        stream_id: Optional[str] = None,
        chat_stream: Any = None,
        *_args: Any,
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        target_stream_id = _first_present(
            stream_id,
            _get_stream_id(ctx),
            _get_stream_id(message),
            _get_stream_id(chat_stream),
            _get_stream_id(kwargs),
        )
        config = self._get_config()
        if not _is_admin(config, ctx, message, kwargs):
            response = "你没有权限切换财报模式"
            await _send_command_response(self.ctx, response, target_stream_id)
            return True, response, True

        mode_text = _extract_mode_argument(message, kwargs)
        if not mode_text:
            current_mode = "麦晨风" if _normalize_mode(config.report.mode) == REPORT_MODE_MAICHENFENG else "默认"
            response = f"当前财报模式：{current_mode}。用法：/财报模式 默认 或 /财报模式 麦晨风"
            await _send_command_response(self.ctx, response, target_stream_id)
            return True, response, True

        mode = _normalize_mode(mode_text)
        if not _is_valid_mode_text(mode_text):
            response = "未知财报模式，可用：默认、麦晨风、default、maichenfeng"
            await _send_command_response(self.ctx, response, target_stream_id)
            return True, response, True

        config.report.mode = mode
        label = "麦晨风" if mode == REPORT_MODE_MAICHENFENG else "默认"
        response = f"财报模式已切换为：{label}"
        await _send_command_response(self.ctx, response, target_stream_id)
        return True, response, True

    @Tool(
        name="expenses_summary",
        description="生成并发送今日模型调用次数与成本财报，可用于公开收入、财务总结、趣味汇报等场景。",
    )
    async def expenses_tool(
        self,
        ctx: Any = None,
        stream_id: Optional[str] = None,
        *_args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        target_stream_id = _first_present(
            stream_id,
            _get_stream_id(ctx),
            _get_stream_id(kwargs),
        )
        sent = await self._send_report(self.ctx, target_stream_id)
        return {
            "success": sent,
            "content": (
                "今日模型调用财报已生成并发送。不要再额外复述财报内容，下一步等待用户新消息。"
                if sent
                else "今日模型调用财报生成完成但发送失败。"
            ),
            "metadata": {"pause_execution": sent},
        }

    @Command(
        name="ledger_record",
        description="记录一笔群友投喂，如 /记账 71 抹茶味兽兽",
        pattern=r"^/(?:记账|投喂)(?:\s+(?P<amount>[0-9]+(?:\.[0-9]+)?)(?:\s+(?P<note>.*))?)?$",
    )
    async def ledger_record_command(
        self,
        ctx: Any = None,
        message: Any = None,
        stream_id: Optional[str] = None,
        chat_stream: Any = None,
        *_args: Any,
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        target_stream_id = _first_present(
            stream_id,
            _get_stream_id(ctx),
            _get_stream_id(message),
            _get_stream_id(chat_stream),
            _get_stream_id(kwargs),
        )
        config = self._get_config()
        if not config.ledger.enabled:
            response = "账本功能未启用"
            await _send_command_response(self.ctx, response, target_stream_id)
            return True, response, True
        if config.ledger.admin_only and not _is_admin(config, ctx, message, kwargs):
            response = "记账命令仅管理员可用"
            await _send_command_response(self.ctx, response, target_stream_id)
            return True, response, True

        amount, note = _extract_ledger_args(message, kwargs)
        if amount is None:
            response = "用法：/记账 <金额> [备注]，如 /记账 71 抹茶味兽兽"
            await _send_command_response(self.ctx, response, target_stream_id)
            return True, response, True

        donation, cost, net = _record_donation(amount, note)
        response = (
            f"已记账：{amount:.2f} 元{('（' + note + '）') if note else ''}。"
            f"累计投喂 {donation:.2f} 元，累计开销 {cost:.2f} 元，净收入 {net:+.2f} 元。"
        )
        await _send_command_response(self.ctx, response, target_stream_id)
        return True, response, True

    @Command(
        name="ledger_show",
        description="查看累计账本",
        pattern=r"^/(?:账本|查账)$",
    )
    async def ledger_show_command(
        self,
        ctx: Any = None,
        message: Any = None,
        stream_id: Optional[str] = None,
        chat_stream: Any = None,
        *_args: Any,
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        target_stream_id = _first_present(
            stream_id,
            _get_stream_id(ctx),
            _get_stream_id(message),
            _get_stream_id(chat_stream),
            _get_stream_id(kwargs),
        )
        config = self._get_config()
        if not config.ledger.enabled:
            response = "账本功能未启用"
            await _send_command_response(self.ctx, response, target_stream_id)
            return True, response, True
        donation, cost, net = _ledger_summary()
        ledger = _load_ledger()
        today = datetime.now().strftime("%Y-%m-%d")
        day = ledger.get("records", {}).get(today, {})
        today_donations = day.get("donations", [])
        today_cost = day.get("cost", 0.0)
        today_donation = sum(float(item.get("amount", 0) or 0) for item in today_donations)
        lines = [
            f"📊 累计账本：投喂 {donation:.2f} 元，开销 {cost:.2f} 元，净收入 {net:+.2f} 元",
            f"今日开销 {today_cost:.2f} 元，今日投喂 {today_donation:.2f} 元",
        ]
        if today_donations:
            lines.append(
                "今日明细：" + "；".join(
                    f"{(item.get('note') or '匿名')} {float(item.get('amount', 0) or 0):.2f} 元"
                    for item in today_donations
                )
            )
        response = "\n".join(lines)
        await _send_command_response(self.ctx, response, target_stream_id)
        return True, response, True

    async def _send_report(self, ctx: Any, stream_id: Optional[str] = None) -> bool:
        config = self._get_config()
        mode = _normalize_mode(config.report.mode)
        data = await _collect_report_data(ctx)
        _apply_billing(config, data)
        _record_today_cost(data.total_cost)
        image = await _render_report_image(ctx, data, mode, config.report.title, ledger_enabled=config.ledger.enabled)
        fun = await _generate_fun_elements(ctx, config) if mode == REPORT_MODE_MAICHENFENG else None
        nickname = await _resolve_bot_nickname(ctx) or "麦麦"
        nodes = _build_forward_nodes(data, image, mode, config, fun, nickname=nickname)
        if config.report.use_forward_message:
            sent = await _send_forward(ctx, nodes, stream_id)
        else:
            sent = await _send_plain_messages(ctx, nodes, stream_id)
        # BGM is disabled until sdk2.x provides a public send.audio capability.
        return sent

    async def _scheduler_loop(self) -> None:
        while True:
            config = self._get_config()
            await asyncio.sleep(_seconds_until(config.scheduler.time))
            try:
                await self._send_scheduled_reports()
            except Exception as exc:
                _log(self.ctx, "error", f"定时发送财报失败: {exc}")

    async def _send_scheduled_reports(self) -> None:
        config = self._get_config()
        group_ids = _config_list(config.scheduler.group_ids)
        private_ids = _config_list(config.scheduler.private_ids)
        if not group_ids and not private_ids:
            _log(self.ctx, "warning", "定时财报已启用，但没有配置目标群聊或私聊")
            return

        for group_id in group_ids:
            await self._send_scheduled_target("group", group_id)
        for user_id in private_ids:
            await self._send_scheduled_target("private", user_id)

    async def _send_scheduled_target(self, chat_type: str, target_id: str) -> None:
        stream_id = await _resolve_target_stream_id(self.ctx, chat_type, target_id)
        if not stream_id:
            _log(self.ctx, "error", f"定时发送财报失败: 无法解析 {chat_type} 目标 {target_id} 的 stream_id")
            return

        sent = await self._send_report(self.ctx, stream_id)
        if sent:
            _log(self.ctx, "info", f"定时财报已发送到 {chat_type} 目标 {target_id}")
        else:
            _log(self.ctx, "error", f"定时发送财报失败: {chat_type} 目标 {target_id} 发送返回失败")

    def _get_config(self) -> ExpensesSummaryConfig:
        try:
            config = self.config
        except RuntimeError:
            return self._fallback_config
        return config or self._fallback_config


async def _resolve_bot_nickname(ctx: Any) -> str:
    try:
        config_get = _get_path(ctx, "config.get")
        if callable(config_get):
            value = await _maybe_await(config_get("bot.nickname", ""))
            if value:
                return str(value).strip()
    except Exception:
        pass
    return ""


async def _collect_report_data(ctx: Any) -> ReportData:
    local = _resolve_statistics_api(ctx)
    if local is None:
        _log(ctx, "warning", "财报统计失败: statistics API 不可用")
    costs_raw = await _maybe_await(
        _call_first(local, ["model_trend"], days=1, bucket="hour", top_models=50, metric="cost")
    )
    requests_raw = await _maybe_await(
        _call_first(local, ["model_trend"], days=1, bucket="hour", top_models=50, metric="request")
    )
    messages_raw = await _maybe_await(
        _call_first(local, ["message_trend"], days=1, bucket="hour", top_chats=50)
    )

    model_costs = _merge_model_stats(None, costs_raw, requests_raw, today_only=True)
    total_requests = sum(item.requests for item in model_costs)
    total_cost = sum(item.cost for item in model_costs)
    total_replies = _series_total(messages_raw, today_only=True)

    if total_requests <= 0:
        total_requests = _series_total(requests_raw, today_only=True)
    if total_cost <= 0:
        total_cost = _series_total(costs_raw, today_only=True)

    return ReportData(
        date_text=datetime.now().strftime("%Y年%m月%d日"),
        total_requests=total_requests,
        total_replies=total_replies,
        total_cost=total_cost,
        model_costs=sorted(model_costs, key=lambda item: item.cost, reverse=True),
    )


def _add_months(d: date, months: int) -> date:
    """日期加 N 个月，日超目标月天数时取月末。"""
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    m += 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def _subscription_period(today: date, renew_day: int) -> tuple[date, date]:
    """返回当前订阅周期 (起始日, 结束日)：以每月 renew_day 为边界，起始日当天开启新一期。

    续费日大于当月天数时取月末（如 2 月 31 号 → 2/28），下一期从下月实际续费日开始。
    """
    renew_day = max(1, min(int(renew_day or 15), 31))

    def actual_renew(y: int, m: int) -> date:
        return date(y, m, min(renew_day, calendar.monthrange(y, m)[1]))

    this = actual_renew(today.year, today.month)
    if today >= this:
        start = this
    else:
        prev = _add_months(date(today.year, today.month, 1), -1)
        start = actual_renew(prev.year, prev.month)
    nxt = _add_months(date(start.year, start.month, 1), 1)
    end = actual_renew(nxt.year, nxt.month)
    return start, end


_RATE_CACHE_FILENAME = "exchange_rate.json"
_RATE_API_TIMEOUT = 6

# 运行时数据目录：优先使用 MaiBot SDK 的 ctx.paths.data_dir（on_load 时注入），
# 未注入时回退到插件目录下的 data/，避免升级/重装时数据丢失。
_DATA_DIR: Optional[Path] = None


def _set_data_dir(path: Any) -> None:
    """设置运行时数据目录（由 on_load 从 ctx.paths.data_dir 注入）。"""
    global _DATA_DIR
    if path:
        _DATA_DIR = Path(str(path))


def _data_dir() -> Path:
    """返回当前数据目录。"""
    return _DATA_DIR or (Path(__file__).resolve().parent / "data")


def _migrate_legacy_data() -> None:
    """把旧版插件目录下 data/ 的运行时数据迁移到新的数据目录（仅首次，避免历史账本丢失）。"""
    data_dir = _data_dir()
    legacy_dir = Path(__file__).resolve().parent / "data"
    if legacy_dir == data_dir:
        return
    for name in (LEDGER_FILENAME, _RATE_CACHE_FILENAME):
        legacy = legacy_dir / name
        if legacy.exists() and not (data_dir / name).exists():
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, data_dir / name)
            except Exception:
                pass


def _rate_cache_path() -> Path:
    path = _data_dir() / _RATE_CACHE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fetch_usd_cny_rate(on_date: Optional[date] = None) -> Optional[float]:
    """从公开 API 获取 USD→CNY 汇率；on_date 指定时取该历史日期牌价。失败返回 None。"""
    sources = []
    if on_date is not None:
        sources.append(
            (f"https://api.frankfurter.app/{on_date.isoformat()}?from=USD&to=CNY", "frankfurter-hist")
        )
    sources.append(("https://api.frankfurter.app/latest?from=USD&to=CNY", "frankfurter"))
    sources.append(("https://open.er-api.com/v6/latest/USD", "er-api"))
    for url, tag in sources:
        try:
            with urllib.request.urlopen(url, timeout=_RATE_API_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if tag == "er-api":
                rate = payload.get("rates", {}).get("CNY")
            else:
                rate = payload.get("rates", {}).get("CNY")
            if rate:
                value = float(rate)
                if 1.0 < value < 20.0:  # 合理性校验
                    return value
        except Exception:
            continue
    return None


def _get_exchange_rate(billing: Any, today: Optional[date] = None) -> float:
    """获得订阅计费用汇率：优先取续费日（本期起始日）快照，快照缺失/过期时自动获取并缓存，失败用配置兜底。"""
    today = today or date.today()
    fallback = float(getattr(billing, "exchange_rate", 0) or 0)
    try:
        cache_path = _rate_cache_path()
        cache = {}
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text("utf-8"))
            except Exception:
                cache = {}
        period_start, _period_end = _subscription_period(today, getattr(billing, "renew_day", 15))
        key = period_start.isoformat()
        cached_rate = cache.get(key)
        if isinstance(cached_rate, (int, float)) and cached_rate > 0:
            return float(cached_rate)
        # 无条件优先取续费日（本期起始日）的历史牌价，失败再试实时接口
        rate = _fetch_usd_cny_rate(period_start)
        if rate is None:
            rate = _fetch_usd_cny_rate()
        if rate and rate > 0:
            cache[key] = rate
            try:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
            except Exception:
                pass
            return rate
    except Exception:
        pass
    if fallback > 0:
        return fallback
    return 0.0


def _apply_billing(config: ExpensesSummaryConfig, data: ReportData) -> None:
    """按计费模式调整财报成本口径（修改 data 的 total_cost / cost_note / 模型 subscribed）。"""
    billing = config.billing
    mode = str(billing.mode or "").strip().lower()
    if mode in ("", "usage", "pay_as_you_go", "按量"):
        return

    currency = str(billing.currency or "usd").strip().lower()
    renew_amount = float(getattr(billing, "renew_amount", 0) or 0)
    if renew_amount <= 0:
        return
    if currency == "cny":
        period_cny = renew_amount
        rate_text = ""
    elif currency == "usd":
        rate = _get_exchange_rate(billing)
        if rate <= 0:
            return
        period_cny = renew_amount * rate
        rate_text = f"（汇率 {rate:.4f}）"
    else:
        return

    today = date.today()
    period_start, period_end = _subscription_period(today, billing.renew_day)
    period_days = max(1, (period_end - period_start).days)
    period_cost = (period_cny / period_days) if billing.share_daily else period_cny
    if currency == "cny":
        spend_text = f"{period_cny:.2f} 元"
    else:
        spend_text = f"{renew_amount:g} USD{rate_text}≈{period_cny:.2f} 元"
    period_text = f"{period_start.strftime('%m月%d日')}~{period_end.strftime('%m月%d日')}（{period_days} 天）"
    spread_text = "按天均摊" if billing.share_daily else "整期计入"

    if mode in ("subscription", "订阅"):
        for item in data.model_costs:
            item.subscribed = True
        data.total_cost = period_cost
        data.cost_note = f"订阅口径：本期 {spend_text}，{period_text}，{spread_text}，今日成本 {period_cost:.4f} 元"
        return

    if mode in ("hybrid", "混合"):
        keys = [str(k).strip().lower() for k in (billing.subscription_models or []) if str(k).strip()]
        for item in data.model_costs:
            if keys and any(k in str(item.name).lower() for k in keys):
                item.subscribed = True
                item.cost = 0.0
        data.total_cost = period_cost + sum(item.cost for item in data.model_costs)
        sub_count = sum(1 for item in data.model_costs if item.subscribed)
        data.cost_note = (
            f"混合口径：本期订阅 {spend_text}，{period_text}，{spread_text}"
            + (f"；{sub_count} 个订阅模型不计按量，" if sub_count else "")
            + "其余按量计费。"
        )


def _resolve_statistics_api(ctx: Any) -> Any:
    for path in ("statistics.local", "statistics", "stats.local", "stats"):
        candidate = _get_path(ctx, path)
        if candidate is None:
            continue
        if all(callable(getattr(candidate, name, None)) for name in ("models", "model_trend", "message_trend")):
            return candidate
    return None


async def _generate_fun_elements(ctx: Any, config: ExpensesSummaryConfig) -> FunElements:
    configured_xiao_name = _pick_configured_text(config.fallback.xiao_names, "小麦")
    fallback = FunElements(
        xiao_name=configured_xiao_name,
        location=random.choice(config.fallback.locations or ["家里"]),
        went_to=_fallback_went_to(config.fallback.locations),
        poem=random.choice(config.fallback.poems or ["谢谢大家。"]),
    )
    llm = _get_path(ctx, "llm")
    generate = getattr(llm, "generate", None)
    if not callable(generate):
        _log(ctx, "warning", "财报文案模型调用失败: ctx.llm.generate 不可用，使用 fallback 素材")
        return fallback

    prompt = (
        "为模型调用财报生成三个短素材，只输出 JSON，不要解释。\n"
        "字段必须是：location、went_to、poem。\n"
        "location: 一个普通日常的地点（如家里、图书馆、教室），不要夸张。\n"
        "went_to: 按“我去了：地点、地点、地点、地点 回复群员信息。”格式输出，地点用普通日常地点，不要浮夸。\n"
        "poem: 一句40字以内朴实温和的短句。\n"
        "示例：{\"location\":\"图书馆\",\"went_to\":\"我去了：图书馆、教室、实验室、宿舍 回复群员信息。\",\"poem\":\"今天也是认真的一天。\"}"
    )
    try:
        llm_task = _get_llm_task(config)
        result = await _call_llm_generate(generate, prompt, llm_task)
    except Exception as exc:
        _log(ctx, "warning", f"财报文案模型调用失败，使用 fallback 素材: {exc}")
        return fallback

    text = _normalize_llm_text(result)
    if not text:
        _log(ctx, "warning", f"财报文案模型返回为空，使用 fallback 素材: {_summarize_value(result)}")
        return fallback
    values = _parse_fun_elements_text(text)
    if not values:
        _log(ctx, "warning", f"财报文案模型返回无法解析，使用 fallback 素材: {text[:120]}")
        return fallback
    _log(ctx, "info", f"财报文案模型调用成功，使用任务: {_get_llm_task(config)}")
    return FunElements(
        xiao_name=configured_xiao_name,
        location=values.get("location") or fallback.location,
        went_to=_normalize_went_to(values.get("went_to")) or fallback.went_to,
        poem=values.get("poem") or fallback.poem,
    )


async def _call_llm_generate(generate: Callable, prompt: str, llm_task: str) -> Any:
    call_specs = (
        ((), {"prompt": prompt, "model": llm_task, "temperature": 0.8, "max_tokens": 180}),
        ((), {"prompt": prompt, "model": llm_task, "temperature": 0.8}),
        ((prompt,), {"model": llm_task, "temperature": 0.8, "max_tokens": 180}),
        ((prompt,), {"model": llm_task, "temperature": 0.8}),
        ((prompt,), {"model": llm_task}),
    )
    last_type_error: Optional[TypeError] = None
    for args, kwargs in call_specs:
        try:
            return await _maybe_await(generate(*args, **kwargs))
        except TypeError as exc:
            last_type_error = exc
            continue
    if last_type_error:
        raise last_type_error
    return None


def _get_llm_task(config: ExpensesSummaryConfig) -> str:
    task = getattr(config.report, "llm_task", None)
    if not task:
        task = getattr(config.report, "llm_model", None)
    return str(task or "utils")


def _normalize_llm_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    for key in ("response", "text", "content", "reply", "message", "result", "output"):
        value = _pick(result, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _normalize_llm_text(value)
            if nested:
                return nested
    return str(result or "").strip()


def _parse_fun_elements_text(text: str) -> dict[str, str]:
    json_values = _parse_fun_elements_json(text)
    if json_values:
        return json_values
    if _looks_like_json_text(text):
        return {}

    values: dict[str, str] = {}
    for line in str(text or "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
        elif "：" in line:
            key, value = line.split("：", 1)
        else:
            continue
        normalized_key = key.strip().lower()
        clean_value = _clean_fun_value(value)
        if not clean_value:
            continue
        if "去了" in normalized_key or "went" in normalized_key:
            values["went_to"] = clean_value[:120]
        elif "地点" in normalized_key or "location" in normalized_key:
            values["location"] = clean_value[:40]
        elif "诗" in normalized_key or "句" in normalized_key or "poem" in normalized_key:
            values["poem"] = clean_value[:80]
    return values


def _parse_fun_elements_json(text: str) -> dict[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return _parse_fun_elements_jsonish(raw)
    if not isinstance(data, dict):
        return {}
    mappings = {
        "location": ("location", "地点"),
        "went_to": ("went_to", "去了", "went"),
        "poem": ("poem", "诗句", "短句"),
    }
    values: dict[str, str] = {}
    for target, keys in mappings.items():
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                values[target] = _clean_fun_value(value)
                break
    return values


def _parse_fun_elements_jsonish(text: str) -> dict[str, str]:
    raw = str(text or "")
    mappings = {
        "location": ("location", "地点"),
        "went_to": ("went_to", "去了", "went"),
        "poem": ("poem", "诗句", "短句"),
    }
    values: dict[str, str] = {}
    for target, keys in mappings.items():
        for key in keys:
            match = re.search(
                rf'["“]?{re.escape(key)}["”]?\s*[:：]\s*["“](.*?)(?=["”]\s*[,，}}]|$)',
                raw,
                flags=re.DOTALL,
            )
            if match:
                value = _clean_fun_value(match.group(1))
                if value:
                    values[target] = value
                    break
    return values


def _looks_like_json_text(text: str) -> bool:
    raw = str(text or "").strip()
    return raw.startswith("{") or raw.startswith("```") or '"location"' in raw or "'location'" in raw


def _clean_fun_value(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\\n", " ").strip()
    text = text.strip(" \t\r\n`")
    text = text.strip('"“”\'‘’')
    text = text.strip(" \t\r\n,，:：")
    text = text.strip('"“”\'‘’')
    while text.endswith((",", "，", '"', "'", "”", "’")):
        text = text[:-1].strip()
    return text


def _pick_configured_text(values: list[str], fallback: str) -> str:
    choices = [str(item).strip() for item in (values or []) if str(item).strip()]
    return random.choice(choices) if choices else fallback


def _fallback_went_to(locations: list[str]) -> str:
    choices = list(dict.fromkeys(str(item).strip() for item in (locations or []) if str(item).strip()))
    if not choices:
        choices = ["家里", "图书馆", "教室", "自习室"]
    picked = random.sample(choices, k=min(4, len(choices)))
    return f"我去了：{'、'.join(picked)} 回复群员信息📱。"


def _normalize_went_to(text: Optional[str]) -> str:
    raw = _clean_fun_value(text)
    if not raw:
        return ""
    if "我去了：" not in raw:
        return raw[:120]

    prefix, rest = raw.split("我去了：", 1)
    suffix = ""
    for marker in (" 回复群员信息", "回复群员信息"):
        if marker in rest:
            rest, tail = rest.split(marker, 1)
            suffix_tail = _clean_fun_value(tail)
            suffix = f" 回复群员信息{suffix_tail}"
            break

    places = [_clean_fun_value(item) for item in rest.replace("，", "、").split("、")]
    places = [item for item in places if item]
    unique_places = list(dict.fromkeys(places))
    if not unique_places:
        return raw[:120]
    if not suffix:
        suffix = " 回复群员信息📱。"
    return f"{prefix}我去了：{'、'.join(unique_places[:4])}{suffix}"[:120]


def _summarize_value(value: Any) -> str:
    text = repr(value)
    return text[:240]


def _extract_updated_config(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Optional[ExpensesSummaryConfig]:
    for key in ("new_config", "config", "updated_config"):
        candidate = kwargs.get(key)
        if _looks_like_plugin_config(candidate):
            return candidate

    for candidate in reversed(args):
        if _looks_like_plugin_config(candidate):
            return candidate
    return None


def _looks_like_plugin_config(candidate: Any) -> bool:
    if candidate is None:
        return False
    if isinstance(candidate, ExpensesSummaryConfig):
        return True
    return all(hasattr(candidate, attr) for attr in ("report", "scheduler"))


def _merge_model_stats(
    models_raw: Any,
    costs_raw: Any,
    requests_raw: Any,
    today_only: bool = False,
) -> list[ModelCost]:
    merged: dict[str, ModelCost] = {}

    for item in _iter_items(models_raw):
        name = str(_pick(item, "model_name", "model", "name", default="未知模型"))
        stat = merged.setdefault(name, ModelCost(name=name))
        stat.requests += int(_pick_number(
            item,
            "requests",
            "request_count",
            "total_requests",
            "call_count",
            "calls",
            "count",
            "total",
        ))
        stat.replies += int(_pick_number(
            item,
            "replies",
            "reply",
            "reply_count",
            "total_replies",
            "response_count",
            "responses",
            "message_count",
            "messages",
        ))
        stat.cost += _pick_number(
            item,
            "cost",
            "total_cost",
            "amount",
            "total_amount",
            "price",
            "total_price",
        )

    for name, value in _series_values_by_label(costs_raw, today_only=today_only).items():
        stat = merged.setdefault(name, ModelCost(name=name))
        if stat.cost <= 0:
            stat.cost = value

    for name, value in _series_values_by_label(requests_raw, today_only=today_only).items():
        stat = merged.setdefault(name, ModelCost(name=name))
        if stat.requests <= 0:
            stat.requests = int(value)

    return [item for item in merged.values() if item.requests or item.replies or item.cost]


async def _render_report_image(ctx: Any, data: ReportData, mode: str, title: str, ledger_enabled: bool = True) -> Any:
    html_doc = _build_report_html(data, mode, title, ledger_enabled=ledger_enabled)
    renderer = _get_path(ctx, "render")
    html2png = getattr(renderer, "html2png", None)
    if callable(html2png):
        try:
            return await _maybe_await(
                html2png(
                    html_doc,
                    selector=".sheet",
                    viewport={"width": 900, "height": 1200},
                    device_scale_factor=1.0,
                    full_page=True,
                )
            )
        except TypeError:
            return await _maybe_await(html2png(html_doc))
    return html_doc


def _build_report_html(data: ReportData, mode: str, title: str, ledger_enabled: bool = True) -> str:
    is_fun = mode == REPORT_MODE_MAICHENFENG
    page_title = "今日模型调用财报" if is_fun else title
    subtitle = "今日 0 点至当前的模型调用概览" if is_fun else "今日 0 点至当前的模型调用概览"
    rows = data.model_costs or [ModelCost(name="暂无模型记录")]
    max_cost = max([item.cost for item in rows] + [0.01])
    body_rows = "\n".join(
        _model_row_html(item, max_cost) for item in rows[:12]
    )

    ledger_block = ""
    net_footer = f"净收入：-{data.total_cost:.4f} 元。"
    cost_note_html = f'<div class="cost-note">{html.escape(data.cost_note)}</div>' if data.cost_note else ""
    if ledger_enabled:
        donation, cost, net = _ledger_summary()
        if donation > 0 or cost > 0:
            ledger_block = f"""
  <div class="ledger-strip">
    <div class="ledger-item"><div class="ledger-label">累计投喂</div><div class="ledger-value">{donation:.2f} 元</div></div>
    <div class="ledger-item"><div class="ledger-label">累计开销</div><div class="ledger-value">{cost:.2f} 元</div></div>
    <div class="ledger-item"><div class="ledger-label">净收入</div><div class="ledger-value">{net:+.2f} 元</div></div>
  </div>"""
        net_footer = f"净收入：{net:+.2f} 元（累计投喂 {donation:.2f} 元，累计开销 {cost:.2f} 元）。"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  width: 900px;
  min-height: 1200px;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  color: #202124;
  background: #f4f7f8;
}}
.sheet {{
  min-height: 1200px;
  padding: 54px;
  background: linear-gradient(180deg, #ffffff 0%, #eef4f5 100%);
}}
.head {{
  border-left: 10px solid #0f766e;
  padding-left: 24px;
  margin-bottom: 34px;
}}
.kicker {{
  font-size: 28px;
  color: #52605f;
  margin-bottom: 8px;
}}
h1 {{
  margin: 0;
  font-size: 54px;
  line-height: 1.14;
  letter-spacing: 0;
}}
.subtitle {{
  margin-top: 12px;
  font-size: 26px;
  color: #56616a;
}}
.metrics {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 18px;
  margin: 34px 0;
}}
.metric {{
  background: #ffffff;
  border: 1px solid #d9e3e4;
  border-radius: 8px;
  padding: 22px;
}}
.label {{
  font-size: 22px;
  color: #667478;
}}
.value {{
  margin-top: 10px;
  font-size: 38px;
  font-weight: 700;
  color: #0b3b3f;
}}
.section-title {{
  font-size: 30px;
  font-weight: 700;
  margin: 40px 0 18px;
}}
.row {{
  background: #ffffff;
  border: 1px solid #dce5e6;
  border-radius: 8px;
  padding: 18px 20px;
  margin-bottom: 12px;
}}
.row-top {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 18px;
}}
.model {{
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 24px;
  font-weight: 700;
}}
.cost {{
  flex: 0 0 auto;
  font-size: 24px;
  color: #0f766e;
  font-weight: 700;
}}
.bar {{
  height: 12px;
  margin: 14px 0 8px;
  border-radius: 99px;
  background: #dde7e8;
  overflow: hidden;
}}
.bar span {{
  display: block;
  height: 100%;
  background: #0f766e;
}}
.bar span.sub {{
  background: #b45309;
}}
.minor {{
  font-size: 20px;
  color: #657276;
}}
.footer {{
  margin-top: 36px;
  padding-top: 20px;
  border-top: 1px solid #cad7d8;
  font-size: 22px;
  color: #52605f;
}}
.ledger-strip {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 18px;
  margin: 34px 0 0;
  background: #0f766e;
  border-radius: 10px;
  padding: 20px 24px;
}}
.ledger-item {{
  text-align: center;
}}
.ledger-label {{
  font-size: 20px;
  color: #cfe8e5;
}}
.ledger-value {{
  margin-top: 6px;
  font-size: 34px;
  font-weight: 700;
  color: #ffffff;
}}
.cost-note {{
  margin-top: 8px;
  font-size: 17px;
  line-height: 1.55;
  color: #8a979b;
}}
.cost.sub {{
  color: #b45309;
  font-size: 20px;
}}
</style>
</head>
<body>
<main class="sheet">
  <section class="head">
    <div class="kicker">{html.escape(data.date_text)}</div>
    <h1>{html.escape(page_title)}</h1>
    <div class="subtitle">{html.escape(subtitle)}</div>
  </section>
  <section class="metrics">
    <div class="metric"><div class="label">累计请求</div><div class="value">{data.total_requests}</div></div>
    <div class="metric"><div class="label">回复消息</div><div class="value">{int(data.total_replies)}</div></div>
    <div class="metric"><div class="label">回复成本</div><div class="value">{data.total_cost:.4f} 元</div>{cost_note_html}</div>
  </section>
  <div class="section-title">各模型回复成本</div>
  {body_rows}
  {ledger_block}
  <div class="footer">{net_footer} {"数据来自 MaiBot 本地统计接口。" if is_fun else "数据来自 MaiBot 本地统计接口。"}</div>
</main>
</body>
</html>"""


def _model_row_html(item: ModelCost, max_cost: float) -> str:
    width = max(4, min(100, int(item.cost / max_cost * 100)))
    detail = f"请求 {item.requests} 次"
    if item.replies > 0:
        detail += f" / 回复 {item.replies} 条"
    if item.subscribed:
        cost_html = '<div class="cost sub">订阅包</div>'
        span_class = ' class="sub"'
    else:
        cost_html = f'<div class="cost">{item.cost:.4f} 元</div>'
        span_class = ""
    return f"""<div class="row">
  <div class="row-top">
    <div class="model">{html.escape(item.name)}</div>
    {cost_html}
  </div>
  <div class="bar"><span{span_class} style="width:{width}%"></span></div>
  <div class="minor">{detail}</div>
</div>"""


def _build_forward_nodes(
    data: ReportData,
    image: Any,
    mode: str,
    config: ExpensesSummaryConfig,
    fun: Optional[FunElements] = None,
    nickname: str = "麦麦",
) -> list[dict[str, Any]]:
    opening = _build_opening(data, mode, config, fun)
    thanks = _build_thanks(data, config, fun)
    return [
        _make_forward_node("text", opening, nickname=nickname),
        _image_node(image, nickname=nickname),
        _make_forward_node("text", thanks, nickname=nickname),
    ]


def _build_opening(
    data: ReportData,
    mode: str,
    config: ExpensesSummaryConfig,
    fun: Optional[FunElements] = None,
) -> str:
    if mode == REPORT_MODE_MAICHENFENG:
        xiao_name = fun.xiao_name if fun else random.choice(config.fallback.xiao_names or ["小麦"])
        location = fun.location if fun else random.choice(config.fallback.locations or ["家里"])
        went_to = fun.went_to if fun else _fallback_went_to(config.fallback.locations)
        template = str(config.fallback.opening_template or "").strip() or DEFAULT_MAICHENFENG_OPENING
        return _render_template(
            template,
            xiao_name=xiao_name,
            location=location,
            date_text=data.date_text,
            total_requests=data.total_requests,
            total_replies=int(data.total_replies),
            total_cost=f"{data.total_cost:.4f}",
            went_to=went_to,
        )
    template = config.report.default_opening or ReportConfig().default_opening
    return template.replace("{date}", data.date_text)


# 麦晨风模式内置默认文案（原版 1.0.2）。配置 fallback.opening_template / thanks_template 可覆盖。
DEFAULT_MAICHENFENG_OPENING = (
    "我是{xiao_name}，我在{location}向各位网友兼股东汇报"
    "{date_text}我在全网的收入情况。\n"
    "{date_text}收入再次创出历史新高📈✨\n"
    "我在{date_text}的税前总收入为：0万0元💸。其中：所有收入 0万0元。\n"
    "除广告收入和带货佣金外，在缴纳了约25%即 0万0元 的个人所得税之后，"
    "此为系统自动扣除，"
    "***不🙅‍♀️可🙅‍♀️能🙅‍♀️不🙅‍♀️交*** 😡💢（咬牙切齿😣），"
    "我的税后总收入为 0万0元🙃。\n\n"
    "🖕以上为我的收入情况，下面是我的支出情况👇\n\n"
    "{date_text}{went_to}"
)

DEFAULT_MAICHENFENG_THANKS = (
    "所以，{date_text}我的净收入为 -{total_cost} 元 📉😵💫。\n\n"
    "{xiao_name}一路走来，是因为屏幕前各位群友的支持🤝💛才有了不一样的人生🌟。\n"
    "{poem} 📜✨\n"
    "也正是你们的陪伴，给了我笃定前行的勇气💪🕊️。\n"
    "再次感谢各位群友的支持🙏尤其要感谢 {thanks_names} 的强力支持⚡🔥！\n"
    "以及所有群员的陪伴❤️ 再次谢谢大家🙇‍♂️🙇‍♀️！"
)


def _render_template(template: str, **values: str) -> str:
    """替换模板中的 {占位符}，未提供的占位符保留原样。"""
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def _build_thanks(
    data: ReportData,
    config: ExpensesSummaryConfig,
    fun: Optional[FunElements] = None,
) -> str:
    xiao_name = fun.xiao_name if fun else random.choice(config.fallback.xiao_names or ["小麦"])
    poem = fun.poem if fun else random.choice(config.fallback.poems or ["谢谢大家。"])
    template = str(config.fallback.thanks_template or "").strip() or DEFAULT_MAICHENFENG_THANKS

    if config.ledger.enabled:
        thanks_names_list, thanks_amounts_list = _ledger_thanks_parts()
        thanks_names = "、".join(thanks_names_list) or "每一位群友"
        thanks_amounts = "、".join(thanks_amounts_list) or "每一位群友"
        thanks_suffix = "等" if len(thanks_names_list) > 6 else ""
    else:
        names = [
            str(item).split("：")[0].strip()
            for item in (config.fallback.thanks_list or [])
            if str(item).strip()
        ]
        thanks_names = "、".join(names) or "每一位群友"
        thanks_amounts = "、".join(config.fallback.thanks_list or []) or "每一位群友"
        thanks_suffix = ""

    donation, cost, net = _ledger_summary()
    ledger_line = ""
    if config.ledger.enabled and (donation > 0 or cost > 0):
        ledger_line = f"📊 累计账本：投喂 {donation:.2f} 元，开销 {cost:.2f} 元，净收入 {net:+.2f} 元。\n\n"

    return _render_template(
        template,
        date_text=data.date_text,
        total_cost=f"{data.total_cost:.4f}",
        xiao_name=xiao_name,
        poem=poem,
        thanks_names=thanks_names,
        thanks_amounts=thanks_amounts,
        thanks_suffix=thanks_suffix,
        ledger_line=ledger_line,
    )


# --------------------------------------------------------------------------- #
# 账本（JSON 记账）
# --------------------------------------------------------------------------- #

LEDGER_FILENAME = "ledger.json"


def _ledger_path() -> Path:
    path = _data_dir() / LEDGER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_ledger() -> dict[str, Any]:
    path = _ledger_path()
    if not path.exists():
        return {"version": 1, "records": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "records": {}}


def _save_ledger(ledger: dict[str, Any]) -> None:
    _ledger_path().write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


def _ledger_thanks_parts() -> tuple[list[str], list[str]]:
    """生成贡献榜：
    - 累计投喂 TOP3 保底（长期贡献者不掉榜）
    - 最近 3 天（今天/明天/后天窗口）内投喂过的人必定上榜，按窗口内金额降序优先展示
    返回 (名字列表, 名字+累计金额列表)；空账本时两个都是 []。"""
    ledger = _load_ledger()
    window_start = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    cumulative: dict[str, float] = {}
    window_amount: dict[str, float] = {}
    for day_key, day in ledger.get("records", {}).items():
        for item in day.get("donations", []):
            note = str(item.get("note") or "").strip() or "匿名"
            amt = float(item.get("amount", 0) or 0)
            cumulative[note] = cumulative.get(note, 0.0) + amt
            if str(day_key) >= window_start:
                window_amount[note] = window_amount.get(note, 0.0) + amt

    if not cumulative:
        return [], []

    top3 = [name for name, _ in sorted(cumulative.items(), key=lambda kv: -kv[1])[:3]]
    recent = sorted(window_amount, key=lambda name: -window_amount[name])
    ordered = list(dict.fromkeys([*recent, *top3]))
    amounts = [f"{name} {cumulative[name]:.2f} 元" for name in ordered]
    return ordered, amounts


def _ledger_thanks_line() -> str:
    names, amounts = _ledger_thanks_parts()
    if not names:
        return "感谢每一位群友的投喂🙏\n"
    suffix = "等" if len(names) > 6 else ""
    return f"特别感谢 {'、'.join(amounts)}{suffix} 的投喂🙏\n"


def _record_today_cost(cost: float) -> None:
    if not cost or cost <= 0:
        return
    try:
        ledger = _load_ledger()
        today = datetime.now().strftime("%Y-%m-%d")
        day = ledger["records"].setdefault(today, {"cost": 0.0, "donations": []})
        day["cost"] = round(float(cost), 4)
        _save_ledger(ledger)
    except Exception:
        pass


def _record_donation(amount: float, note: str = "") -> tuple[float, float, float]:
    try:
        ledger = _load_ledger()
        today = datetime.now().strftime("%Y-%m-%d")
        day = ledger["records"].setdefault(today, {"cost": 0.0, "donations": []})
        day["donations"].append(
            {
                "amount": round(float(amount), 2),
                "note": note,
                "at": datetime.now().strftime("%H:%M"),
            }
        )
        _save_ledger(ledger)
    except Exception:
        pass
    return _ledger_summary(ledger)


def _ledger_summary(ledger: Optional[dict[str, Any]] = None) -> tuple[float, float, float]:
    ledger = ledger or _load_ledger()
    total_cost = 0.0
    total_donation = 0.0
    for day in ledger.get("records", {}).values():
        total_cost += float(day.get("cost", 0.0) or 0.0)
        for item in day.get("donations", []):
            total_donation += float(item.get("amount", 0.0) or 0.0)
    return total_donation, total_cost, total_donation - total_cost


def _extract_ledger_args(message: Any, kwargs: dict[str, Any]) -> tuple[Optional[float], str]:
    amount_raw = (
        _get_path(kwargs, "amount")
        or _get_path(kwargs, "matched_groups.amount")
        or _get_path(kwargs, "groups.amount")
    )
    note = (
        _get_path(kwargs, "note")
        or _get_path(kwargs, "matched_groups.note")
        or _get_path(kwargs, "groups.note")
    )
    if amount_raw is None:
        content = ""
        for path in ("message.content", "content", "text", "raw_message"):
            content = _get_path(kwargs, path) or _get_path(message, path)
            if isinstance(content, str) and content.strip():
                break
        match = re.search(r"/(?:记账|投喂)\s+([0-9]+(?:\.[0-9]+)?)(?:\s+(.*))?", content or "")
        if match:
            amount_raw = match.group(1)
            note = (match.group(2) or "").strip()
    if not amount_raw:
        return None, str(note or "").strip()
    try:
        return float(amount_raw), str(note or "").strip()
    except (TypeError, ValueError):
        return None, str(note or "").strip()


def _make_forward_node(segment_type: str, content: str, nickname: str = "麦麦") -> dict[str, Any]:
    return {
        "user_id": "0",
        "nickname": nickname,
        "segments": [{"type": segment_type, "content": content}],
    }


def _image_node(image: Any, nickname: str = "麦麦") -> dict[str, Any]:
    image_base64 = _extract_image_base64(image)
    if image_base64:
        return _make_forward_node("image", image_base64, nickname=nickname)
    if isinstance(image, str) and image.lstrip().startswith("<!doctype"):
        return _make_forward_node("text", image, nickname=nickname)
    return _make_forward_node("text", "图片生成失败，无法展示财报图。", nickname=nickname)


def _extract_image_base64(image: Any) -> str:
    if isinstance(image, bytes):
        return base64.b64encode(image).decode("utf-8")

    if isinstance(image, dict):
        for key in ("image_base64", "base64", "data", "content"):
            value = image.get(key)
            if isinstance(value, str) and value.strip():
                return _strip_data_url(value)
            if isinstance(value, bytes):
                return base64.b64encode(value).decode("utf-8")
        for key in ("path", "file_path", "filename"):
            value = image.get(key)
            if isinstance(value, str) and value.strip():
                encoded = _base64_from_file(value)
                if encoded:
                    return encoded
        return ""

    if isinstance(image, str):
        value = image.strip()
        if not value or value.startswith("<!doctype"):
            return ""
        if value.startswith("data:image/"):
            return _strip_data_url(value)
        encoded = _base64_from_file(value)
        if encoded:
            return encoded
        if _looks_like_base64(value):
            return value
    return ""


def _base64_from_file(value: str) -> str:
    try:
        path = Path(value)
        if path.exists() and path.is_file():
            return base64.b64encode(path.read_bytes()).decode("utf-8")
    except Exception:
        return ""
    return ""


def _strip_data_url(value: str) -> str:
    if "," in value and value.lstrip().startswith("data:image/"):
        return value.split(",", 1)[1].strip()
    return value.strip()


def _looks_like_base64(value: str) -> bool:
    clean = value.strip()
    if len(clean) < 64:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r")
    return all(char in allowed for char in clean)


def _forward_nodes_plain_text(nodes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for node in nodes:
        for segment in node.get("segments", []):
            if segment.get("type") == "text":
                content = str(segment.get("content") or "").strip()
                if content:
                    parts.append(content)
            elif segment.get("type") == "image":
                parts.append("[图片]")
    return "\n\n".join(parts)


async def _send_forward(
    ctx: Any,
    nodes: list[dict[str, Any]],
    stream_id: Optional[str] = None,
) -> bool:
    sender = _get_path(ctx, "send")
    forward = getattr(sender, "forward", None)
    target_stream_id = stream_id or _get_stream_id(ctx)
    if not callable(forward):
        text = _forward_nodes_plain_text(nodes)
        text_method = getattr(sender, "text", None) or getattr(sender, "message", None)
        if callable(text_method):
            try:
                sent = await _maybe_await(text_method(text, target_stream_id))
            except TypeError:
                sent = await _maybe_await(text_method(text))
            return bool(sent)
        _log(ctx, "error", "当前上下文不支持发送转发消息")
        return False

    if not target_stream_id:
        _log(ctx, "error", "发送财报失败: 缺少 stream_id")
        return False

    plain_text = _forward_nodes_plain_text(nodes) or "[今日模型调用财报]"
    call_specs = (
        ((nodes, target_stream_id), {"storage_message": False, "processed_plain_text": plain_text}),
        ((nodes, target_stream_id), {}),
        ((), {"messages": nodes, "stream_id": target_stream_id, "storage_message": False, "processed_plain_text": plain_text}),
        ((), {"messages": nodes, "stream_id": target_stream_id}),
    )
    for args, kwargs in call_specs:
        try:
            sent = await _maybe_await(forward(*args, **kwargs))
            if not sent:
                _log(ctx, "error", "发送财报合并转发失败: send.forward 返回 False")
            return bool(sent)
        except TypeError:
            continue
        except Exception as exc:
            _log(ctx, "error", f"发送财报合并转发失败: {exc}")
            return False
    _log(ctx, "error", "发送财报合并转发失败: send.forward 参数不兼容")
    return False


async def _send_plain_messages(
    ctx: Any,
    nodes: list[dict[str, Any]],
    stream_id: Optional[str] = None,
) -> bool:
    sender = _get_path(ctx, "send")
    target_stream_id = stream_id or _get_stream_id(ctx)
    if not target_stream_id:
        _log(ctx, "error", "发送财报失败: 缺少 stream_id")
        return False

    sent_any = False
    for node in nodes:
        for segment in node.get("segments", []):
            segment_type = segment.get("type")
            content = str(segment.get("content") or "")
            if segment_type == "text":
                sent_any = await _send_text_segment(sender, content, target_stream_id) or sent_any
            elif segment_type == "image":
                sent_any = await _send_image_segment(sender, content, target_stream_id) or sent_any
    return sent_any


async def _send_text_segment(sender: Any, content: str, stream_id: str) -> bool:
    text_method = getattr(sender, "text", None) or getattr(sender, "message", None)
    if not callable(text_method):
        return False
    for args, kwargs in (
        ((content, stream_id), {}),
        ((content,), {"stream_id": stream_id}),
        ((content,), {}),
    ):
        try:
            sent = await _maybe_await(text_method(*args, **kwargs))
            return bool(sent)
        except TypeError:
            continue
    return False


async def _send_command_response(
    ctx: Any,
    content: str,
    stream_id: Optional[str] = None,
) -> bool:
    target_stream_id = stream_id or _get_stream_id(ctx)
    if not target_stream_id:
        _log(ctx, "warning", f"发送命令回应失败: 缺少 stream_id，回应内容: {content}")
        return False
    sender = _get_path(ctx, "send")
    sent = await _send_text_segment(sender, content, target_stream_id)
    if not sent:
        _log(ctx, "warning", f"发送命令回应失败: {content}")
    return sent


async def _send_image_segment(sender: Any, content: str, stream_id: str) -> bool:
    image_method = getattr(sender, "image", None)
    if not callable(image_method):
        return await _send_text_segment(sender, "[图片]", stream_id)
    for args, kwargs in (
        ((content, stream_id), {}),
        ((content,), {"stream_id": stream_id}),
        ((), {"image_base64": content, "stream_id": stream_id}),
        ((), {"base64": content, "stream_id": stream_id}),
        ((), {"content": content, "stream_id": stream_id}),
        ((content,), {}),
    ):
        try:
            sent = await _maybe_await(image_method(*args, **kwargs))
            return bool(sent)
        except TypeError:
            continue
    return False


# async def _try_send_audio(
#     ctx: Any,
#     file_location: str,
#     stream_id: Optional[str] = None,
# ) -> None:
#     sender = _get_path(ctx, "send")
#     audio = getattr(sender, "audio", None) or getattr(sender, "voice", None)
#     if callable(audio):
#         target_stream_id = stream_id or _get_stream_id(ctx)
#         try:
#             await _maybe_await(audio(file_location, stream_id=target_stream_id))
#         except TypeError:
#             await _maybe_await(audio(file_location))


async def _resolve_target_stream_id(ctx: Any, chat_type: str, target_id: str) -> Optional[str]:
    chat = _get_path(ctx, "chat")
    if chat is None:
        _log(ctx, "error", "定时发送财报失败: ctx.chat 不可用")
        return None

    method_names = (
        ("get_stream_by_group_id", "get_group_stream_by_group_id")
        if chat_type == "group"
        else ("get_stream_by_user_id", "get_private_stream_by_user_id")
    )
    for method_name in method_names:
        method = getattr(chat, method_name, None)
        if callable(method):
            try:
                stream = await _maybe_await(method(target_id, platform="qq"))
            except TypeError:
                try:
                    stream = await _maybe_await(method(target_id))
                except Exception as exc:
                    _log(ctx, "warning", f"解析定时财报目标失败: {method_name}({target_id}) 返回异常: {exc}")
                    continue
            except Exception as exc:
                _log(ctx, "warning", f"解析定时财报目标失败: {method_name}({target_id}) 返回异常: {exc}")
                continue

            stream_id = _stream_id_from_result(stream)
            if stream_id:
                return stream_id

    open_session = getattr(chat, "open_session", None)
    if callable(open_session):
        kwargs = {"platform": "qq", "chat_type": chat_type}
        if chat_type == "group":
            kwargs["group_id"] = target_id
        else:
            kwargs["user_id"] = target_id
        try:
            stream = await _maybe_await(open_session(**kwargs))
            stream_id = _stream_id_from_result(stream)
            if stream_id:
                return stream_id
        except Exception as exc:
            _log(ctx, "warning", f"打开定时财报目标会话失败: {chat_type} {target_id}: {exc}")

    return None


def _can_query(
    config: ExpensesSummaryConfig,
    ctx: Any = None,
    message: Any = None,
    kwargs: Optional[dict[str, Any]] = None,
) -> bool:
    if not config.permission.query_admin_only:
        return True
    return _is_admin(config, ctx, message, kwargs)


def _is_admin(
    config: ExpensesSummaryConfig,
    ctx: Any = None,
    message: Any = None,
    kwargs: Optional[dict[str, Any]] = None,
) -> bool:
    user_id = _get_user_id(ctx, message, kwargs)
    admins = {str(item).strip() for item in (config.permission.admins or []) if str(item).strip()}
    return bool(user_id and user_id in admins)


def _get_user_id(
    ctx: Any = None,
    message: Any = None,
    kwargs: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    for obj in (message, kwargs, ctx):
        for path in (
            "user_id",
            "sender.user_id",
            "sender.id",
            "sender.qq",
            "sender_id",
            "from_user_id",
            "operator_id",
            "user_info.user_id",
            "user_info.qq",
            "message_info.user_id",
            "message_info.sender_id",
            "message_info.platform_user_id",
            "message_info.sender.user_id",
            "message_info.user_info.user_id",
            "event.user_id",
            "event.sender.user_id",
            "event.message.user_id",
            "event.message.sender.user_id",
            "event.message.message_info.user_id",
        ):
            value = _get_path(obj, path)
            if value:
                return str(value)
    return None


def _extract_mode_argument(message: Any, kwargs: dict[str, Any]) -> str:
    for key in ("mode", "arg", "args", "text", "content"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return _last_command_part(value)

    for path in ("matched_groups.mode", "groups.mode", "message.content", "content", "text", "raw_message"):
        value = _get_path(kwargs, path) or _get_path(message, path)
        if isinstance(value, str) and value.strip():
            return _last_command_part(value)

    return ""


def _last_command_part(text: str) -> str:
    parts = text.strip().split()
    if len(parts) <= 1:
        return "" if text.strip().startswith("/") else text.strip()
    return parts[-1]


def _is_valid_mode_text(mode: str) -> bool:
    normalized = (mode or "").strip().lower()
    return normalized in {
        "默认",
        "default",
        "normal",
        "麦晨风",
        "maichenfeng",
        "mai-chenfeng",
        "huchenfeng",
        "fun",
    }


def _normalize_mode(mode: str) -> str:
    normalized = (mode or "").strip().lower()
    if normalized in {"麦晨风", "maichenfeng", "mai-chenfeng", "huchenfeng", "fun"}:
        return REPORT_MODE_MAICHENFENG
    return REPORT_MODE_DEFAULT


def _seconds_until(time_text: str) -> float:
    now = datetime.now()
    try:
        hour, minute = [int(part) for part in time_text.split(":", 1)]
    except ValueError:
        hour, minute = 23, 30
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max((target - now).total_seconds(), 1)


def _iter_items(raw: Any) -> Iterable[Any]:
    raw = _unwrap_payload(raw)
    if raw is None:
        return []
    if isinstance(raw, dict):
        for key in ("models", "data", "items", "records", "result"):
            if isinstance(raw.get(key), list):
                return raw[key]
        return [dict({"name": key}, **value) if isinstance(value, dict) else {"name": key, "value": value}
                for key, value in raw.items()]
    if isinstance(raw, list):
        return raw
    return [raw]


def _extract_total(raw: Any, keys: tuple[str, ...]) -> float:
    series_total = _series_total(raw)
    if series_total:
        return series_total
    total = 0.0
    for item in _iter_items(raw):
        total += _pick_number(item, *keys)
    return total


def _series_total(raw: Any, today_only: bool = False) -> float:
    raw = _unwrap_payload(raw)
    if raw is None:
        return 0.0
    timestamps = _pick(raw, "timestamps", "time_labels", "labels")
    direct_total = _pick_number(raw, "total")
    if direct_total and not today_only:
        return direct_total
    values_by_key = _pick(raw, "values_by_key", "series", "data_by_key")
    if isinstance(values_by_key, dict):
        return sum(
            _sum_numeric_sequence(values, timestamps=timestamps, today_only=today_only)
            for values in values_by_key.values()
        )
    values = _pick(raw, "values", "data")
    if isinstance(values, (list, tuple)):
        return _sum_numeric_sequence(values, timestamps=timestamps, today_only=today_only)
    return 0.0


def _series_values_by_label(raw: Any, today_only: bool = False) -> dict[str, float]:
    raw = _unwrap_payload(raw)
    values_by_key = _pick(raw, "values_by_key", "series", "data_by_key")
    if not isinstance(values_by_key, dict):
        return {}
    labels_by_key = _pick(raw, "labels_by_key", "label_by_key", "names_by_key") or {}
    timestamps = _pick(raw, "timestamps", "time_labels", "labels")
    result: dict[str, float] = {}
    for key, values in values_by_key.items():
        label = str(
            labels_by_key.get(key)
            if isinstance(labels_by_key, dict) and labels_by_key.get(key)
            else key
        )
        result[label] = _sum_numeric_sequence(values, timestamps=timestamps, today_only=today_only)
    return result


def _sum_numeric_sequence(
    values: Any,
    timestamps: Any = None,
    today_only: bool = False,
) -> float:
    if isinstance(values, dict):
        total = 0.0
        for timestamp, item in values.items():
            if today_only and not _is_today_timestamp(timestamp):
                continue
            if isinstance(item, (int, float)):
                total += float(item)
            else:
                total += _pick_number(item, "value", "count", "total", "cost")
        return total
    if isinstance(values, (list, tuple)):
        total = 0.0
        timestamp_list = timestamps if isinstance(timestamps, (list, tuple)) else None
        if today_only and timestamp_list is None:
            return 0.0
        for index, item in enumerate(values):
            if today_only and timestamp_list is not None and not _is_today_timestamp(timestamp_list[index] if index < len(timestamp_list) else None):
                continue
            if isinstance(item, (int, float)):
                total += float(item)
            else:
                total += _pick_number(item, "value", "count", "total", "cost")
        return total
    if today_only and timestamps is not None and not _is_today_timestamp(timestamps):
        return 0.0
    try:
        return float(values or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_today_timestamp(value: Any) -> bool:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return False
    return parsed.date() == date.today()


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _pick(item: Any, *keys: str, default: Any = None) -> Any:
    item = _unwrap_payload(item)
    for key in keys:
        if isinstance(item, dict) and key in item:
            return item[key]
        if hasattr(item, key):
            return getattr(item, key)
    return default


def _unwrap_payload(raw: Any) -> Any:
    current = raw
    seen = 0
    while isinstance(current, dict) and seen < 4:
        seen += 1
        if any(key in current for key in (
            "values_by_key",
            "items",
            "models",
            "records",
            "timestamps",
            "total",
        )):
            return current
        for key in ("data", "result", "payload"):
            value = current.get(key)
            if isinstance(value, (dict, list)):
                current = value
                break
        else:
            return current
    return current


def _pick_number(item: Any, *keys: str) -> float:
    value = _pick(item, *keys, default=0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _call_first(target: Any, names: list[str], *args: Any, **kwargs: Any) -> Any:
    for name in names:
        method = getattr(target, name, None)
        if callable(method):
            try:
                return method(*args, **kwargs)
            except TypeError:
                return method()
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _config_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int)):
        items = [value]
    else:
        try:
            items = list(value)
        except TypeError:
            items = [value]
    return [str(item).strip() for item in items if str(item).strip()]


def _stream_id_from_result(value: Any) -> Optional[str]:
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip()
    stream_id = _get_stream_id(value)
    if stream_id:
        return stream_id
    for path in (
        "data.stream_id",
        "result.stream_id",
        "payload.stream_id",
        "stream.stream_id",
        "chat_stream.stream_id",
    ):
        candidate = _get_path(value, path)
        if candidate:
            return str(candidate)
    return None


def _get_stream_id(obj: Any) -> Optional[str]:
    for path in (
        "stream_id",
        "session_id",
        "chat_id",
        "chat_stream.stream_id",
        "message.stream_id",
        "message.session_id",
        "message.chat_id",
        "message.chat_stream.stream_id",
        "message.message_info.stream_id",
        "message.message_info.session_id",
        "message.message_info.chat_id",
        "event.stream_id",
        "event.session_id",
        "event.chat_id",
        "event.chat_stream.stream_id",
        "event.message.stream_id",
        "event.message.session_id",
        "event.message.chat_id",
        "event.message.chat_stream.stream_id",
        "event.message.message_info.stream_id",
        "event.message.message_info.session_id",
        "event.message.message_info.chat_id",
        "context.stream_id",
        "context.session_id",
        "context.chat_id",
        "ctx.stream_id",
        "ctx.session_id",
        "ctx.chat_id",
    ):
        value = _get_path(obj, path)
        if value:
            return str(value)
    return None


def _get_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _log(ctx: Any, level: str, message: str) -> None:
    logger = getattr(ctx, "logger", None)
    method = getattr(logger, level, None)
    if callable(method):
        method(message)


def create_plugin() -> ExpensesSummaryPlugin:
    return ExpensesSummaryPlugin()
