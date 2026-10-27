"""广西师范大学校园网自动登录工具。"""

__version__ = "1.0.0"

from .drcom import DrcomClient, LoginResult  # noqa: F401

__all__ = ["DrcomClient", "LoginResult", "__version__"]
