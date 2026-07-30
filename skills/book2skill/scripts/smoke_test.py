#!/usr/bin/env python3
"""Book2Skill 部署后冒烟测试。

验证 ``book2skill`` CLI 可被发现、Analyze Only 模式可运行。脚本只用标准库，
不依赖项目虚拟环境内的包，便于在任何部署目标上执行。

流程：
1. 解析 ``book2skill`` 可执行文件（.venv 优先，PATH 回退，``python -m`` 兜底）；
2. 在临时目录创建一个最小 TXT 样本（无版权风险）；
3. 运行 ``book2skill analyze <sample> --json``；
4. 解析 stdout 的 AnalysisBundle JSON，断言 ``collection_id`` 存在且
   ``candidate_units`` 为列表；
5. 输出 PASS / FAIL 并返回对应退出码。

退出码：0 = 通过，1 = 失败。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: 仓库根目录（脚本位于 ``<repo>/skills/book2skill/scripts/smoke_test.py``）。
_REPO_ROOT = Path(__file__).resolve().parents[4]

#: 最小 TXT 样本内容（合成数据，无版权风险）。
_SAMPLE_TEXT = (
    "Principle 1: Compounding favours the patient over the popular.\n"
    "Case study: a 20-year holding period smooths variance.\n"
    "Term: moat — a durable competitive advantage.\n"
)


def resolve_cli() -> list[str]:
    """解析 ``book2skill`` CLI 调用命令。

    优先级：
    1. 仓库内 ``.venv/Scripts/book2skill.exe``（Windows）；
    2. 仓库内 ``.venv/bin/book2skill``（POSIX）；
    3. ``PATH`` 中的 ``book2skill``；
    4. ``python -m book2skill.cli`` 兜底。
    """
    candidates = [
        _REPO_ROOT / ".venv" / "Scripts" / "book2skill.exe",
        _REPO_ROOT / ".venv" / "bin" / "book2skill",
    ]
    for cand in candidates:
        if cand.exists():
            return [str(cand)]

    exe = shutil.which("book2skill")
    if exe:
        return [exe]

    return [sys.executable, "-m", "book2skill.cli"]


def run_smoke_test() -> int:
    """执行冒烟测试，返回退出码（0=通过，1=失败）。"""
    cli = resolve_cli()

    with tempfile.TemporaryDirectory(prefix="b2s-smoke-") as tmp:
        sample = Path(tmp) / "sample.txt"
        sample.write_text(_SAMPLE_TEXT, encoding="utf-8")

        cmd = [*cli, "analyze", str(sample), "--json"]
        print(f"[smoke] running: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        if proc.returncode != 0:
            print(f"[smoke] FAIL: analyze exited {proc.returncode}")
            if proc.stderr:
                print(f"[smoke] stderr:\n{proc.stderr}", file=sys.stderr)
            return 1

        try:
            bundle = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            print(f"[smoke] FAIL: stdout is not valid JSON: {exc}")
            print(f"[smoke] stdout:\n{proc.stdout}", file=sys.stderr)
            return 1

        collection_id = bundle.get("collection_id")
        if not collection_id:
            print("[smoke] FAIL: bundle has no 'collection_id'")
            return 1

        candidate_units = bundle.get("candidate_units")
        if not isinstance(candidate_units, list):
            print(
                "[smoke] FAIL: 'candidate_units' is not a list "
                f"(got {type(candidate_units).__name__})"
            )
            return 1

        print(f"[smoke] PASS: collection_id={collection_id}")
        print(f"[smoke]   candidate_units: {len(candidate_units)}")
        print(f"[smoke]   structure entries: {len(bundle.get('structure', []))}")
        return 0


def main() -> int:
    """入口：运行冒烟测试并返回退出码。"""
    try:
        return run_smoke_test()
    except FileNotFoundError as exc:
        print(f"[smoke] FAIL: book2skill CLI not found: {exc}", file=sys.stderr)
        print(
            "[smoke] hint: install via `pip install -e .` or `uv sync` "
            "in the Book2Skill repo",
            file=sys.stderr,
        )
        return 1
    except subprocess.TimeoutExpired:
        print("[smoke] FAIL: analyze timed out after 60s", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[smoke] FAIL: OS error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
