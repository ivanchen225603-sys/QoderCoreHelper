#!/usr/bin/env python
# image_check.py —— 发布物检查：最终镜像不得以 root 运行。
# 通过探针取值（最后一个 USER 指令），避免多阶段构建的假象。
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pio  # noqa: F401 —— 导入即把控制台切到 UTF-8，防 GBK 终端崩溃
PROJECT = os.path.abspath(os.path.join(HERE, "..", ".."))


def main():
    dockerfile = os.path.join(PROJECT, "Dockerfile")
    if not os.path.exists(dockerfile):
        print("Dockerfile 不存在：发布准备环节必须先产出 Dockerfile")
        return 2
    r = subprocess.run([sys.executable, os.path.join(HERE, "probes.py"),
                        "image-user", "--file", dockerfile],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip())
        return 3
    import json
    non_root = json.loads(r.stdout)["value"]
    if not non_root:
        print("镜像以 root 运行：违反发布基线。在 Dockerfile 末尾加 USER 指令。")
        return 2
    print("镜像以非 root 用户运行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
