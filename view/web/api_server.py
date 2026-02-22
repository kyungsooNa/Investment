from flask import Flask, jsonify, request
from managers.virtual_trade_manager import VirtualTradeManager

app = Flask(__name__)
vtm = VirtualTradeManager()

@app.route('/api/virtual/summary', methods=['GET'])
def get_virtual_summary():
    """모의투자 요약 통계 반환"""
    summary = vtm.get_summary()
    return jsonify(summary)

@app.route('/api/virtual/history', methods=['GET'])
def get_virtual_history():
    """전체 거래 내역 반환 (요약, 보유, 매도 리스트 포함)"""
    return jsonify({
        "summary": vtm.get_summary(),
        "holds": vtm.get_holds(),
        "solds": vtm.get_solds()
    })

@app.route('/api/virtual/strategies', methods=['GET'])
def get_strategies():
    """등록된 모든 전략 목록 반환 (UI 탭 생성용)"""
    strategies = vtm.get_all_strategies()
    return jsonify(strategies)

@app.route('/api/virtual/chart/<strategy_name>', methods=['GET'])
def get_strategy_chart(strategy_name):
    """특정 전략의 수익률 히스토리(차트용) 반환"""
    history = vtm.get_strategy_return_history(strategy_name)
    return jsonify(history)

@app.route('/api/virtual/holds', methods=['GET'])
def get_holds():
    """현재 보유 중인 종목 반환 (전략별 필터 가능)"""
    strategy = request.args.get('strategy')
    if strategy:
        holds = vtm.get_holds_by_strategy(strategy)
    else:
        holds = vtm.get_holds()
    return jsonify(holds)

if __name__ == '__main__':
    app.run(debug=True, port=5000)