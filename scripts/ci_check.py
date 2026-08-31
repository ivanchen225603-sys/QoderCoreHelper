#!/usr/bin/env python
# ci_check.py —— 发布物检查：CI 定义存在且权限范围显式声明。
#
# 为什么查权限声明：CI 的权限范围与凭据引用方式属于生成待批项，
# 必须显式存在才能被人在关卡上看到；缺了它等于权限在暗处生效。
# 不引入 yaml 依赖：只做结构性存在检查，深度校验留给 CI 平台。
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pio  # noqa: F401 —— 导入即把控制台切到 UTF-8，防 GBK 终端崩溃
PROJECT = os.path.abspath(os.path.join(HERE, "..", ".."))


def main():
    ci = os.path.join(PROJECT, "ci", "ci.yml")
    if not os.path.exists(ci):
        print("ci/ci.yml 不存在：发布准备环节必须先产出 CI 定义")
        return 2
    with open(ci, "r", encoding="utf-8") as f:
        raw = f.read()
    problems = []
    if len(raw.strip()) < 20:
        problems.append("CI 定义内容过短，疑似占位未填")
    if "permissions" not in raw:
        problems.append("未声明 permissions：CI 权限范围是生成待批项，"
                        "必须显式写出（哪怕是最小权限）")
    if "secret" in raw.lower() and "${{" not in raw and "${" not in raw:
        problems.append("疑似硬编码凭据：凭据只允许占位符与注入机制")
    if problems:
        for p in problems:
            print("- " + p)
        return 2
    print("ci/ci.yml 结构检查通过（存在、非空、显式权限声明）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
