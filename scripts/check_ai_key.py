"""AI 키·엔드포인트 연결을 단독 검증하는 진단 스크립트.

config/config.yaml 의 ai_analysis 설정(base_url/api_key/model)을 읽어 실제
chat completion 을 한 번 호출한다. 키를 명령행에 넣지 않으므로 셸 히스토리에
남지 않는다. Gemini/Groq/Ollama 공통(AiClient 가 provider 무관).

사용:
    python scripts/check_ai_key.py
"""
import asyncio
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 콘솔(cp949)이 처리 못 하는 문자에서 출력이 잘리지 않도록 UTF-8 고정.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from services.ai_client import AiClient  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_DEFAULT_MODEL = "gemini-2.5-flash"


def _load_ai_config() -> dict:
    config_path = _ROOT / "config" / "config.yaml"
    if not config_path.exists():
        print(f"[오류] {config_path} 가 없습니다.")
        print("→ config/config.yaml.example 을 복사해 config.yaml 로 만들고 키를 넣으세요.")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("ai_analysis") or {}


def _mask(api_key: str) -> str:
    if len(api_key) > 12:
        return f"{api_key[:6]}...{api_key[-4:]}"
    return "***"


async def _main() -> None:
    cfg = _load_ai_config()
    api_key = str(cfg.get("api_key") or "")
    base_url = str(cfg.get("base_url") or _DEFAULT_BASE_URL)
    model = str(cfg.get("model") or _DEFAULT_MODEL)
    provider = str(cfg.get("provider") or "gemini").strip().lower()

    if not api_key and provider != "ollama":
        print("[오류] ai_analysis.api_key 가 비어 있습니다. config.yaml 에 키를 넣으세요.")
        sys.exit(1)
    # Gemini API 키는 'AIza'(구형) 또는 'AQ.'(신형) 로 시작한다. 둘 다 유효.
    if (
        "googleapis" in base_url
        and not api_key.startswith("AIza")
        and not api_key.startswith("AQ.")
    ):
        print(f"[경고] Gemini 키는 보통 'AIza' 또는 'AQ.' 로 시작합니다. 현재 키: {_mask(api_key)}")
        print("→ Google AI Studio > Create API key 로 발급한 값인지 확인하세요.")

    print(f"모델={model}  base_url={base_url}  키={_mask(api_key)}")
    print("AI 호출 중...")

    client = AiClient(base_url=base_url, api_key=api_key, model=model, timeout_sec=20)
    try:
        result = await client.complete(
            system="너는 한국 주식 애널리스트다.",
            user="삼성전자 유상증자 공시를 투자자 관점에서 한 문장으로 요약해줘.",
        )
    except Exception as exc:
        print(f"[실패] {type(exc).__name__}: {exc}")
        print("→ 키 형식(AIza)·모델명·네트워크/프록시를 확인하세요.")
        sys.exit(1)

    print("[성공] 응답:")
    print(result)


if __name__ == "__main__":
    asyncio.run(_main())
