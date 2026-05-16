"""共享 credentials loader — 跟 ephone (OpenAI 兼容端) 集成的两个 hybrid 脚本
(rewrite_prompt.py 走 openai SDK chat.completions, image_gen_hybrid.py 走 requests POST
/v1/responses) 共用同一份 env vars."""
import os


def load_credentials() -> tuple[str, str]:
    """返回 (base_url_with_v1, api_key). 缺 EPHONE_API_KEY 时友好报错并 SystemExit."""
    key = os.environ.get("EPHONE_API_KEY")
    if not key:
        raise SystemExit(
            "EPHONE_API_KEY 未设置。请在系统 env 配置:\n"
            "  Windows: setx EPHONE_API_KEY \"sk-...\"  (重开终端生效)\n"
            "  Linux/Mac: export EPHONE_API_KEY=\"sk-...\""
        )
    base = os.environ.get("EPHONE_BASE_URL", "https://api.ephone.ai")
    if not base.endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    return base, key
