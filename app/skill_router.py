"""从用户意图选择 system_prompts_leaks 改造后的技能。"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple


_ROOT = Path(__file__).resolve().parent.parent
_SKILLS = _ROOT / "prompts" / "skills"


# (skill_file_stem, keywords)
_RULES: List[Tuple[str, List[str]]] = [
    (
        "plan",
        [
            r"先计划",
            r"先做计划",
            r"做个方案",
            r"给出方案",
            r"设计方案",
            r"plan\b",
            r"不要改代码",
            r"先别改",
            r"只分析",
            r"架构设计",
            r"方案设计",
            r"需求分析",
        ],
    ),
    (
        "security-review",
        [
            r"安全审查",
            r"安全审计",
            r"security review",
            r"安全漏洞",
            r"漏洞扫描",
            r"渗透测试",
            r"sql注入",
            r"xss",
            r"命令注入",
            r"路径穿越",
        ],
    ),
    (
        "code-review",
        [
            r"代码审查",
            r"审查",
            r"review",
            r"审计",
            r"找\s*bug",
            r"有没有问题",
            r"看看这改动",
            r"pr\s*review",
        ],
    ),
    (
        "debug",
        [
            r"调试",
            r"debug",
            r"报错",
            r"报\s*error",
            r"不工作",
            r"失败",
            r"traceback",
            r"exception",
            r"崩溃",
            r"挂了",
            r"为什么.*错",
        ],
    ),
    (
        "research",
        [
            r"研究",
            r"调研",
            r"research",
            r"深入分析",
            r"调查报告",
            r"验证.*论点",
            r"事实核查",
        ],
    ),
    (
        "simplify",
        [
            r"简化",
            r"优化代码",
            r"重构",
            r"cleanup",
            r"消除重复",
            r"代码质量",
            r"性能优化",
        ],
    ),
    (
        "verify",
        [
            r"验证",
            r"verify",
            r"确认.*有效",
            r"跑通",
            r"测一下",
            r"是否修好",
            r"回归",
        ],
    ),
    (
        "implement",
        [
            r"实现",
            r"开发",
            r"修复",
            r"修改",
            r"改一下",
            r"加上",
            r"新增",
            r"写一个",
            r"封装",
            r"fix",
            r"implement",
        ],
    ),
]


@lru_cache(maxsize=16)
def _load_skill(stem: str) -> str:
    path = _SKILLS / f"{stem}.md"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def detect_skills(goal: str, *, max_skills: int = 2) -> List[str]:
    text = (goal or "").strip().lower()
    if not text:
        return ["implement"]

    hit: List[str] = []
    for stem, patterns in _RULES:
        for pat in patterns:
            if re.search(pat, text, flags=re.IGNORECASE):
                if stem not in hit:
                    hit.append(stem)
                break
        if len(hit) >= max_skills:
            break

    if not hit:
        # 默认实现型，避免空技能
        hit = ["implement"]
    return hit[:max_skills]


def build_skill_block(goal: str) -> Tuple[str, List[str]]:
    skills = detect_skills(goal)
    parts: List[str] = []
    for s in skills:
        body = _load_skill(s)
        if body:
            parts.append(body)
    if not parts:
        return "", skills
    header = f"## 激活技能: {', '.join(skills)}"
    return header + "\n\n" + "\n\n---\n\n".join(parts), skills
