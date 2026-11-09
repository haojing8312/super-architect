"""The actual stdio process must serve tools and close without corrupting handles."""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from architecture_workbench.store import Store


def test_stdio_real_protocol_and_clean_exit(tmp_path):
    store = Store(tmp_path)
    session = store.create_session("stdio process")
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "architecture_workbench.stdio"],
        env={**os.environ, "ARCHITECTURE_DATA_DIR": str(tmp_path), "ARCHITECTURE_SESSION_ID": session["id"]})
    # Complete each request before closing stdin; premature EOF legitimately
    # cancels outstanding server work, so pipelining then EOF is not a client.
    error_log = tmp_path / "stdio.stderr.log"
    async def validate(error_stream):
        async with stdio_client(parameters, errlog=error_stream) as streams:
            async with ClientSession(*streams) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "architecture-workbench"
                assert "file_write" in {item.name for item in (await client.list_tools()).tools}
    with error_log.open("w", encoding="utf-8") as error_stream:
        asyncio.run(asyncio.wait_for(validate(error_stream), timeout=30))
    assert error_log.read_text(encoding="utf-8") == ""
