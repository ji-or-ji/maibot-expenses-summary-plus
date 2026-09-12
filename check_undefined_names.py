"""静态检查：找出模块里「被使用但从未定义」的全局名字。

py_compile 只查语法，模块级的 NameError 要到运行时才炸（这次 _now_text 就是这么漏的）。
这个脚本用 AST 把所有被引用的名字和所有绑定过的名字对比，把漏网的挑出来。
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

TARGET = Path(sys.argv[1] if len(sys.argv) > 1 else "plugin.py")

source = TARGET.read_text(encoding="utf-8")
tree = ast.parse(source)

defined: set[str] = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__builtins__"}
used: dict[str, list[int]] = {}

for node in ast.walk(tree):
    # 绑定：函数/类定义
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defined.add(node.name)
    # 绑定：赋值 / 解包 / 导入 / 参数 / with as / except as / 推导式 / 全局声明
    elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        defined.add(node.id)
    elif isinstance(node, ast.arg):
        defined.add(node.arg)
    elif isinstance(node, ast.alias):
        defined.add((node.asname or node.name).split(".")[0])
    elif isinstance(node, ast.Global):
        defined.update(node.names)
    elif isinstance(node, ast.ExceptHandler) and node.name:
        defined.add(node.name)
    elif isinstance(node, (ast.comprehension,)):
        for sub in ast.walk(node.target):
            if isinstance(sub, ast.Name):
                defined.add(sub.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for sub in ast.walk(node):
            if isinstance(sub, ast.alias):
                defined.add((sub.asname or sub.name).split(".")[0])
    # 使用
    elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        used.setdefault(node.id, []).append(node.lineno)

# 属性访问不算全局名，ast.Name 只覆盖裸名字
missing = sorted(name for name in used if name not in defined)

if missing:
    print(f"[FAIL] {TARGET} 存在未定义的名字：")
    for name in missing:
        lines = sorted(set(used[name]))
        print(f"  - {name}  行 {lines[:8]}")
    sys.exit(1)

print(f"[OK] {TARGET} 未发现未定义的全局名字（已扫描 {len(used)} 个引用）")
