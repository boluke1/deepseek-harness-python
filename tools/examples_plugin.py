# ============================================================================
# tools/examples_plugin.py
# ExamplesPlugin：为 Agent 提供一组实用示例工具。
#
# 设计意图：
#   演示如何用 mycordis 插件机制向 Agent 提供工具能力。
#   本插件依赖 'tools' 服务（inject = ['tools']），在 apply 中注册若干工具：
#     · get_current_datetime  获取当前日期与时间
#     · calculator            简单四则运算计算器
#     · get_weather_info      模拟天气查询（演示错误处理）
#
# 工具设计原则（对标 DSH）：
#   · 名称用动宾结构，语义清晰。
#   · 描述"返回什么"，而非仅"接受什么"。
#   · 用参数 Schema 明确每个参数的类型与必填性。
#   · 内部出错时返回结构化错误信息，而非抛出异常。
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

    # inject：依赖 'tools' 服务（工具注册表）。
    inject = ['tools']

    # provide：本插件不直接提供新服务，只是往 'tools' 注册表里添加工具。
    # 因此 provide 为空（它不对外提供独立服务键）。
    provide = []

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # 获取工具注册表。
        tools = ctx.get('tools')

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
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )

        # ---- 工具 2：计算器 ----
        def calculator(expression: str) -> str:
            """
            计算一个简单的四则运算表达式。
            Args:
                expression (str): 数学表达式，仅支持 + - * / 和括号，如 '2 + 3 * 4'。
            Returns:
                str: 计算结果字符串；若表达式非法，返回错误说明。
            """
            try:
                # 使用 eval 需谨慎；这里用白名单校验表达式，仅允许数字、运算符、空格、括号、小数点。
                allowed = set("0123456789+-*/(). ")
                if not all(c in allowed for c in expression):
                    return "错误：表达式包含非法字符，仅支持数字和 + - * / ( ) 运算。"
                # 求值并返回。
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

        # ---- 工具 3：模拟天气查询（演示结构化错误处理）----
        def get_weather_info(city: str) -> str:
            """
            查询指定城市的天气（模拟数据）。
            Args:
                city (str): 城市名称，如 '北京'、'上海'。
            Returns:
                str: 天气描述字符串。
            """
            # 用一个简单的映射模拟天气，未知城市返回错误提示。
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
                    "city": {
                        "type": "string",
                        "description": "城市名称，如 '北京'",
                    },
                },
                "required": ["city"],
            },
        )

        logger.info("[ExamplesPlugin] 已注册 3 个示例工具")
