"""账本修正量（v2.0.0）本地验证。

修正量语义：实际总额 = 账本估算/累计 + 修正量。
重点验证：设定 / 累加 / 清零、以及新投喂不能把修正量算重。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plugin  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  [OK]   " if condition else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


tmp = Path(tempfile.mkdtemp())
plugin._set_data_dir(tmp)

print("=" * 72)
print("数据目录:", tmp)
print("=" * 72)

# --- 基础累积 ---
plugin._record_today_cost(10.0)
plugin._record_donation(100.0, "甲")
plugin._record_donation(50.0, "乙")
donation, cost, net = plugin._ledger_summary()
print(f"\n[1] 基础\n    投喂={donation} 开销={cost} 净={net}")
check("投喂累计 150", donation == 150.0, str(donation))
check("开销 10（覆盖写入）", cost == 10.0, str(cost))
check("净收入 140", net == 140.0, str(net))

# --- 设定消耗修正 ---
plugin._set_ledger_adjustment("cost_adjustment", 90.0)
donation, cost, net = plugin._ledger_summary()
print(f"\n[2] 消耗修正 +90\n    投喂={donation} 开销={cost} 净={net}")
check("开销 = 10 + 90 = 100", cost == 100.0, str(cost))
check("投喂不受影响", donation == 150.0, str(donation))

# --- 设定捐赠修正 ---
plugin._set_ledger_adjustment("donation_adjustment", -30.0)
donation, cost, net = plugin._ledger_summary()
print(f"\n[3] 捐赠修正 -30\n    投喂={donation} 开销={cost} 净={net}")
check("投喂 = 150 - 30 = 120", donation == 120.0, str(donation))
check("净收入 = 120 - 100 = 20", net == 20.0, str(net))

# --- 累加 ---
plugin._set_ledger_adjustment("donation_adjustment", 10.0, add=True)
donation, _cost, _net = plugin._ledger_summary()
print(f"\n[4] 捐赠修正再 +10\n    投喂={donation}")
check("投喂 = 120 + 10 = 130", donation == 130.0, str(donation))

# --- 新投喂不应把修正量算重 ---
plugin._record_donation(20.0, "丙")
donation, cost, _net = plugin._ledger_summary()
print(f"\n[5] 新增投喂 20\n    投喂={donation} 开销={cost}")
check("投喂 = (150+20-30+10) = 150", donation == 150.0, str(donation))

# --- 当日成本更新 ---
plugin._record_today_cost(30.0)
_donation, cost, _net = plugin._ledger_summary()
print(f"\n[6] 当日成本改为 30\n    开销={cost}")
check("开销 = 30 + 90 = 120", cost == 120.0, str(cost))

# --- 估算口径用于对照 ---
est_donation, est_cost, _ = plugin._ledger_estimated_summary()
print(f"\n[7] 估算口径对照\n    估算投喂={est_donation} 估算开销={est_cost}")
check("估算投喂 = 170", est_donation == 170.0, str(est_donation))
check("估算开销 = 30", est_cost == 30.0, str(est_cost))

# --- 清零 ---
plugin._set_ledger_adjustment("cost_adjustment", None)
plugin._set_ledger_adjustment("donation_adjustment", None)
donation, cost, net = plugin._ledger_summary()
print(f"\n[8] 修正清零\n    投喂={donation} 开销={cost} 净={net}")
check("回到估算口径", (donation, cost) == (170.0, 30.0), f"{donation}/{cost}")

# --- 命令文本解析 ---
plugin._set_ledger_adjustment("cost_adjustment", 12.5)
print("\n[9] 参数解析")
cases = [
    ("/消耗修正 120.5", (120.5, False, None), 120.5),
    ("/消耗修正 +50", (50.0, True, None), 62.5),
    ("/消耗修正 -7.5", (-7.5, True, None), 55.0),
    ("/实际总消耗 清空", (None, False, None), 0.0),
    ("/消耗修正 abc", "error", None),
    ("/消耗修正", (None, False, None), 55.0),
]
for text, expected, _ in cases:
    class _Msg:
        content = text

    token = plugin._extract_command_token(_Msg(), {}, ["消耗修正", "实际总消耗", "costfix"])
    value, add, error = plugin._parse_adjustment(token)
    if expected == "error":
        ok = error is not None
        check(f"「{text}」报错", ok, f"error={error}")
    else:
        ok = (value, add, error) == expected
        check(f"「{text}」→ {expected}", ok, f"实际 {(value, add, error)}")

# 空参数的显示分支不应改动数值
class _Bare:
    content = "/消耗修正"


token = plugin._extract_command_token(_Bare(), {}, ["消耗修正", "actual"])
check("无参数时 token 为空", token == "", repr(token))
# 解析用例只做纯解析、没有落库，所以修正量应仍是前面设过的 12.5
check("无参数不改动修正量", float(plugin._ledger_manual().get("cost_adjustment") or 0) == 12.5)

plugin._set_data_dir(None)
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 72)
if FAILURES:
    print(f"失败 {len(FAILURES)} 项: {FAILURES}")
    sys.exit(1)
print("全部通过")
