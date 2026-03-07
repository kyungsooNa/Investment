import unittest
from common.types import ResStockFullInfoApiOutput

class TestResStockFullInfoApiOutputNewHighLow(unittest.TestCase):
    """ResStockFullInfoApiOutput의 신고가/신저가 프로퍼티 로직 테스트"""

    def _create_obj(self, code_val):
        # Pydantic 모델 유효성 검사를 통과하기 위해 모든 필수 필드에 더미 값을 채웁니다.
        data = {name: "0" for name in ResStockFullInfoApiOutput.model_fields}
        data["new_hgpr_lwpr_cls_code"] = code_val
        return ResStockFullInfoApiOutput.model_validate(data)

    def test_is_new_high(self):
        # API 코드 "1"
        self.assertTrue(self._create_obj("1").is_new_high)
        # 한글 "신고가"
        self.assertTrue(self._create_obj("신고가").is_new_high)
        
        # 아닌 경우
        self.assertFalse(self._create_obj("2").is_new_high)
        self.assertFalse(self._create_obj("신저가").is_new_high)
        self.assertFalse(self._create_obj("0").is_new_high)
        self.assertFalse(self._create_obj(None).is_new_high)

    def test_is_new_low(self):
        # API 코드 "2"
        self.assertTrue(self._create_obj("2").is_new_low)
        # 한글 "신저가"
        self.assertTrue(self._create_obj("신저가").is_new_low)

        # 아닌 경우
        self.assertFalse(self._create_obj("1").is_new_low)
        self.assertFalse(self._create_obj("신고가").is_new_low)
        self.assertFalse(self._create_obj("0").is_new_low)
        self.assertFalse(self._create_obj(None).is_new_low)

    def test_new_high_low_status_text(self):
        self.assertEqual(self._create_obj("1").new_high_low_status, "신고가")
        self.assertEqual(self._create_obj("신고가").new_high_low_status, "신고가")
        self.assertEqual(self._create_obj("2").new_high_low_status, "신저가")
        self.assertEqual(self._create_obj("신저가").new_high_low_status, "신저가")
        self.assertEqual(self._create_obj("0").new_high_low_status, "-")