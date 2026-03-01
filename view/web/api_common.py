"""
웹 API 공통 유틸리티: 컨텍스트 관리, 응답 직렬화, 인증, Pydantic 모델.
"""
from fastapi import HTTPException, Request
from pydantic import BaseModel

_ctx = None  # 전역 변수로 선언

# API 호출 실패 시 대처를 위한 전역 가격 캐시 (메모리)
_PRICE_CACHE = {}  # {code: (price, rate, timestamp)}


def set_ctx(ctx):
    global _ctx
    _ctx = ctx


def _get_ctx():
    if _ctx is None:
        raise HTTPException(status_code=503, detail="서비스가 초기화되지 않았습니다.")
    return _ctx


def check_auth(request: Request):
    """로그인 여부를 확인하는 공통 함수."""
    ctx = _get_ctx()
    expected_token = ctx.env.active_config.get("auth", {}).get("secret_key")
    token = request.cookies.get("access_token")

    if token != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


def _serialize_response(resp):
    """ResCommonResponse를 JSON 직렬화 가능한 dict로 변환."""
    if resp is None:
        return {"rt_cd": "999", "msg1": "응답 없음", "data": None}
    if hasattr(resp, 'to_dict'):
        return resp.to_dict()
    return {"rt_cd": str(resp.rt_cd), "msg1": str(resp.msg1), "data": resp.data}


def _serialize_list_items(items):
    """dataclass 리스트를 dict 리스트로 변환."""
    result = []
    for item in (items or []):
        if hasattr(item, 'to_dict'):
            result.append(item.to_dict())
        elif isinstance(item, dict):
            result.append(item)
        else:
            result.append(str(item))
    return result


# --- Pydantic 모델 ---

class OrderRequest(BaseModel):
    code: str
    price: str
    qty: str
    side: str  # "buy" or "sell"


class EnvironmentRequest(BaseModel):
    is_paper: bool


class ProgramTradingRequest(BaseModel):
    code: str


class ProgramTradingUnsubscribeRequest(BaseModel):
    code: str | None = None


class ProgramTradingDataModel(BaseModel):
    chartData: dict
    subscribedCodes: list
    codeNameMap: dict
    savedAt: str | None = None
