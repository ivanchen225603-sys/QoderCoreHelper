#!/usr/bin/env python
# probes.py —— 取值探针：把各种格式的量归一成可比的值。
#
# 为什么必须独立成层（关键设计点）：覆盖率有 coverage.py / lcov /
# cobertura / istanbul 四五种格式，如果每个门禁检查自己解析，格式差异
# 就会污染门禁逻辑——同一个覆盖率在不同工具下被判成不同结果。探针把
# "取值"和"比阈值"分开，门禁只认归一后的数字。
#
# 探针：
#   coverage             → 0-100 的浮点数（支持 coverage.py JSON / lcov /
#                          cobertura XML / istanbul(c8) JSON / 纯数字）
#   image-user           → Dockerfile 最终 USER，是否非 root
#   migration-rollback   → 迁移文件是否可回滚（有无 down/rollback 段）
#
# 输出统一为 {"metric","value","unit","format","source"}。
# 解析失败退出码 3（配置/工具问题），绝不吐 0 冒充成功。
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pio


def _emit(metric, value, unit, fmt, source, note=None):
    payload = {"metric": metric, "value": value, "unit": unit,
               "format": fmt, "source": source}
    if note:
        payload["note"] = note
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def probe_coverage(path):
    if not os.path.exists(path):
        pio.die(layout.EXIT_CONFIG, "覆盖率文件不存在: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read().strip()

    # 1) JSON 家族：coverage.py 与 istanbul/c8
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            pio.die(layout.EXIT_CONFIG, "JSON 解析失败: %s" % path)
        if "totals" in data and "percent_covered" in data["totals"]:
            return _emit("coverage", float(data["totals"]["percent_covered"]),
                         "percent", "coverage.py-json", path)
        total = data.get("total", {})
        for key in ("lines", "statements", "branches", "functions"):
            if key in total and "pct" in total[key]:
                return _emit("coverage", float(total[key]["pct"]),
                             "percent", "istanbul-json", path)
        pio.die(layout.EXIT_CONFIG,
                "JSON 里找不到已知覆盖率字段: %s" % path)

    # 2) cobertura XML：line-rate 是 0-1 比例
    if raw.startswith("<"):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            pio.die(layout.EXIT_CONFIG, "XML 解析失败 %s: %s" % (path, e))
        node = root if root.tag == "coverage" else root.find("coverage")
        if node is not None and "line-rate" in node.attrib:
            return _emit("coverage", float(node.attrib["line-rate"]) * 100.0,
                         "percent", "cobertura-xml", path)
        pio.die(layout.EXIT_CONFIG, "XML 不是 cobertura 格式: %s" % path)

    # 3) lcov：LF（总行数）与 LH（命中行数）累计
    lf = lh = 0
    if re.search(r"^LF:", raw, re.M):
        lf = sum(int(m) for m in re.findall(r"^LF:(\d+)", raw, re.M))
        lh = sum(int(m) for m in re.findall(r"^LH:(\d+)", raw, re.M))
        if lf == 0:
            pio.die(layout.EXIT_CONFIG, "lcov 文件 LF 全为 0: %s" % path)
        return _emit("coverage", lh / lf * 100.0, "percent", "lcov", path)

    # 4) 纯数字 / "87.3%"
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*%?$", raw)
    if m:
        return _emit("coverage", float(m.group(1)), "percent",
                     "plain-number", path)

    # 5) pytest/coverage 控制台文本里的 "TOTAL ... 87%"
    m = re.search(r"TOTAL\s+.*?\s([0-9]+(?:\.[0-9]+)?)%", raw)
    if m:
        return _emit("coverage", float(m.group(1)), "percent",
                     "console-text", path)

    pio.die(layout.EXIT_CONFIG,
            "无法识别的覆盖率格式（探针拒绝猜测）: %s" % path)


def probe_image_user(dockerfile):
    """取最终镜像的实际运行用户（阶段感知解析）。

    多阶段构建的陷阱：USER 是镜像元数据。新 FROM 一个基础镜像时，
    前一阶段的 USER 不继承（重置为镜像默认，即无声明= root）；
    FROM 前序阶段时才继承该阶段的用户。只看"全文最后一个 USER"
    会被"中间阶段声明过非 root"骗过——这正是回归测试钉死的缺陷。
    """
    if not os.path.exists(dockerfile):
        pio.die(layout.EXIT_CONFIG, "Dockerfile 不存在: %s" % dockerfile)
    stage_users = {}       # 阶段名 -> 该阶段的最终 USER（None=未声明）
    current = None         # 当前阶段的 USER；None = 未声明（按 root 计）
    stage_name = None
    with open(dockerfile, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = re.match(r"(?i)^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", s)
            if m:
                if stage_name is not None:
                    stage_users[stage_name.lower()] = current
                base, stage_name = m.group(1), m.group(2) or m.group(1)
                # FROM 前序阶段 → 继承其用户；否则重置为基础镜像默认（无声明）
                current = stage_users.get(base.lower())
                continue
            m = re.match(r"(?i)^USER\s+(\S+)", s)
            if m:
                current = m.group(1)
    if stage_name is not None:
        stage_users[stage_name.lower()] = current
    non_root = current is not None and current.split(":")[0] != "root" \
        and current != "0"
    # 没有 USER 指令 = 以 root 运行：门禁按不达标处理；警告写进 JSON，
    # 保持 stdout 纯 JSON（下游用 json.loads 直接吃）。
    _emit("image_user", non_root, "bool:non_root", "dockerfile-user",
          dockerfile,
          note=("最终阶段未声明 USER，按 root 运行计"
                if current is None else None))


def probe_migration_rollback(path):
    """迁移是否可回滚：存在 down/rollback/undo 段即为可回滚。

    纯 SQL 迁移没有 down 段的一律视为不可回滚——不可回滚迁移属于
    生成待批项，门禁呈现时必须单独列出。
    """
    if not os.path.exists(path):
        pio.die(layout.EXIT_CONFIG, "迁移文件不存在: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    markers = [r"(?im)^\s*def\s+down\s*\(", r"(?i)\brollback\b",
               r"(?im)^\s*--\s*\+migrate\s+Down",
               r"(?im)^\s*#?\s*down\s*:", r"(?i)\bdef\s+undo\s*\("]
    reversible = any(re.search(m, raw) for m in markers)
    _emit("migration_reversible", reversible, "bool", "marker-search", path)


def main():
    ap = argparse.ArgumentParser(description="取值探针")
    sub = ap.add_subparsers(dest="probe", required=True)
    p = sub.add_parser("coverage"); p.add_argument("--file", required=True)
    p = sub.add_parser("image-user"); p.add_argument("--file", required=True)
    p = sub.add_parser("migration-rollback")
    p.add_argument("--file", required=True)
    args = ap.parse_args()
    if args.probe == "coverage":
        probe_coverage(args.file)
    elif args.probe == "image-user":
        probe_image_user(args.file)
    else:
        probe_migration_rollback(args.file)


if __name__ == "__main__":
    main()
