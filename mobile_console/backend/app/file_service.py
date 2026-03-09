from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


class FileService:
    @staticmethod
    def _resolve(repo_path: Path, user_path: str) -> Path:
        target = (repo_path / user_path).resolve()
        if not str(target).startswith(str(repo_path.resolve())):
            raise ValueError("Path escapes repo root")
        return target

    @classmethod
    def read_file(cls, repo_path: Path, user_path: str) -> Dict[str, Any]:
        p = cls._resolve(repo_path, user_path)
        if not p.is_file():
            raise FileNotFoundError(user_path)
        return {"path": user_path, "content": p.read_text(encoding="utf-8", errors="replace")}

    @classmethod
    def write_file(cls, repo_path: Path, user_path: str, content: str) -> Dict[str, Any]:
        p = cls._resolve(repo_path, user_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"path": user_path, "bytes": len(content.encode("utf-8"))}

    @classmethod
    def tree(cls, repo_path: Path, rel: str = ".", depth: int = 2) -> List[Dict[str, Any]]:
        start = cls._resolve(repo_path, rel)
        if not start.exists():
            return []

        def walk(node: Path, d: int) -> List[Dict[str, Any]]:
            entries: List[Dict[str, Any]] = []
            if d < 0:
                return entries
            for child in sorted(node.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                if child.name.startswith(".git"):
                    continue
                item = {
                    "name": child.name,
                    "path": str(child.relative_to(repo_path)),
                    "type": "file" if child.is_file() else "dir",
                }
                if child.is_dir() and d > 0:
                    item["children"] = walk(child, d - 1)
                entries.append(item)
            return entries

        if start.is_file():
            return [{"name": start.name, "path": str(start.relative_to(repo_path)), "type": "file"}]
        return walk(start, depth)
