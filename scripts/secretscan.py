#!/usr/bin/env python
# secretscan.py —— 内置密钥扫描兜底（永远可用的那层）。
#
# 为什么必须有内置兜底：不变量 13 要求密钥扫描在所有环境零豁免。
# 外部扫描器（gitleaks）不可用时，如果整项检查被"降级跳过"，
# 零豁免就被悄悄架空了——所以密钥扫描的降级模式是"换内置扫描器"，
# 不是"不扫"。内置扫描器精度低于专业工具，这个差距会在报告里明说。
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout
import pio

# 高置信度模式：宁可漏报弱模式，不可把误报刷成噪音（噪音会让真命中被忽略）
PATTERNS = [
    ("aws_access_key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)"
                                  r"[A-Z0-9]{16}\b")),
    ("aws_secret", re.compile(r"(?i)aws.{0,20}?['\"][0-9a-zA-Z/+=]{40}"
                              r"['\"]")),
    ("private_key_block", re.compile(
        r"-----BEGIN (RSA|OPENSSH|EC|DSA|PGP)? ?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("generic_api_key", re.compile(
        r"(?i)\b(api[_-]?key|apikey|secret[_-]?key|access[_-]?token"
        r"|auth[_-]?token)\b\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]")),
    ("password_assignment", re.compile(
        r"(?i)\bpassword\s*[:=]\s*['\"][^'\"\s]{6,}['\"]")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}"
                       r"\.[A-Za-z0-9_\-]{8,}\b")),
]

# 明确的占位符不算命中：流水线只允许生成占位符（第三档边界）
PLACEHOLDER = re.compile(
    r"(?i)(xxx+|your[_-]?|changeme|placeholder|example|<[^>]{2,}|"
    r"\$\{[^}]+\}|\{\{[^}]+\}\}|dummy|sample|redacted)")

SKIP_DIRS = {".git", ".pipeline", "node_modules", "__pycache__", ".venv",
             "venv", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache"}
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml",
            ".md", ".txt", ".toml", ".ini", ".cfg", ".env", ".sh", ".ps1",
            ".html", ".css", ".go", ".java", ".rb", ".tf", ".dockerfile"}
MAX_SIZE = 1_000_000


def scan_file(path, rel):
    findings = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return findings
    for i, line in enumerate(lines, 1):
        if PLACEHOLDER.search(line):
            continue
        for rule, rx in PATTERNS:
            if rx.search(line):
                findings.append({"rule": rule, "file": rel, "line": i,
                                 "snippet": line.strip()[:80]})
    return findings


def scan(project):
    findings = []
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            base = fn.lower()
            if ext not in TEXT_EXT and base not in ("dockerfile", ".env",
                                                    "makefile"):
                continue
            full = os.path.join(dirpath, fn)
            if os.path.getsize(full) > MAX_SIZE:
                continue
            rel = os.path.relpath(full, project).replace("\\", "/")
            findings.extend(scan_file(full, rel))
    return findings


def main():
    ap = argparse.ArgumentParser(description="内置密钥扫描")
    ap.add_argument("--project", default=".")
    args = ap.parse_args()
    project = os.path.abspath(args.project)
    findings = scan(project)
    result = {"scanner": "builtin-regex",
              "note": "内置兜底扫描器，精度低于 gitleaks；报告中会标明用的是哪层",
              "findings": findings, "count": len(findings)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(layout.EXIT_FAIL if findings else layout.EXIT_OK)


if __name__ == "__main__":
    main()
