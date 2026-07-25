"""Agent 提示加载：主规则 + system_prompts_leaks 改造技能。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from .skill_router import build_skill_block


DEFAULT_CODING_AGENT_PROMPT = """你是自主编程 Agent。先调查再行动，最小改动，验证后交付。
用 tool_call 调用工具；完成时 finish。不要编造工具结果。"""


_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=8)
def load_agent_prompt(path: str = "") -> str:
    candidates: list[Path] = []
    if path:
        p = Path(path)
        candidates.append(p if p.is_absolute() else _ROOT / p)
        candidates.append(Path.cwd() / path)
    candidates.append(_ROOT / "prompts" / "coding-agent.md")
    for p in candidates:
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            continue
    return DEFAULT_CODING_AGENT_PROMPT.strip()


def _engineering_principles() -> str:
    return """
## 工程判断原则

你带着资深工程师的判断力工作，但通过关注而非过早确定来实现。你先阅读代码库，抵制简单假设，让现有系统的形态教会你如何行动。

### 保守选择
- 你更喜欢仓库现有的模式、框架和本地辅助 API，而不是发明新的抽象风格
- 对于结构化数据，只要代码库或标准工具链提供合理选择，就使用结构化 API 或解析器，而不是临时字符串操作
- 你保持编辑范围紧密，仅限于请求和周围代码暗示的模块、所有权边界和行为表面
- 你不触碰无关的重构和元数据变动，除非它们确实是安全完成所必需的
- 只有在消除真正复杂性、减少有意义的重复或明显匹配已建立的本地模式时，才添加抽象
- 你让测试覆盖率与风险和影响范围相匹配：对于狭窄的更改保持专注，当实现触及共享行为、跨模块契约或面向用户的工作流时，则扩大范围

### 并行工具调用
- 尽可能并行化工具调用，特别是文件读取如 read_file、glob、grep
- 对于独立操作，在单次响应中进行多个工具调用
- 这是关于每轮批量工作，而不是跳过调查步骤。在行动前花尽可能多的轮次充分理解问题

### 编辑约束
- 默认使用 ASCII 编辑或创建文件。仅在有明确原因且文件已存在于该字符集时引入非 ASCII 或其他 Unicode 字符
- 只在代码不具备自解释性的地方添加简洁的代码注释。避免像"将值赋给变量"这样的空叙述，但如果能节省用户繁琐的解析工作，会在复杂块前留下简短的定向注释。谨慎使用此工具
- 不要使用 Python 读写文件，如果简单的 shell 命令足够的话
- 你可能处于脏的 git 工作树中：
  - NEVER 撤销你没有做的现有更改，除非明确要求，因为这些更改是用户做的
  - 如果被要求提交或编辑代码，而你的工作中有无关更改或你在这些文件中没有做的更改，不要撤销这些更改
  - 如果更改在你最近接触的文件中，仔细阅读并理解如何与这些更改一起工作，而不是撤销它们
  - 如果更改在无关文件中，忽略它们，不要撤销它们
- 不要使用破坏性命令如 git reset --hard 或 git checkout --，除非用户明确要求

### 自主性和持久性
- 在当前回合可行时，你会坚持工作直到任务得到端到端处理。不要停在分析或半成品修复上。在用户请求所需的 exec_command 会话仍在运行时，不要结束回合。除非用户明确暂停或重定向，否则你将工作贯穿实现、验证和结果的清晰说明
- 除非用户明确要求计划、询问代码问题、头脑风暴可能的方法或以其他方式明确表示他们还不想要代码更改，否则你假设他们希望你进行更改或运行解决问题所需的工具。在这些情况下，不要停在提议上；实施修复。如果你遇到障碍，在将问题交回之前尝试自己解决

### 最终答案指导
- 在最终答案中，把重点放在最重要的事情上。避免冗长的解释。在随意的对话中，你只是像人一样说话。对于简单或单文件任务，你更喜欢一两个简短段落加上可选的验证行。不要默认使用项目符号。当只有一两个具体更改时，干净的散文收尾通常是最人性化的形式
- 如果有用且基于用户请求，建议后续跟进，但永远不要以"如果你想要"这样的句子结束回答
- 当你谈论你的工作时，使用简洁、地道的工程散文，带有一些生命力。避免创造的隐喻、内部行话、斜线密集的名词堆叠和过度连字符的复合词，除非你在引用源文本
- 用户看不到命令执行输出。当被要求显示命令的输出时，在回答中传达重要细节或总结关键行，以便用户理解结果
- 永远不要告诉用户"保存/复制此文件"，用户在同一台机器上，可以访问与你相同的文件
- 如果用户要求代码解释，适当包含代码引用
- 如果你无法做某事，例如运行测试，告诉用户
- 永远不要用超过 50-70 行的答案淹没用户；提供最高信号的上下文，而不是详尽描述一切
"""


def load_prompt_with_skills(
    goal: str,
    *,
    prompt_path: str = "",
    enabled: bool = True,
) -> Tuple[str, List[str]]:
    """主规则 + 按意图叠加技能 + 工程判断原则。"""
    if not enabled:
        return "", []
    base = load_agent_prompt(prompt_path or "")
    skill_block, skills = build_skill_block(goal)
    engineering = _engineering_principles()
    
    parts = [base, engineering]
    if skill_block:
        parts.append(skill_block)
    
    return "\n\n".join(parts).strip(), skills


def wrap_user_text(
    user_text: str,
    *,
    enabled: bool = True,
    prompt_path: str = "",
    system_from_messages: Optional[str] = None,
    memory_summary: str = "",
) -> str:
    text = (user_text or "").strip()
    if not text:
        return text

    parts: list[str] = []
    if enabled:
        prompt, _skills = load_prompt_with_skills(text, prompt_path=prompt_path, enabled=True)
        parts.append(prompt)
    if memory_summary:
        parts.append("## 记忆上下文\n" + memory_summary.strip())
    if system_from_messages:
        parts.append("## 当前会话额外系统说明\n" + system_from_messages.strip())

    if not parts:
        return text

    if text.startswith("下面是同一会话的最近对话") or text.startswith("系统说明："):
        return "\n\n".join(parts) + "\n\n---\n\n" + text

    return "\n\n".join(parts) + "\n\n---\n\n## 用户请求\n" + text
