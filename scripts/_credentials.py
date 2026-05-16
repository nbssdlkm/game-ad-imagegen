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


_VALID_PATH_SUFFIXES = ("/v1",)  # 我们 expected user 配 base, /v1 自动补


def _normalize_base_url(base: str) -> str:
    """规范成 `https://.../v1`. 防 user 配完整 URL 如 `.../v1/responses` → 后缀变 `.../v1/responses/v1`.
    如果 user 配的 base 含 known endpoint path (/responses, /chat/completions 等), 截到 /v1."""
    base = base.rstrip("/")
    # 检测 user 是不是配了完整 endpoint URL (e.g. .../v1/responses, .../v1/chat/completions, .../v1/images/...)
    # 如果是, 截到 /v1 那一段
    if "/v1/" in base:
        idx = base.index("/v1/")
        base = base[:idx] + "/v1"
        return base
    if base.endswith("/v1"):
        return base
    return base + "/v1"


def _read_toml() -> dict:
    """读 ~/.config/game-ad-imagegen/config.toml.

    Python 3.11+ 用 stdlib `tomllib`; 3.10 需 `pip install tomli`.
    不提供 naive fallback parser (round-5 hardcode #10: 处理 quoted '=' / multiline /
    inline comment 出 silent wrong value, 让 user 拿到错误 key 跑 401 比 fail-fast 更糟).
    """
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
            raise CredentialsError(
                f"toml 配置文件存在 ({_TOML_PATH}) 但 Python {os.sys.version_info[:2]} "
                f"无 tomllib (需 3.11+) 也无 tomli 包。\n"
                f"修复方法 (任一):\n"
                f"  - 升级到 Python 3.11+\n"
                f"  - 或装 tomli: pip install tomli\n"
                f"  - 或改用 env vars 配 credentials (见 SKILL.md First-time setup)"
            )
    except Exception as e:
        raise CredentialsError(
            f"toml 解析失败 ({_TOML_PATH}): {type(e).__name__}: {e}\n"
            f"请检查 toml 语法 (格式应为 `ephone_api_key = \"sk-...\"`)"
        )


def load_credentials() -> tuple[str, str]:
    """返回 (base_url_with_v1, api_key). 缺时 raise CredentialsError (CLI main 翻 exit-2).
    CredentialsError message 含 checked paths 让 user debug (round-5 blind #15)."""
    checked = []  # 给 error message 用, 让 user 看到哪条 fallback 检查过了

    # 路 0: 标准 OpenAI SDK env
    key = os.environ.get("OPENAI_API_KEY")
    checked.append(f"  (0) OPENAI_API_KEY env: {'set ✓' if key else 'unset'}")
    if key:
        base = os.environ.get("OPENAI_BASE_URL", _DEFAULT_BASE_URL)
        return _normalize_base_url(base), key

    # 路 1: skill 专用 env
    key = os.environ.get("GAME_AD_IMAGEGEN_EPHONE_KEY")
    checked.append(f"  (1) GAME_AD_IMAGEGEN_EPHONE_KEY env: {'set ✓' if key else 'unset'}")
    if key:
        base = os.environ.get("GAME_AD_IMAGEGEN_BASE_URL", _DEFAULT_BASE_URL)
        return _normalize_base_url(base), key

    # 路 2: 项目通用 env
    key = os.environ.get("EPHONE_API_KEY")
    checked.append(f"  (2) EPHONE_API_KEY env: {'set ✓' if key else 'unset'}")
    if key:
        base = os.environ.get("EPHONE_BASE_URL", _DEFAULT_BASE_URL)
        return _normalize_base_url(base), key

    # 路 3: ~/.config/game-ad-imagegen/config.toml (setup wizard 写)
    cfg = _read_toml()
    if cfg:
        key = cfg.get("ephone_api_key") or cfg.get("openai_api_key") or cfg.get("api_key")
        if key:
            checked.append(f"  (3) {_TOML_PATH}: found key field ✓")
            base = (
                cfg.get("ephone_base_url")
                or cfg.get("openai_base_url")
                or cfg.get("base_url")
                or _DEFAULT_BASE_URL
            )
            return _normalize_base_url(base), key
        checked.append(f"  (3) {_TOML_PATH}: 文件存在但无 ephone_api_key/openai_api_key/api_key 字段")
    else:
        checked.append(f"  (3) {_TOML_PATH}: 文件不存在")

    raise CredentialsError(
        "缺 API key。已检查的 4 路 fallback:\n"
        + "\n".join(checked)
        + "\n\n修复 (任一):\n"
        "  - 设 EPHONE_API_KEY env (Win: setx, Unix: export), 然后重启终端 / 重启宿主\n"
        "  - 或让 agent 调 First-time setup wizard 自动写 toml\n"
        "  - 或手写 ~/.config/game-ad-imagegen/config.toml:\n"
        '      ephone_api_key = "sk-..."'
    )
