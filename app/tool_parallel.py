import asyncio
from typing import Any, Dict, List, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed


@dataclass
class ParallelToolCall:
    name: str
    arguments: Dict[str, Any]
    id: str = ""


@dataclass
class ParallelToolResult:
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    result: Any
    error: Optional[str] = None
    duration: float = 0.0


class ParallelToolExecutor:
    def __init__(self, runtime, max_workers: int = 4):
        self.runtime = runtime
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def execute_parallel(self, calls: List[ParallelToolCall]) -> List[ParallelToolResult]:
        results = []
        futures = {}

        for call in calls:
            future = self.executor.submit(
                self._execute_single,
                call.id,
                call.name,
                call.arguments,
            )
            futures[future] = call

        for future in as_completed(futures):
            call = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append(ParallelToolResult(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    result=None,
                    error=str(e),
                ))

        return sorted(results, key=lambda r: r.call_id)

    def _execute_single(self, call_id: str, name: str, arguments: Dict[str, Any]) -> ParallelToolResult:
        import time
        start = time.time()
        try:
            result = self.runtime.execute(name, arguments)
            duration = time.time() - start
            return ParallelToolResult(
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                result=result,
                duration=duration,
            )
        except Exception as e:
            duration = time.time() - start
            return ParallelToolResult(
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                result=None,
                error=str(e),
                duration=duration,
            )

    async def execute_parallel_async(self, calls: List[ParallelToolCall]) -> List[ParallelToolResult]:
        loop = asyncio.get_event_loop()
        futures = []

        for call in calls:
            future = loop.run_in_executor(
                self.executor,
                self._execute_single,
                call.id,
                call.name,
                call.arguments,
            )
            futures.append(future)

        results = await asyncio.gather(*futures)
        return sorted(results, key=lambda r: r.call_id)

    def shutdown(self):
        self.executor.shutdown(wait=True)


def build_parallel_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[ParallelToolCall]:
    calls = []
    for i, tc in enumerate(tool_calls):
        calls.append(ParallelToolCall(
            id=str(i),
            name=tc.get("name", ""),
            arguments=tc.get("arguments", {}),
        ))
    return calls


def format_parallel_results(results: List[ParallelToolResult]) -> str:
    lines = ["<parallel_results>"]
    for r in results:
        if r.error:
            lines.append(f"  <call id=\"{r.call_id}\" tool=\"{r.tool_name}\" status=\"error\">")
            lines.append(f"    <error>{r.error}</error>")
            lines.append("  </call>")
        else:
            lines.append(f"  <call id=\"{r.call_id}\" tool=\"{r.tool_name}\" duration=\"{r.duration:.2f}s\">")
            if isinstance(r.result, dict):
                if "content" in r.result:
                    lines.append(f"    <content>{r.result['content']}</content>")
                elif "stdout" in r.result:
                    lines.append(f"    <stdout>{r.result['stdout']}</stdout>")
                elif "files" in r.result:
                    lines.append(f"    <files>{r.result['files']}</files>")
                else:
                    import json
                    lines.append(f"    <result>{json.dumps(r.result, ensure_ascii=False)}</result>")
            else:
                lines.append(f"    <result>{r.result}</result>")
            lines.append("  </call>")
    lines.append("</parallel_results>")
    return "\n".join(lines)
