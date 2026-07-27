"""Exceptions shared across the toolbox.

ToolError lives here rather than in compress.py because inspect.py raises it
too, and compress.py imports inspect -- putting it in either module would
create an import cycle.
"""


class ToolError(RuntimeError):
    """An external tool failed. ``stderr`` carries its diagnostics."""

    def __init__(self, message, stderr=""):
        super().__init__(message)
        self.stderr = stderr
