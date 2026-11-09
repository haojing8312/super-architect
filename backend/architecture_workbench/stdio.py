"""Session-scoped workbench MCP. stdout is reserved for the MCP protocol."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .interactions import InteractionStore
from .skills import RUNTIME_ENV, SkillCatalog, professional_python

from mcp.server.fastmcp import FastMCP

from .store import Store


def workspace_path(store: Store, session_id: str, supplied: str, *, allow_root: bool = False) -> Path:
    try:
        session = store.get_session(session_id)
    except KeyError as error:
        raise ValueError("当前会话不存在") from error
    if not session:
        raise ValueError("当前会话不存在")
    root = Path(session["workspace"]).expanduser().resolve()
    candidate = Path(supplied).expanduser()
    candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not candidate.is_relative_to(root) or (candidate == root and not allow_root):
        raise ValueError("文件操作必须在当前会话工作区内")
    return candidate


def create_mcp(store: Store | None = None, session_id: str | None = None):
    store = store or Store(os.environ.get("ARCHITECTURE_DATA_DIR"))
    session_id = session_id or os.environ.get("ARCHITECTURE_SESSION_ID", "")
    if not session_id or not store.get_session(session_id):
        raise ValueError("ARCHITECTURE_SESSION_ID 必须指定有效工作台会话")
    mcp = FastMCP("architecture-workbench", instructions="工作台业务数据以工具为准。文件读写仅允许当前会话工作区，专业软件用对应 MCP。", log_level="WARNING")
    interactions = InteractionStore(store)
    from .reminders import ReminderService, utc_now
    reminders = ReminderService(store)

    @mcp.tool()
    def time_now() -> dict:
        """返回当前 UTC 时间和工作台用户时区。"""
        from zoneinfo import ZoneInfo
        current = utc_now()
        zone = store.get_settings().get("timezone", "America/Phoenix")
        return {"utc": current.isoformat(), "local": current.astimezone(ZoneInfo(zone)).isoformat(), "timezone": zone}

    @mcp.tool()
    def reminder_create(title: str, due_at: str, timezone: str = "") -> dict:
        """用户要求提醒时保存一次提醒；客户端必须保持运行，时间可带UTC偏移。"""
        return reminders.create(title, due_at, timezone, session_id)

    @mcp.tool()
    def reminders_list() -> list:
        """读取当前会话相关提醒。"""
        return [item for item in reminders.list() if item.get("session_id") == session_id]

    @mcp.tool()
    def reminder_cancel(id: str) -> dict:
        """取消本会话的指定提醒。"""
        item = reminders.get(id)
        if item.get("session_id") != session_id:
            raise ValueError("只能取消当前会话的提醒")
        return reminders.dismiss(id)

    @mcp.tool()
    def session_get() -> dict:
        """读取当前会话的工作区和技能，不返回其他会话内容。"""
        return store.get_session(session_id)

    @mcp.tool()
    def present_options(question: str, options: list[str | dict], allow_other: bool = True) -> dict:
        """真正缺少信息时提出可持久恢复的选项问题。返回 pending 后停止依赖答案的任务，用户会在工作台回答。"""
        return interactions.present(session_id, question, options, allow_other)

    @mcp.tool()
    def questions_get() -> list:
        """读取当前会话问题与回答；已经回答的信息无需再次询问。"""
        return interactions.questions(session_id)

    @mcp.tool()
    def run_python_script(path: str, args: list[str] | None = None, timeout_seconds: int = 120) -> dict:
        """运行用户授权的工作区 Python 或本会话启用的技能脚本。cwd 为会话 workspace；入口/参数有路径守卫，但不是操作系统沙箱。"""
        session = store.get_session(session_id)
        root = Path(session["workspace"]).resolve()
        candidate = Path(path).expanduser()
        candidate = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
        selected, _, _ = SkillCatalog(store).for_session(session.get("skills", []))
        skill_roots = [Path(skill["path"]).resolve() for skill in selected]
        if candidate.suffix.lower() != ".py" or not candidate.is_file() or not any(candidate.is_relative_to(allowed) for allowed in [root, *skill_roots]):
            raise ValueError("只允许当前工作区或当前会话已启用技能内的 Python 脚本")
        args = args or []
        if len(args) > 100 or any(not isinstance(arg, str) or len(arg) > 4000 or "\x00" in arg for arg in args):
            raise ValueError("args 必须是最多 100 个无空字节的字符串")
        for arg in args:
            value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
            if value.startswith(("http://", "https://")):
                continue
            if Path(value).is_absolute() or "/" in value or "\\" in value or value == "..":
                parameter = Path(value)
                resolved = (parameter if parameter.is_absolute() else root / parameter).resolve()
                if not resolved.is_relative_to(root):
                    raise ValueError("脚本参数中的路径必须在当前工作区内")
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds 必须是 1 至 600 秒")
        command = [professional_python(store.get_settings()), str(candidate), *args]
        process = subprocess.Popen(command, cwd=root, env={**os.environ, **RUNTIME_ENV},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
            else:
                try:
                    import psutil
                    for child in psutil.Process(process.pid).children(recursive=True):
                        child.kill()
                except (ImportError, ProcessLookupError):
                    pass
                process.kill()
            stdout, stderr = process.communicate()
        return {"exit_code": process.returncode, "timed_out": timed_out,
                "stdout": stdout.decode("utf-8", errors="replace")[-100000:],
                "stderr": stderr.decode("utf-8", errors="replace")[-100000:],
                "workspace": str(root), "entry": str(candidate),
                "execution_boundary": "入口与参数路径限制，workspace cwd；不是操作系统沙箱"}

    @mcp.tool()
    def memory_list() -> list:
        """读取本地工作台长期记忆。"""
        return store.memories()

    @mcp.tool()
    def memory_save(content: str, category: str = "general") -> dict:
        """保存用户明确提供的长期事实，避免保存密钥和临时工具输出。"""
        if not content.strip() or len(content) > 20_000:
            raise ValueError("记忆内容不能为空或超过 20000 字符")
        return store.save_memory(content, category)

    @mcp.tool()
    def memory_delete(id: str) -> dict:
        """用户明确要求删除时移除指定长期记忆。"""
        store.delete_memory(id)
        return {"deleted": True}

    @mcp.tool()
    def knowledge_query(query: str, limit: int = 5, source_id: str | None = None) -> list:
        """检索已导入知识库，结果包含出处；无命中时不得编造内容。"""
        return store.knowledge_query(query, max(1, min(limit, 30)), source_id=source_id)

    @mcp.tool()
    def knowledge_list() -> list:
        """列出已导入的本地知识文档。"""
        return store.knowledge_items()

    @mcp.tool()
    def knowledge_import(path: str) -> dict:
        """将当前会话工作区内真实文件导入知识库，不修改原文件。"""
        return store.knowledge_import(str(workspace_path(store, session_id, path)))

    @mcp.tool()
    def todo_get() -> list:
        """读取当前会话待办。"""
        return store.get_todos(session_id)

    @mcp.tool()
    def todo_save(items: list[dict]) -> list:
        """保存当前会话任务步骤及状态，不把未执行步骤标为完成。"""
        if len(items) > 100:
            raise ValueError("待办最多 100 项")
        return store.save_todos(session_id, items)

    @mcp.tool()
    def canvas_get() -> dict:
        """读取当前会话画布的元素和视口。"""
        return store.get_canvas(session_id)

    @mcp.tool()
    def canvas_save(elements: list[dict], viewport: dict | None = None) -> dict:
        """保存当前会话画布元素；保留 element id 便于后续编辑。"""
        if len(elements) > 10_000:
            raise ValueError("画布元素最多 10000 项")
        return store.save_canvas(session_id, {"elements": elements, "viewport": viewport or {}})

    @mcp.tool()
    def resources_list() -> list:
        """列出本地素材目录中已索引的文件。"""
        return store.resources()

    @mcp.tool()
    def resource_import(path: str) -> dict:
        """将当前会话工作区内文件索引到素材库，不修改原文件。"""
        return store.import_resource(str(workspace_path(store, session_id, path)))

    @mcp.tool()
    def files_list(path: str = ".") -> list:
        """列出当前工作区指定目录的直接子项。"""
        directory = workspace_path(store, session_id, path, allow_root=True)
        if not directory.is_dir():
            raise ValueError("目录不存在")
        root = workspace_path(store, session_id, ".", allow_root=True)
        return [{"name": child.name, "path": str(child.relative_to(root)),
                 "directory": child.is_dir(), "size": child.stat().st_size if child.is_file() else 0}
                for child in sorted(directory.iterdir())[:1000]
                if child.resolve().is_relative_to(root) and not child.is_symlink()]

    @mcp.tool()
    def file_read(path: str) -> dict:
        """读取工作区 UTF-8 文本文件，最多 2 MB。"""
        target = workspace_path(store, session_id, path)
        if not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("文件不存在或超过 2 MB 文本读取限制")
        return {"path": str(target), "content": target.read_text(encoding="utf-8-sig")}

    @mcp.tool()
    def file_write(path: str, content: str, overwrite: bool = False) -> dict:
        """向当前会话工作区写 UTF-8 文本；覆盖文件必须显式 overwrite=true。"""
        if len(content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("写入内容超过 2 MB")
        target = workspace_path(store, session_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w" if overwrite else "x", encoding="utf-8") as handle:
            handle.write(content)
        return {"path": str(target), "bytes": target.stat().st_size}

    return mcp


def main():
    # MCP wraps and releases the binary standard streams. Give it owned copies:
    # the frozen Windows bootloader still flushes the original stdout on exit.
    original_input, original_output = sys.stdin, sys.stdout
    owned_input = os.fdopen(os.dup(original_input.fileno()), "r", encoding="utf-8", errors="replace")
    owned_output = os.fdopen(os.dup(original_output.fileno()), "w", encoding="utf-8")
    sys.stdin, sys.stdout = owned_input, owned_output
    try:
        create_mcp().run(transport="stdio")
    finally:
        sys.stdin, sys.stdout = original_input, original_output
        for stream in (owned_input, owned_output):
            if not stream.closed:
                stream.close()


if __name__ == "__main__":
    main()
