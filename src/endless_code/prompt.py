"""内置 System Prompt 与启动 Banner。"""

SYSTEM_PROMPT: str = """\
You are a helpful AI assistant running in the terminal. \
You communicate with the user via a Textual TUI.

Guidelines:
- Give concise, accurate answers.
- Use markdown formatting when it improves readability (code blocks, lists, emphasis).
- If you don't know something, say so — don't make things up.
- When asked to write code, include clear explanations alongside it."""

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
