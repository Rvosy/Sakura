import sys
from pathlib import Path

dependency = Path(sys.argv[1])
sys.path[:0] = [str(dependency), str(dependency / "win32"), str(dependency / "win32/lib"), str(dependency / "pywin32_system32")]

import anyio
from mcp.server.mcpserver import MCPServer, Context
from mcp.server.mcpserver import Elicit, Resolve
from pydantic import BaseModel
from typing import Annotated
import os

server = MCPServer("Sakura fixture")


@server.tool()
def echo(text: str) -> str:
    return text


@server.tool()
def pid() -> int:
    return os.getpid()


class Answer(BaseModel):
    name: str


def ask_name():
    return Elicit("Name?", Answer)


@server.tool()
def question(answer: Annotated[Answer, Resolve(ask_name)]) -> str:
    return answer.name


@server.tool()
async def hold(ctx: Context) -> str:
    await ctx.report_progress(1, 2, "entered")
    await anyio.sleep_forever()


@server.resource("test://hello")
def resource() -> str:
    return "资源内容"


@server.prompt()
def welcome(name: str) -> str:
    return "你好 " + name


if __name__ == "__main__":
    server.run()
