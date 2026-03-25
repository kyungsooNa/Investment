import unittest
from common.types import ResStockFullInfoApiOutput

class TestResStockFullInfoApiOutputNewHighLow(unittest.TestCase):
    """ResStockFullInfoApiOutput의 신고가/신저가 프로퍼티 로직 테스트"""

    def _create_obj(self, code_val):
        # 이제 모든 필드에 기본값이 설정되어 있으므로 필수 필드 더미 값을 채울 필요가 없습니다.
        data = {"new_hgpr_lwpr_cls_code": code_val}
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

    def test_missing_fields_handled_gracefully(self):
        """기타 속성(필드)이 모두 누락된 상태의 dict를 넣어도 에러 없이 정상 생성되는지 확인"""
        data = {"new_hgpr_lwpr_cls_code": "1"}
        obj = ResStockFullInfoApiOutput.model_validate(data)
        self.assertTrue(obj.is_new_high)
        self.assertEqual(obj.stck_prpr, "")  # 누락된 필드는 기본값("")을 가짐