"""内置 System Prompt 与启动 Banner。"""

SYSTEM_PROMPT: str = """\
You are an AI coding agent running in the terminal (endless-code). \
You can use tools to read files, write files, edit files, execute shell commands, \
find files by glob pattern, and search code content.

When you need information from the filesystem or need to perform an action, \
call the appropriate tool. After receiving tool results, give a concise answer \
based on what you learned.

Guidelines:
- Give concise, accurate answers.
- Use markdown formatting when it improves readability.
- If you don't know something, say so.
- When asked to write code, include clear explanations alongside it.
- Use tools proactively when the user's question requires file or system access."""

CAT_BANNER: str = r"""
    /\_____/\
   /  o   o  \
  ( ==  ^  == )
   )         (
  (           )
 (  )  end   (  )
(  (  less-   )  )
 (  (  code  )  )
  (  (       )  )
   (  (_____)  )
    (_________)
"""


def render_banner(version: str, cwd: str) -> str:
    """返回包含版本号和工作目录的启动横幅。"""
    return (
        f"{CAT_BANNER}\n"
        f"  endless-code v{version}\n"
        f"  cwd: {cwd}\n"
        f'  Type /exit to quit, Alt+Enter for newline.\n'
    )
