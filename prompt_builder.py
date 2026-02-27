"""
prompt_builder.py - LLM構造化専用プロンプト生成
AI Trading System v3.0

LLMには判断させず、データの分類・正規化のみを行わせるプロンプトを生成する。
旧: approve/reject判定プロンプト → 新: データ構造化プロンプト
"""

import json
import logging

logger = logging.getLogger(__name__)

# llm_structurer.py に定義されたシステムプロンプトを使用する
# (旧 SYSTEM_PROMPT は完全に削除)


def build_structuring_prompt(context: dict) -> list[dict]:
    """
    LLM構造化用のプロンプトを生成する。
    LLMには判断させず、データの分類・正規化のみを行わせる。

    Args:
        context: context_builder.py が生成するコンテキスト dict

    Returns:
        [{"role": "system", "content": ...}, {"role": "user", "content": ...}]
    """
    from llm_structurer import STRUCTURING_SYSTEM_PROMPT

    user_content = json.dumps(context, ensure_ascii=False, default=str)

    return [
        {"role": "system", "content": STRUCTURING_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def build_prompt(context: dict) -> list[dict]:
    """
    後方互換のためのラッパー。
    revaluator.py が build_prompt() を呼び出しているため維持する。

    v3.0では構造化プロンプトを返す。
    """
    return build_structuring_prompt(context)
