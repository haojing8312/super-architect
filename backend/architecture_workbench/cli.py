from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


def main():
    if sys.argv[1:2] == ["native-geometry-worker"]:
        from .geometry import _worker
        raise SystemExit(_worker(sys.argv[2:]))
    if sys.argv[1:3] == ["-m", "architecture_workbench.stdio"]:
        from .stdio import main as stdio_main
        stdio_main()
        return
    parser = argparse.ArgumentParser(prog="architecture-workbench", description="超级建筑师")
    parser.add_argument("command", nargs="?", default="desktop", choices=["desktop", "serve", "mcp", "check", "init-skills", "import-legacy"])
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--port", type=int, default=33821)
    parser.add_argument("--source", default="")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "mcp":
        from .stdio import main as stdio_main
        stdio_main()
    elif arguments.command == "desktop":
        from .desktop import launch
        launch(arguments.data_dir, arguments.port)
    elif arguments.command == "serve":
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(arguments.data_dir), host="127.0.0.1", port=arguments.port, access_log=False)
    elif arguments.command == "check":
        from .runtime import cli_command
        from .store import Store
        store = Store(arguments.data_dir)
        result = {"data_dir": str(store.data_dir), "version": __import__("architecture_workbench").__version__, "runtimes": {}}
        import subprocess
        for provider in ("codex", "codebuddy"):
            try:
                command = cli_command(provider)
                output = subprocess.run([*command,"--version"],capture_output=True,timeout=10,encoding="utf-8",errors="replace",creationflags=0x08000000 if sys.platform=="win32" else 0)
                result["runtimes"][provider] = {"installed": output.returncode==0, "version": output.stdout.strip()}
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                result["runtimes"][provider] = {"installed": False, "error": str(error)}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    elif arguments.command == "init-skills":
        from .store import Store
        from .skills import SkillCatalog
        source=Path(arguments.source)
        if not source.is_dir():
            parser.error("--source 需要提供技能包目录")
        catalog=SkillCatalog(Store(arguments.data_dir))
        output=[]
        for child in sorted(source.iterdir()):
            if child.is_dir() and (child/"SKILL.md").is_file():
                try:
                    output.append(catalog.import_skill(str(child)))
                except ValueError as error:
                    output.append({"id":child.name,"error":str(error)})
        print(json.dumps(output,ensure_ascii=False,indent=2))
    elif arguments.command == "import-legacy":
        from .migration import import_legacy
        from .store import Store
        if not arguments.source:
            parser.error("import-legacy 需要 --source 指向旧版本数据目录")
        store = Store(arguments.data_dir)
        print(json.dumps(import_legacy(arguments.source, store.data_dir / "imports",
                                      dry_run=arguments.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
