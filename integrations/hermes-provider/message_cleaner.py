"""Message cleaning + triviality detection — filters out useless content and
context injection before storage.

Reference: Supermemory (_is_trivial_message, message cleaning)
Purpose: strip tags Hermes injects into its own context before storing to
Minnas, and filter out meaningless messages.

v1.1 upgrade (0706): Chinese acknowledgement-phrase filtering + tool-call JSON
filtering + information-density detection.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── Context-injection tags to strip ──

_CONTEXT_INJECTION_PATTERNS = [
    r"## Minnas related memories[\s\S]*?(?:\n---|\n\n)",
    r"## Mnemosyne related memories[\s\S]*?(?:\n---|\n\n)",  # legacy header (pre-rename)
    r"## Mnemosyne 关联记忆[\s\S]*?(?:\n---|\n\n)",  # legacy Chinese header (pre-translation)
    r"## 关联记忆[\s\S]*?(?:\n---|\n\n)",
    r"<hermes-context>[\s\S]*?</hermes-context>",
    r"<memory-context>[\s\S]*?</memory-context>",
    r"\[SYSTEM\].*?(?:\n|$)",
]

_CONTEXT_RE = re.compile(
    "|".join(_CONTEXT_INJECTION_PATTERNS),
    re.DOTALL | re.IGNORECASE,
)

# ── Trivial-message detection ──

_MIN_CONTENT_LENGTH = 5

_TRIVIAL_PATTERNS = [
    r"^[\s\d\.,!?。，！？、\-—……~～·@#\$%\^&\*\(\)\[\]{}:;\"'《》【】]+$",
    r"^[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    r"\U0001F1E0-\U0001F1FF\u2600-\u26FF\u2700-\u27BF"
    r"\uFE00-\uFE0F\u200D]+$",
    r"^(.{1,4})\1{2,}$",
    r"^\s*$",
    # v1.1: Chinese acknowledgement phrases
    r"^(好的|收到|明白|了解|懂了|知道了|行|好|嗯|哦|噢|OK|ok|OKAY|okay)[\s!！。.]*$",
    r"^(明白了|了解了|收到了|好的好的|行吧|好吧|可以|没问题)[\s!！。.]*$",
    # v1.1: pure action acknowledgements
    r"^(已(完成|处理|执行|修复|更新|删除|添加|创建|保存|记录))[\s!！。.]*$",
    # v1.1: raw JSON tool results
    r"^\s*\{\s*\".*\}\s*$",
    # v1.1: extremely low information density
    r"^[^a-zA-Z\u4e00-\u9fff]{0,3}$",
]

_TRIVIAL_RE = re.compile("|".join(_TRIVIAL_PATTERNS))

# ── v1.1: effective content density ──

def _content_density(content: str) -> float:
    """Fraction of the content made up of Chinese characters, English letters, and digits."""
    if not content:
        return 0.0
    meaningful = sum(1 for c in content if (
        '\u4e00' <= c <= '\u9fff' or c.isalpha() or c.isdigit()
    ))
    return meaningful / len(content)


def clean_content(content: str) -> str:
    """Strip context-injection tags."""
    if not content:
        return ""
    cleaned = _CONTEXT_RE.sub("", content).strip()
    return cleaned


def is_trivial(content: str, min_length: int = _MIN_CONTENT_LENGTH) -> bool:
    """Determine whether a message is meaningless."""
    if not content:
        return True
    if len(content) < min_length:
        return True
    if _TRIVIAL_RE.match(content):
        return True
    # v1.1: long content but effective density < 30% → garbled text / noise
    if len(content) >= 10 and _content_density(content) < 0.3:
        return True
    return False


def prepare_for_storage(content: str, min_length: int = _MIN_CONTENT_LENGTH) -> str:
    """Pre-storage processing: clean + filter."""
    cleaned = clean_content(content)
    if not cleaned:
        return ""
    if is_trivial(cleaned, min_length):
        logger.debug("Skipping trivial message: %s", cleaned[:30])
        return ""
    return cleaned
