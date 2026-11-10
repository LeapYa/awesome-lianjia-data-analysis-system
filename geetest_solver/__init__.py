"""
geetest-solver —— 极验 v4 点选验证码求解库

支持三种题型：
    word / icon  —— 模板检索定位（需要 ques 小图）
    phrase       —— YOLO 检测 + 微调 PP-OCRv6 识别 + n-gram 语序
"""

from .solver import GeetestSolver, solve

__version__ = "1.0.0"
__all__ = ["GeetestSolver", "solve"]

