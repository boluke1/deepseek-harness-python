# ============================================================================
# mycordis/core/errors.py
# 结构化错误系统（对标 DSH fiber.ts CordisError + Validation）。
#
# 提供框架级错误类型和工具函数：
#   · CordisError：带稳定错误码的框架错误
#   · Validation：配置验证失败错误
#   · compose_error：组合清理过程中的多个错误
#   · enhance_error：增强错误堆栈追踪
# ============================================================================

import traceback


class CordisError(Exception):
    """
    框架级错误，带稳定错误码（对标 DSH CordisError）。

    错误码同时作为默认错误消息。
    """

    INACTIVE_EFFECT = 'cannot create effect on inactive context'
    INVALID_EFFECT = 'invalid effect'
    INVALID_CONFIG = 'invalid config'

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


class Validation(TypeError):
    """
    配置验证失败错误（对标 DSH Validation）。

    聚合 schema 验证产生的所有问题，生成可读的错误消息。
    """

    def __init__(self, issues: list):
        self.issues = issues
        lines = []
        for issue in issues:
            msg = issue.get('message', str(issue))
            path = issue.get('path')
            if path:
                lines.append(f"  - {msg} (at {'.'.join(str(p) for p in path)})")
            else:
                lines.append(f"  - {msg}")
        super().__init__("invalid config:\n" + "\n".join(lines))


def compose_error(cleanup_fn, error_catcher=None):
    """
    执行清理函数，捕获并组合错误（对标 DSH composeError）。

    :param cleanup_fn:   清理函数（可同步或异步）。
    :param error_catcher: 可选的错误捕获回调 (error) -> None。
    :return:             清理函数的返回值，或 None。
    """
    import asyncio

    try:
        result = cleanup_fn()
        if asyncio.iscoroutine(result):
            return result
        return result
    except Exception as e:
        if error_catcher:
            error_catcher(e)
        return None


def enhance_error(error: Exception) -> Exception:
    """
    增强错误堆栈追踪（对标 DSH enhanceError）。

    在堆栈顶部插入原始错误消息，便于诊断。
    """
    if error.__traceback__:
        lines = traceback.format_exception(type(error), error, error.__traceback__)
        enhanced = [f"Error: {error}\n"] + lines[1:]
        try:
            error.__traceback_str__ = "".join(enhanced)
        except Exception:
            pass
    return error
