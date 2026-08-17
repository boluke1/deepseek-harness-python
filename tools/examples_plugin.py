# ============================================================================
# tools/examples_plugin.py
# ExamplesPlugin：为 Agent 提供一组实用示例工具。
# ★ 采用 ctx.tools 属性访问工具注册表（对标 DSH 反射层）。
# ============================================================================

import datetime
import logging

from mycordis import Context, Plugin

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class ExamplesPlugin(Plugin):
    """
    注册一组示例工具（依赖 'tools' 服务）。
    """

    # --- 插件声明 ---
    inject = ['tools']
    provide = []

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # ★ 用 ctx.tools 属性访问工具注册表。
        tools = ctx.tools

        # ---- 工具 1：获取当前时间 ----
        def get_current_datetime() -> str:
            """
            返回当前的日期和时间。
            Returns a string like 'YYYY-MM-DD HH:MM:SS'.
            """
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        tools.register_fn(
            "get_current_datetime",
            get_current_datetime,
            description="返回当前的日期和时间字符串，格式为 YYYY-MM-DD HH:MM:SS",
            parameters={"type": "object", "properties": {}, "required": []},
        )

        # ---- 工具 2：计算器 ----
        def calculator(expression: str) -> str:
            """
            计算一个简单的四则运算表达式。
            """
            try:
                allowed = set("0123456789+-*/(). ")
                if not all(c in allowed for c in expression):
                    return "错误：表达式包含非法字符，仅支持数字和 + - * / ( ) 运算。"
                result = eval(expression, {"__builtins__": {}}, {})
                return f"计算结果: {result}"
            except Exception as e:
                return f"错误：无法计算该表达式（{e}）"

        tools.register_fn(
            "calculator",
            calculator,
            description="计算一个简单的四则运算表达式，返回计算结果字符串。仅支持 + - * / 和括号。",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的数学表达式，如 '2 + 3 * 4'",
                    },
                },
                "required": ["expression"],
            },
        )

        # ---- 工具 3：模拟天气查询 ----
        def get_weather_info(city: str) -> str:
            """
            查询指定城市的天气（模拟数据）。
            """
            weather_map = {
                "北京": "晴，25°C，微风",
                "上海": "多云，28°C，湿度 70%",
                "广州": "雷阵雨，30°C，注意带伞",
            }
            if city in weather_map:
                return f"{city}的天气：{weather_map[city]}"
            return f"抱歉，暂无 {city} 的天气数据，可查询的城市：北京、上海、广州。"

        tools.register_fn(
            "get_weather_info",
            get_weather_info,
            description="查询指定城市的天气信息（模拟）。返回天气描述字符串。",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，如 '北京'"},
                },
                "required": ["city"],
            },
        )

        logger.info("[ExamplesPlugin] 已注册 3 个示例工具")
