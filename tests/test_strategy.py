import unittest
import pytest # pytest.mark.asyncio를 사용하기 위함
from interfaces.strategy import Strategy # 실제 Strategy 클래스 경로에 맞게 수정
from typing import List, Dict # 타입 힌트 임포트


class TestStrategyInterface(unittest.IsolatedAsyncioTestCase):

    async def test_run_not_implemented_error(self):
        """
        TC: Strategy 인터페이스의 run 메서드를 구현하지 않고 호출했을 때
            NotImplementedError가 발생하는지 테스트합니다.
        이는 interfaces/strategy.py의 6번 라인 (`raise NotImplementedError`)을 커버합니다.
        """
        # Given: Strategy 클래스의 인스턴스를 직접 생성합니다.
        # 이 인스턴스는 run 메서드를 오버라이드하지 않았습니다.
        strategy_instance = Strategy()

        # When & Then: run 메서드를 호출했을 때 NotImplementedError가 발생하는지 검증합니다.
        with self.assertRaises(NotImplementedError):
            await strategy_instance.run(stock_codes=[]) # stock_codes는 List[str] 타입 힌트에 맞춤

        # run 메서드는 Dict를 반환하도록 타입 힌트가 되어 있지만, NotImplementedError가 발생해야 하므로
        # 실제 반환값을 검증할 필요는 없습니다.