import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    key: str
    value: Any
    created_at: datetime
    accessed_at: datetime
    confidence: float = 1.0


class MemoryStore:
    def __init__(self, memory_dir: str = ".memory"):
        self.memory_dir = Path(memory_dir).resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, MemoryEntry] = {}
        self._load()

    def _load(self):
        index_file = self.memory_dir / "index.json"
        if index_file.exists():
            with open(index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for entry in data.get("entries", []):
                    self._cache[entry["key"]] = MemoryEntry(
                        key=entry["key"],
                        value=entry["value"],
                        created_at=datetime.fromisoformat(entry["created_at"]),
                        accessed_at=datetime.fromisoformat(entry["accessed_at"]),
                        confidence=entry.get("confidence", 1.0),
                    )

    def _save(self):
        index_file = self.memory_dir / "index.json"
        entries = [
            {
                "key": e.key,
                "value": e.value,
                "created_at": e.created_at.isoformat(),
                "accessed_at": e.accessed_at.isoformat(),
                "confidence": e.confidence,
            }
            for e in self._cache.values()
        ]
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, indent=2)

    def set(self, key: str, value: Any, confidence: float = 1.0):
        now = datetime.now()
        self._cache[key] = MemoryEntry(
            key=key,
            value=value,
            created_at=now,
            accessed_at=now,
            confidence=confidence,
        )
        self._save()

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry:
            entry.accessed_at = datetime.now()
            self._save()
            return entry.value
        return None

    def delete(self, key: str):
        if key in self._cache:
            del self._cache[key]
            self._save()

    def search(self, query: str) -> List[Dict[str, Any]]:
        now = datetime.now()
        results = []
        query_lower = query.lower()
        for entry in self._cache.values():
            key_lower = entry.key.lower()
            if query_lower in key_lower:
                entry.accessed_at = now
                results.append({
                    "key": entry.key,
                    "value": entry.value,
                    "confidence": entry.confidence,
                    "created_at": entry.created_at.isoformat(),
                })
        self._save()
        return sorted(results, key=lambda x: x["confidence"], reverse=True)

    def cleanup(self, max_age_days: int = 30):
        cutoff = datetime.now() - timedelta(days=max_age_days)
        to_remove = [k for k, e in self._cache.items() if e.created_at < cutoff]
        for k in to_remove:
            del self._cache[k]
        self._save()
        return len(to_remove)

    def keys(self) -> List[str]:
        return list(self._cache.keys())


class ProjectMemory:
    def __init__(self, workspace: str, session_id: Optional[str] = None):
        self.workspace = Path(workspace).resolve()
        self.session_id = session_id or hashlib.md5(str(self.workspace).encode()).hexdigest()[:16]
        self.memory = MemoryStore(str(self.workspace / ".doubao" / "memory"))
        self._project_key = f"project:{self.session_id}"

    def record_project_context(self, context: Dict[str, Any]):
        key = f"{self._project_key}:context"
        existing = self.memory.get(key) or {}
        existing.update(context)
        self.memory.set(key, existing)

    def get_project_context(self) -> Dict[str, Any]:
        return self.memory.get(f"{self._project_key}:context") or {}

    def record_decision(self, decision: str, rationale: str, confidence: float = 1.0):
        key = f"{self._project_key}:decisions"
        decisions = self.memory.get(key) or []
        decisions.append({
            "decision": decision,
            "rationale": rationale,
            "confidence": confidence,
            "timestamp": datetime.now().isoformat(),
        })
        self.memory.set(key, decisions)

    def get_decisions(self) -> List[Dict[str, Any]]:
        return self.memory.get(f"{self._project_key}:decisions") or []

    def record_user_preference(self, key: str, value: Any):
        preference_key = f"{self._project_key}:preferences:{key}"
        self.memory.set(preference_key, value)

    def get_user_preference(self, key: str, default: Any = None) -> Any:
        preference_key = f"{self._project_key}:preferences:{key}"
        return self.memory.get(preference_key) or default

    def record_file_change(self, file_path: str, change_type: str, description: str):
        key = f"{self._project_key}:changes"
        changes = self.memory.get(key) or []
        changes.append({
            "file_path": file_path,
            "change_type": change_type,
            "description": description,
            "timestamp": datetime.now().isoformat(),
        })
        if len(changes) > 100:
            changes = changes[-50:]
        self.memory.set(key, changes)

    def get_recent_changes(self, limit: int = 10) -> List[Dict[str, Any]]:
        changes = self.memory.get(f"{self._project_key}:changes") or []
        return changes[-limit:]

    def build_context_summary(self) -> str:
        context = self.get_project_context()
        decisions = self.get_decisions()[-5:]
        preferences = {}
        for k in self.memory.keys():
            if k.startswith(f"{self._project_key}:preferences:"):
                pref_key = k.replace(f"{self._project_key}:preferences:", "")
                preferences[pref_key] = self.memory.get(k)

        parts = []
        if context:
            parts.append(f"## 项目上下文\n{json.dumps(context, ensure_ascii=False, indent=2)}")
        if decisions:
            parts.append("## 最近决策\n" + "\n".join(
                f"- {d['decision']} (置信度: {d['confidence']})"
                for d in decisions
            ))
        if preferences:
            parts.append(f"## 用户偏好\n{json.dumps(preferences, ensure_ascii=False, indent=2)}")

        return "\n\n".join(parts) if parts else ""

    def cleanup(self):
        self.memory.cleanup()
