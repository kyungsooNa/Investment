"""
프로그램매매 실시간 스트리밍 관련 API 엔드포인트 (program.html).
"""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from view.web.api_common import (
    _get_ctx, _serialize_response,
    ProgramTradingRequest, ProgramTradingUnsubscribeRequest, ProgramTradingDataModel,
)

router = APIRouter()


@router.post("/program-trading/subscribe")
async def subscribe_program_trading(req: ProgramTradingRequest):
    """프로그램매매 실시간 구독 시작 (다중 종목 추가 구독)."""
    ctx = _get_ctx()
    success = await ctx.start_program_trading(req.code)
    if not success:
        raise HTTPException(status_code=500, detail="WebSocket 연결 실패")
    mapper = getattr(ctx, 'stock_code_mapper', None)
    stock_name = mapper.get_name_by_code(req.code) if mapper else ''
    # [변경] 매니저 사용
    return {"success": True, "code": req.code, "stock_name": stock_name, "codes": ctx.realtime_data_manager.get_subscribed_codes()}


@router.get("/program-trading/history/{code}")
async def get_program_trading_history(code: str):
    """프로그램 매매 추이 히스토리 조회 (차트용)."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    resp = await ctx.stock_query_service.handle_get_program_trading_history(code)
    result = _serialize_response(resp)

    if result.get("rt_cd") == "0" and isinstance(result.get("data"), dict):
        mapper = getattr(ctx, 'stock_code_mapper', None)
        result["data"]["name"] = mapper.get_name_by_code(code) if mapper else ""
    ctx.pm.log_timer(f"get_program_trading_history({code})", t_start)
    return result


@router.post("/program-trading/unsubscribe")
async def unsubscribe_program_trading(req: ProgramTradingUnsubscribeRequest = None):
    """프로그램매매 구독 해지. code 지정 시 개별 해지, 미지정 시 전체 해지."""
    ctx = _get_ctx()
    if req and req.code:
        await ctx.stop_program_trading(req.code)
    else:
        await ctx.stop_all_program_trading()
    # [변경] 매니저 사용
    return {"success": True, "codes": ctx.realtime_data_manager.get_subscribed_codes()}


@router.get("/program-trading/status")
async def get_program_trading_status():
    """프로그램매매 구독 상태 확인."""
    ctx = _get_ctx()
    # [변경] 매니저 사용
    codes = ctx.realtime_data_manager.get_subscribed_codes()
    return {
        "subscribed": len(codes) > 0,
        "codes": codes,
    }


@router.get("/program-trading/stream")
async def stream_program_trading(request: Request):
    """SSE 스트리밍: 프로그램매매 실시간 데이터를 브라우저에 전달 (Array 배열 전송 최적화 적용)."""
    ctx = _get_ctx()
    # 매니저를 통해 큐 생성 및 등록
    queue = ctx.realtime_data_manager.create_subscriber_queue()

    async def event_generator():
        try:
            # 1. 저장된 과거 데이터 먼저 전송 (Replay)
            history = ctx.realtime_data_manager.get_history_data()
            for code, items in list(history.items()):
                for item in list(items):
                    # 과거 데이터도 클라이언트가 해석할 수 있도록 Array로 변환하여 전송
                    # 배열 순서: [종목코드, 체결시간, 현재가, 등락률, 대비, 부호, 매도체결, 매수체결, 순매수체결, 순매수대금, 매도잔량, 매수잔량]
                    payload = [
                        code,
                        item.get('주식체결시간', ''),
                        item.get('price', 0),
                        item.get('rate', 0),
                        item.get('change', 0),
                        item.get('sign', ''),
                        item.get('매도체결량', 0),
                        item.get('매수2체결량', 0),
                        item.get('순매수체결량', 0),
                        item.get('순매수거래대금', 0),
                        item.get('매도호가잔량', 0),
                        item.get('매수호가잔량', 0)
                    ]
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.0001)

            # 2. 실시간 데이터 전송 (이미 realtime_data_manager에서 Array로 변환하여 큐에 넣음)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=0.1)
                    if data is None:  # 테스트 종료 신호 (Poison Pill)
                        break
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # 연결 종료 시 매니저를 통해 큐 제거
            ctx.realtime_data_manager.remove_subscriber_queue(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/program-trading/save-data")
async def save_pt_data(data: ProgramTradingDataModel):
    """프로그램 매매 데이터를 서버 파일(data/program_subscribe/pt_data.json)에 저장"""
    try:
        # [변경] 매니저를 통해 스냅샷 저장
        ctx = _get_ctx()
        t_start = ctx.pm.start_timer()
        ctx.realtime_data_manager.save_snapshot(data.model_dump())
        ctx.pm.log_timer("save_pt_data", t_start)
        return {"success": True}
    except Exception as e:
        print(f"[WebAPI] PT Data Save Error: {e}")
        return {"success": False, "msg": str(e)}


@router.get("/program-trading/load-data")
async def load_pt_data():
    """서버 파일에서 프로그램 매매 데이터 로드"""
    # [변경] 매니저를 통해 스냅샷 로드
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    data = ctx.realtime_data_manager.load_snapshot()
    ctx.pm.log_timer("load_pt_data", t_start)

    if data is None:
        return {"success": False, "msg": "File not found"}
    return {"success": True, "data": data}


@router.get("/program-trading/db-status")
async def get_db_status():
    """DB 내부 상태(스냅샷 시간, 히스토리 건수 등) 조회."""
    ctx = _get_ctx()
    return ctx.realtime_data_manager.inspect_db_status()


@router.websocket("/ws/echo")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 테스트용 에코 엔드포인트."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Message text was: {data}")
    except WebSocketDisconnect:
        pass
