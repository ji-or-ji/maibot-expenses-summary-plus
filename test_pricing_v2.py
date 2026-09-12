"""v2.0.0 价格引擎本地验证。

重点验证：
1. 价带归一化与校验（脏数据必须被丢弃）
2. 峰谷 / 时段价带选择（含时区换算）
3. 比对与阈值所用的「最大变化幅度」
4. 用 09-09 真实小时数据复算 SiliconFlow DeepSeek-V4-Flash 的成本
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plugin  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  [OK]   " if condition else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


print("=" * 72)
print("本地时区偏移（应为 +8 才能复现服务器口径）:", datetime.now().astimezone().utcoffset())
print("=" * 72)

print("\n[1] 价带归一化：脏 band 必须被丢弃")
bands = plugin._normalize_bands(
    [
        {"tz": "Asia/Shanghai", "hours": [[2, 8]], "cache_hit": 0.15, "cache_miss": 1.5, "output": 4.5},
        {"tz": "Asia/Shanghai", "hours": None, "cache_hit": 0.3, "cache_miss": 3.0, "output": 9.0},
        {"tz": "UTC", "hours": [[9, 5]], "cache_hit": 1, "cache_miss": 1, "output": 1},   # 非法区间
        {"tz": "UTC", "cache_hit": -1, "cache_miss": 1, "output": 1},                     # 负数
        {"tz": "UTC", "cache_hit": "abc", "cache_miss": 1, "output": 1},                  # 非数字
        {"note": "没有价格"},                                                             # 缺字段
    ]
)
check("有效 band 保留 2 条", len(bands) == 2, f"实际 {len(bands)}")
check(
    "价带排序：具体时段在前、兜底在后",
    bands[0]["hours"] == [[2, 8]] and bands[1]["hours"] is None,
)

print("\n[2] 峰谷价带选择（DeepSeek：UTC 周一至周五 01:00-04:00 / 06:00-10:00 为高峰）")
deepseek_entry = plugin._normalize_price_entry(
    {
        "currency": "USD",
        "bands": [
            {"tz": "UTC", "hours": [[1, 4], [6, 10]], "weekdays": [0, 1, 2, 3, 4], "cache_hit": 0.006, "cache_miss": 0.3, "output": 1.2},
            {"tz": "UTC", "hours": None, "cache_hit": 0.003, "cache_miss": 0.15, "output": 0.6},
        ],
    }
)
# 本地 09:00 (周三) -> UTC 01:00 -> 高峰
peak_band = plugin._select_band(deepseek_entry["bands"], datetime(2026, 9, 9, 9, 0), 0)
# 本地 15:00 (周三) -> UTC 07:00 -> 高峰
peak_band2 = plugin._select_band(deepseek_entry["bands"], datetime(2026, 9, 9, 15, 0), 0)
# 本地 20:00 (周三) -> UTC 12:00 -> 低谷
off_band = plugin._select_band(deepseek_entry["bands"], datetime(2026, 9, 9, 20, 0), 0)
# 本地 09:00 周日 -> UTC 01:00 周日 -> 非工作日 -> 低谷
weekend_band = plugin._select_band(deepseek_entry["bands"], datetime(2026, 9, 13, 9, 0), 0)
check("本地09:00 -> 高峰价", peak_band and peak_band["output"] == 1.2)
check("本地15:00 -> 高峰价", peak_band2 and peak_band2["output"] == 1.2)
check("本地20:00 -> 低谷价", off_band and off_band["output"] == 0.6)
check("周日同一时刻 -> 低谷价", weekend_band and weekend_band["output"] == 0.6)

print("\n[3] 价格比对：最大变化幅度与结构性变化")
old_models = {"A||m": {"currency": "CNY", "bands": [{"tz": "UTC", "hours": None, "weekdays": None, "tier": 0, "cache_hit": 0.3, "cache_miss": 3.0, "output": 9.0}]}}
new_models = {
    "A||m": {"currency": "CNY", "bands": [{"tz": "UTC", "hours": None, "weekdays": None, "tier": 0, "cache_hit": 0.3, "cache_miss": 3.6, "output": 9.0}]},
    "B||n": {"currency": "CNY", "bands": [{"tz": "UTC", "hours": None, "weekdays": None, "tier": 0, "cache_hit": 0, "cache_miss": 1, "output": 1}]},
}
percent, structural, changes = plugin._compare_price_tables(old_models, new_models)
check("最大变化幅度 = 20%", abs(percent - 20.0) < 0.01, f"实际 {percent:.2f}")
check("检出 1 条结构性新增", len(structural) == 1, str(structural))
check("检出 1 条数值变化", len(changes) == 1, str(changes))

print("\n[4] 输入长度分档：SQL 表达式生成")
tier_sql, tier_params = plugin._build_tier_expression(
    {"SiliconFlow||Qwen/Qwen3.5-35B-A3B": {"tiers": [128000], "bands": [{"cache_hit": 0, "cache_miss": 0.4, "output": 3.2}]}}
)
check("生成 CASE 表达式", "CASE" in tier_sql and "prompt_tokens >= ?" in tier_sql, tier_sql)
check("档位参数按降序绑定", tier_params == ["Qwen/Qwen3.5-35B-A3B", 128000], str(tier_params))
empty_sql, _ = plugin._build_tier_expression({})
check("无分档时退化为常量", empty_sql == "0 AS tier", empty_sql)

print("\n[5] 端到端复算 09-09 SiliconFlow DeepSeek-V4-Flash（期望 ≈ ¥6.5837）")

ROWS = [
    ("2026-09-09 01:00:00", 0, 29322, 3641),
    ("2026-09-09 06:00:00", 0, 721, 92),
    ("2026-09-09 07:00:00", 0, 61399, 6638),
    ("2026-09-09 08:00:00", 0, 14046, 1308),
    ("2026-09-09 11:00:00", 256, 160828, 12924),
    ("2026-09-09 12:00:00", 256, 87947, 19016),
    ("2026-09-09 13:00:00", 0, 22655, 2131),
    ("2026-09-09 14:00:00", 0, 22987, 3328),
    ("2026-09-09 15:00:00", 0, 16910, 1602),
    ("2026-09-09 16:00:00", 0, 743, 138),
    ("2026-09-09 17:00:00", 0, 16416, 3429),
    ("2026-09-09 18:00:00", 256, 298528, 64348),
    ("2026-09-09 19:00:00", 0, 251440, 56668),
    ("2026-09-09 20:00:00", 512, 337903, 69622),
    ("2026-09-09 21:00:00", 3328, 85390, 13201),
    ("2026-09-09 22:00:00", 0, 29524, 6483),
    ("2026-09-09 23:00:00", 0, 2474, 771),
]

temp_dir = Path(tempfile.mkdtemp())
db_path = temp_dir / "MaiBot.db"
connection = sqlite3.connect(db_path)
connection.execute(
    """
    CREATE TABLE llm_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_name TEXT NOT NULL,
        model_assign_name TEXT,
        model_api_provider_name TEXT NOT NULL,
        timestamp DATETIME,
        prompt_tokens INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL,
        prompt_cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
        prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
        cost FLOAT NOT NULL
    )
    """
)
for stamp, hit, miss, out in ROWS:
    native = hit / 1e6 * 0.02 + miss / 1e6 * 1.0 + out / 1e6 * 2.0
    connection.execute(
        "INSERT INTO llm_usage (model_name, model_assign_name, model_api_provider_name, timestamp, prompt_tokens, "
        "completion_tokens, prompt_cache_hit_tokens, prompt_cache_miss_tokens, cost) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "deepseek-ai/DeepSeek-V4-Flash",
            "deepseek-v4-flash-sf",
            "SiliconFlow",
            stamp,
            hit + miss,
            out,
            hit,
            miss,
            native,
        ),
    )
connection.commit()
connection.close()

models = {
    "SiliconFlow||deepseek-ai/DeepSeek-V4-Flash": plugin._normalize_price_entry(
        {
            "currency": "CNY",
            "bands": [
                {"tz": "Asia/Shanghai", "hours": [[2, 8]], "cache_hit": 0.15, "cache_miss": 1.5, "output": 4.5},
                {"tz": "Asia/Shanghai", "hours": None, "cache_hit": 0.30, "cache_miss": 3.0, "output": 9.0},
            ],
        }
    )
}

total, per_model, per_day, warnings = plugin._compute_cost_range(
    db_path, models, "2026-09-09 00:00:00", "2026-09-10 00:00:00"
)
print(f"    引擎算出: ¥{total:.4f}   按模型: {per_model}   警告: {warnings}")
check("总额 ≈ 6.5837", total is not None and abs(total - 6.5837) < 0.01, f"实际 {total:.4f}")
check("落到了 09-09 这一天", list(per_day.keys()) == ["2026-09-09"], str(per_day))
check("无兜底警告", not warnings, str(warnings))

native_total = sum(hit / 1e6 * 0.02 + miss / 1e6 * 1.0 + out / 1e6 * 2.0 for _s, hit, miss, out in ROWS)
print(f"    麦麦原生口径: ¥{native_total:.4f}   倍数: {total / native_total:.2f}x")
check("确实约 3.3 倍", 3.2 < total / native_total < 3.5)

print("\n[6] 兜底：模型没有牌价时必须退回原生 cost")
total2, _pm2, _pd2, warnings2 = plugin._compute_cost_range(
    db_path, {}, "2026-09-09 00:00:00", "2026-09-10 00:00:00"
)
check("无牌价时总额 = 原生口径", total2 is not None and abs(total2 - native_total) < 0.01, f"实际 {total2:.4f}")
check("产生兜底警告", bool(warnings2), str(warnings2))

print("\n" + "=" * 72)
if FAILURES:
    print(f"失败 {len(FAILURES)} 项: {FAILURES}")
    sys.exit(1)
print("全部通过")
