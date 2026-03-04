"""
계좌 잔고 관련 API 엔드포인트 (balance.html).
"""
from fastapi import APIRouter, HTTPException
from view.web.api_common import _get_ctx, _serialize_response

router = APIRouter()


@router.post("/sell_all")
async def sell_all_stocks():
    """모든 보유 주식을 매도한다."""
    ctx = _get_ctx()
    try:
        results = await ctx.order_execution_service.sell_all_stocks()
        # 성공 및 실패 결과에 대한 상세 정보 반환
        return {"message": "모든 주식에 대한 매도 주문이 시작되었습니다.", "results": results}
    except Exception as e:
        # 오류 발생 시 더 구체적인 오류 메시지 제공
        raise HTTPException(status_code=500, detail=f"일괄 매도 중 오류 발생: {str(e)}")


@router.get("/balance")
async def get_balance():
    """계좌 잔고 조회."""
    ctx = _get_ctx()
    resp = await ctx.stock_query_service.handle_get_account_balance()

    # 1. 기존 응답 직렬화
    result = _serialize_response(resp)

    # 2. [수정됨] 환경 설정(env) 찾기 우선순위 적용
    env = None

    # (1순위) 웹 앱 Context가 직접 가진 env 사용 (가장 확실)
    if hasattr(ctx, 'env') and ctx.env:
        env = ctx.env
    # (2순위) Broker를 통해 env 접근 (1단계 적용 시 작동함)
    elif hasattr(ctx, 'broker') and ctx.broker and hasattr(ctx.broker, 'env'):
        env = ctx.broker.env

    # 3. 계좌 정보 추출
    if env:
        # 설정값 가져오기 (active_config가 없으면 전체 설정 로드 시도)
        config = getattr(env, 'active_config', None) or {}
        if not config and hasattr(env, 'get_full_config'):
             try:
                 config = env.get_full_config()
             except:
                 config = {}

        # 계좌번호 키 찾기 (설정 파일마다 키 이름이 다를 수 있음)
        acc_no = (
            config.get("stock_account_number") or
            config.get("CANO") or
            config.get("account_number") or
            getattr(env, 'stock_account_number', None) or
            getattr(env, 'paper_stock_account_number', None) or
            "번호없음"
        )

        acc_type = "모의투자" if getattr(env, 'is_paper_trading', False) else "실전투자"

        result['account_info'] = {
            "number": acc_no,
            "type": acc_type
        }
    else:
        result['account_info'] = {
            "number": "연동실패",
            "type": "Env Not Found"
        }

    return result
