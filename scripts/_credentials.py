"""共享 credentials loader — 跟 ephone (OpenAI 兼容端) 集成的两个 hybrid 脚本
(rewrite_prompt.py 走 openai SDK chat.completions, image_gen_hybrid.py 走 requests POST
/v1/responses) 共用同一份 env vars。

4 路 fallback (按优先级):
  0. OPENAI_API_KEY env + OPENAI_BASE_URL env (标准 OpenAI SDK 兼容)
  1. GAME_AD_IMAGEGEN_EPHONE_KEY env (skill 专用)
  2. EPHONE_API_KEY env (项目通用)
  3. ~/.config/game-ad-imagegen/config.toml (agent setup wizard 写的)

跟 SKILL.md "First-time setup" 段对齐 — setup wizard 写的 toml 真的会被读到。
"""
from __future__ import annotations
import os
from pathlib import Path


class CredentialsError(RuntimeError):
    """缺 credentials. CLI main() 翻成 exit-code 2; library 路径可 except 处理."""
    pass


_DEFAULT_BASE_URL = "https://api.ephone.ai"
_TOML_PATH = Path.home() / ".config" / "game-ad-imagegen" / "config.toml"


def _normalize_base_url(base: str) -> str:
    if not base.endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    return base


def _read_toml() -> dict:
    """读 ~/.config/game-ad-imagegen/config.toml. 文件不存在或解析失败返回空 dict."""
    if not _TOML_PATH.exists():
        return {}
    try:
        import tomllib  # stdlib 3.11+
        return tomllib.loads(_TOML_PATH.read_text(encoding="utf-8"))
    except ImportError:
        try:
            import tomli  # backport for 3.10
            return tomli.loads(_TOML_PATH.read_text(encoding="utf-8"))
        except ImportError:
            # 最 naive 后备: 只 parse `key = "value"` 单行格式
            cfg = {}
            for line in _TOML_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip().strip('"').strip("'")
            return cfg
    except Exception:
        return {}


def load_credentials() -> tuple[str, str]:
    """返回 (base_url_with_v1, api_key). 缺时 raise CredentialsError (CLI main 翻 exit-2)."""
    # 路 0: 标准 OpenAI SDK env
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        base = os.environ.get("OPENAI_BASE_URL", _DEFAULT_BASE_URL)
        return _normalize_base_url(base), key

    # 路 1: skill 专用 env
    key = os.environ.get("GAME_AD_IMAGEGEN_EPHONE_KEY")
    if key:
        base = os.environ.get("GAME_AD_IMAGEGEN_BASE_URL", _DEFAULT_BASE_URL)
        return _normalize_base_url(base), key

    # 路 2: 项目通用 env
    key = os.environ.get("EPHONE_API_KEY")
    if key:
        base = os.environ.get("EPHONE_BASE_URL", _DEFAULT_BASE_URL)
        return _normalize_base_url(base), key

    # 路 3: ~/.config/game-ad-imagegen/config.toml (setup wizard 写)
    cfg = _read_toml()
    if cfg:
        # 兼容 ephone / openai 两种 key 字段命名
        key = cfg.get("ephone_api_key") or cfg.get("openai_api_key") or cfg.get("api_key")
        if key:
            base = (
                cfg.get("ephone_base_url")
                or cfg.get("openai_base_url")
                or cfg.get("base_url")
                or _DEFAULT_BASE_URL
            )
            return _normalize_base_url(base), key

    raise CredentialsError(
        "缺 API key。可选 4 种配置方式 (按优先级):\n"
        "  (0) 设 OPENAI_API_KEY env (标准 OpenAI SDK 兼容)\n"
        "  (1) 设 GAME_AD_IMAGEGEN_EPHONE_KEY env (本 skill 专用)\n"
        "  (2) 设 EPHONE_API_KEY env (项目通用)\n"
        "  (3) 写 ~/.config/game-ad-imagegen/config.toml:\n"
        '      ephone_api_key = "sk-..."\n'
        "\n"
        "WorkBuddy / Claude Code 用户: agent 可调用 first-time setup wizard 自动写 (3).\n"
        "纯 CLI 用户: setx (Win) / export (Unix) 一种 env, 然后重启终端 / 重启宿主。"
    )
