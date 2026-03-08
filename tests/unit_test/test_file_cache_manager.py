# tests/unit_test/test_file_cache_manager.py
import os
import json
import tempfile
import time
from typing import Any
import pytest
from unittest.mock import MagicMock, patch

from pydantic import BaseModel
from core.cache.file_cache_manager import FileCacheManager, load_deserializable_classes


# ------------------------
# 기본 유틸 / 더미 클래스
# ------------------------
def _mk_manager(base_dir: str, classes: list[type] | None = None) -> FileCacheManager:
    cfg = {"cache": {"base_dir": base_dir, "deserializable_classes": []}}
    m = FileCacheManager(config=cfg)
    if classes is not None:
        m._deserializable_classes = classes  # 테스트용 주입
    return m


# dataclasses.fields()를 쓰므로 dataclass여야 함
from dataclasses import dataclass

class PydanticDummy(BaseModel):
    x: int
    y: str

@dataclass
class Dummy:
    a: int
    b: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]):
        return cls(**d)

@dataclass
class ResCommonResponse:  # 이름 정확히 맞춰서 특수 분기 유도
    rt_cd: str
    msg1: str
    data: Any = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]):
        return cls(**d)


# ------------------------
# load_deserializable_classes
# ------------------------
def test_load_deserializable_classes_invalid_path(capfd):
    # 존재하지 않는 경로 → except 블록 실행(로그는 print)
    classes = load_deserializable_classes(["totally.not.exists.ClassName"])
    out, _ = capfd.readouterr()
    assert classes == []
    # "[❌ 클래스 로드 실패]" 메시지가 포함되어야 함
    assert "클래스 로드 실패" in out  # :contentReference[oaicite:0]{index=0}


# ------------------------
# __init__(config is None) 분기
# ------------------------
def test_init_with_none_config(monkeypatch, tmp_path):
    dummy_config = {"cache": {"base_dir": str(tmp_path), "deserializable_classes": []}}
    monkeypatch.setattr(
        "core.cache.file_cache_manager.load_cache_config",
        lambda: dummy_config
    )
    mgr = FileCacheManager(config=None)  # config None → load_cache_config 호출 :contentReference[oaicite:1]{index=1}
    assert mgr._base_dir == str(tmp_path)


# ------------------------
# _serialize: 리스트/튜플 분기
# ------------------------
def test_serialize_list_and_tuple(tmp_path):
    mgr = _mk_manager(str(tmp_path))
    data = [{"a": 1}, (2, 3, [4, 5])]
    out = mgr._serialize(data)  # 리스트/튜플을 재귀 직렬화 :contentReference[oaicite:2]{index=2}
    assert isinstance(out, list)
    # tuple → list 로 변환되어야 함
    assert isinstance(out[1], list)
    # 내부 리스트도 유지
    assert out[1][2] == [4, 5]


# ------------------------
# _deserialize: 클래스 매칭 + ResCommonResponse 특수 처리 + 리스트/튜플 분기
# ------------------------
def test_deserialize_with_matching_class_and_rescommonresponse(tmp_path):
    mgr = _mk_manager(str(tmp_path), classes=[ResCommonResponse, Dummy])

    raw = {
        "rt_cd": "0",
        "msg1": "ok",
        "data": {"a": 10, "b": 20},  # 내부 data를 먼저 재귀 복원 → Dummy로 변환 후 바깥 from_dict :contentReference[oaicite:3]{index=3}
    }
    res = mgr._deserialize(raw)
    assert isinstance(res, ResCommonResponse)
    assert isinstance(res.data, Dummy)
    assert res.data.a == 10
    assert res.data.b == 20


def test_deserialize_list_branch(tmp_path):
    mgr = _mk_manager(str(tmp_path), classes=[Dummy])
    raw_list = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    res = mgr._deserialize(raw_list)  # 리스트 분기 타기 :contentReference[oaicite:4]{index=4}
    assert all(isinstance(x, Dummy) for x in res)
    assert [x.a for x in res] == [1, 3]


# ------------------------
# set: 파일 저장 실패 예외 처리
# ------------------------
def test_set_file_write_error_logs(tmp_path):
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)

    with patch("builtins.open", side_effect=IOError("disk error")):
        mgr.set("k", {"data": 123}, save_to_file=True)  # except 블록 → logger.error

    assert logger.error.called
    msg = logger.error.call_args[0][0]
    assert "저장 실패" in msg  # 한글 메시지 확인


# ------------------------
# delete: 파일 삭제 실패 예외 처리
# ------------------------
def test_delete_file_remove_error_logs(tmp_path):
    # 미리 파일 생성
    p = tmp_path / "k.json"
    p.write_text("{}")

    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)

    with patch("os.remove", side_effect=OSError("cannot delete")):
        mgr.delete("k")  # except 블록 → logger.error

    assert logger.error.called
    msg = logger.error.call_args[0][0]
    assert "삭제 실패" in msg


# ------------------------
# clear: base_dir 미존재 → early return
# ------------------------
def test_clear_base_dir_not_exists_returns_early(tmp_path):
    not_exists = tmp_path / "nope"
    mgr = _mk_manager(str(not_exists))
    # 존재하지 않으면 바로 return (아무 로그/예외 없이) :contentReference[oaicite:7]{index=7}
    mgr.clear()


# ------------------------
# clear: 내부 파일 삭제 중 오류/로그, 전체 예외 처리
# ------------------------
def test_clear_with_delete_error_logs_each_file(tmp_path):
    # .json 파일 2개 생성
    f1 = tmp_path / "a.json"
    f2 = tmp_path / "b.json"
    f1.write_text("{}")
    f2.write_text("{}")

    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)

    # 첫 번째 remove는 실패, 두 번째는 성공하도록 side_effect 설정
    with patch("os.remove", side_effect=[OSError("fail a"), None]):
        mgr.clear()  # 파일 루프 내 except → logger.error 호출

    assert logger.error.called
    # 개별 파일 삭제 실패 메시지 형태 확인
    errors = [call[0][0] for call in logger.error.call_args_list]
    assert any("파일 삭제 실패" in e for e in errors)


def test_clear_top_level_exception_is_logged(tmp_path):
    # os.walk 자체에서 예외 발생시키기 → 바깥 except에서 전체 캐시 삭제 실패 로그
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)

    with patch("os.walk", side_effect=RuntimeError("walk boom")):
        mgr.clear()  # 전체 예외 처리

    assert logger.error.called
    msg = logger.error.call_args[0][0]
    assert "전체 캐시 삭제 실패" in msg


# ------------------------
# get_raw: 정상 로드(리스트 역직렬화 포함) + 에러 로깅
# ------------------------
def test_get_raw_happy_path_and_list_deserialize(tmp_path):
    # {"data": [...] } 형태 파일 생성 → get_raw가 내부적으로 _deserialize 호출
    data = {"data": [{"a": 7, "b": 8}, {"a": 9, "b": 10}]}
    path = tmp_path / "g.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    mgr = _mk_manager(str(tmp_path), classes=[Dummy])
    out = mgr.get_raw("g")  # wrapper['data']를 Dummy 리스트로 역직렬화 :contentReference[oaicite:10]{index=10}
    assert isinstance(out, dict)
    assert "data" in out
    assert all(isinstance(x, Dummy) for x in out["data"])
    assert [x.a for x in out["data"]] == [7, 9]


def test_get_raw_logs_on_json_error(tmp_path):
    # 깨진 JSON 생성 → except 로거 호출
    (tmp_path / "bad.json").write_text("{invalid json", encoding="utf-8")
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)

    out = mgr.get_raw("bad")  # 에러 발생 → None 반환 + logger.error :contentReference[oaicite:11]{index=11}
    assert out is None
    assert logger.error.called
    msg = logger.error.call_args[0][0]
    assert "Load Error" in msg

# ------------------------
# Pydantic Model Support
# ------------------------
def test_serialize_pydantic_model(tmp_path):
    """Pydantic 모델 직렬화 테스트"""
    mgr = _mk_manager(str(tmp_path))
    obj = PydanticDummy(x=1, y="test")
    serialized = mgr._serialize(obj)
    assert serialized == {"x": 1, "y": "test"}

def test_deserialize_pydantic_model(tmp_path):
    """Pydantic 모델 역직렬화 테스트"""
    mgr = _mk_manager(str(tmp_path), classes=[PydanticDummy])
    raw_data = {"x": 10, "y": "restored"}
    obj = mgr._deserialize(raw_data)
    assert isinstance(obj, PydanticDummy)
    assert obj.x == 10
    assert obj.y == "restored"

def test_deserialize_pydantic_nested_in_res_common_response(tmp_path):
    """ResCommonResponse(Pydantic) 내부의 data 필드 재귀 역직렬화 테스트"""
    class ResCommonResponse(BaseModel): # FileCacheManager가 이름을 체크하므로 동일한 이름 사용
        rt_cd: str
        msg1: str
        data: Any = None

    mgr = _mk_manager(str(tmp_path), classes=[ResCommonResponse, PydanticDummy])
    raw = {"rt_cd": "0", "msg1": "ok", "data": {"x": 99, "y": "nested"}}
    
    res = mgr._deserialize(raw)
    assert isinstance(res, ResCommonResponse)
    assert isinstance(res.data, PydanticDummy)
    assert res.data.x == 99

def test_cleanup_old_files(tmp_path):
    """오래된 캐시 파일 삭제 테스트"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)

    # 1. 최신 파일 (유지)
    fresh = tmp_path / "fresh.json"
    fresh.write_text("{}")

    # 2. 오래된 파일 (삭제)
    old = tmp_path / "old.json"
    old.write_text("{}")
    
    # 8일 전으로 수정 시간 변경
    past = time.time() - (8 * 86400)
    os.utime(str(old), (past, past))

    mgr.cleanup_old_files(days=7)

    assert fresh.exists()
    assert not old.exists()
    
    # 로그 확인
    assert logger.debug.called
    logs = [call.args[0] for call in logger.debug.call_args_list]
    assert any("File cache 삭제됨 (기간만료)" in msg for msg in logs)

# ------------------------
# Additional Tests for Coverage
# ------------------------

def test_serialize_dict_recursion(tmp_path):
    """_serialize: 딕셔너리 재귀 직렬화 테스트"""
    mgr = _mk_manager(str(tmp_path))
    data = {"a": {"b": 1, "c": [2, 3]}}
    out = mgr._serialize(data)
    assert out == data

def test_serialize_to_dict_method(tmp_path):
    """_serialize: to_dict 메서드 호출 테스트"""
    class ToDictObj:
        def to_dict(self):
            return {"converted": True}
            
    mgr = _mk_manager(str(tmp_path))
    obj = ToDictObj()
    out = mgr._serialize(obj)
    assert out == {"converted": True}

def test_deserialize_dict_recursion_no_match(tmp_path):
    """_deserialize: 매칭되는 클래스가 없을 때 딕셔너리 재귀 복원 테스트"""
    mgr = _mk_manager(str(tmp_path), classes=[Dummy])
    # Dummy는 a, b 필드를 가짐. c, d는 매칭 안됨.
    raw = {"c": 1, "d": {"e": 2}} 
    out = mgr._deserialize(raw)
    assert out == raw
    assert isinstance(out, dict)

def test_deserialize_field_coverage_check(tmp_path):
    """_deserialize: 필드 커버리지 50% 미만일 때 매칭 건너뛰기 테스트"""
    @dataclass
    class SmallClass:
        a: int

    mgr = _mk_manager(str(tmp_path), classes=[SmallClass])
    
    # raw_data 키가 3개, SmallClass 필드는 1개 (a). 1/3 < 0.5 이므로 매칭 안되어야 함.
    raw_data = {"a": 1, "b": 2, "c": 3}
    
    out = mgr._deserialize(raw_data)
    # 매칭되었다면 SmallClass 인스턴스여야 하지만, 건너뛰었으므로 dict여야 함
    assert isinstance(out, dict)
    assert out["a"] == 1

def test_deserialize_exception_handling(tmp_path):
    """_deserialize: 클래스 매칭 시도 중 예외 발생 시 무시하고 계속 진행 테스트"""
    @dataclass
    class FaultyClass:
        a: int
        
        @classmethod
        def from_dict(cls, d):
            raise ValueError("Intentional Error")

    mgr = _mk_manager(str(tmp_path), classes=[FaultyClass])
    raw_data = {"a": 1}
    
    # FaultyClass.from_dict에서 예외가 발생하지만, _deserialize는 이를 잡고(pass) 
    # 다음 로직(dict 반환)으로 넘어가야 함.
    out = mgr._deserialize(raw_data)
    assert isinstance(out, dict)
    assert out["a"] == 1

def test_cleanup_old_files_base_dir_not_exists(tmp_path):
    """cleanup_old_files: base_dir이 없으면 조기 리턴"""
    not_exists = tmp_path / "nope"
    mgr = _mk_manager(str(not_exists))
    # 에러 없이 리턴되어야 함
    mgr.cleanup_old_files()

def test_cleanup_old_files_remove_error(tmp_path):
    """cleanup_old_files: 파일 삭제 중 에러 발생 시 로깅"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    # 파일 생성 및 시간 조작
    old = tmp_path / "old.json"
    old.write_text("{}")
    past = time.time() - (8 * 86400)
    os.utime(str(old), (past, past))
    
    with patch("os.remove", side_effect=OSError("Delete Fail")):
        mgr.cleanup_old_files()
        
    assert logger.error.called
    assert "오래된 파일 삭제 실패" in logger.error.call_args[0][0]

def test_cleanup_old_files_outer_exception(tmp_path):
    """cleanup_old_files: 전체 로직 중 에러 발생 시 로깅"""
    mgr = _mk_manager(str(tmp_path))
    logger = MagicMock()
    mgr.set_logger(logger)
    
    with patch("os.walk", side_effect=Exception("Walk Fail")):
        mgr.cleanup_old_files()
        
    assert logger.error.called
    assert "캐시 정리 실패" in logger.error.call_args[0][0]

def test_exists(tmp_path):
    """exists: 파일 존재 여부 확인"""
    mgr = _mk_manager(str(tmp_path))
    key = "exist_test"
    
    assert not mgr.exists(key)
    
    mgr.set(key, {"a": 1}, save_to_file=True)
    assert mgr.exists(key)


def test_cleanup_old_files_ohlcv_retention(tmp_path):
    """OHLCV 데이터 별도 보관 기간(1년) 적용 테스트"""
    mgr = _mk_manager(str(tmp_path))
    
    now = time.time()
    day = 86400
    
    # 1. 일반 데이터 (8일 전 -> 삭제 대상)
    f1 = tmp_path / "normal.json"
    f1.write_text("{}")
    os.utime(str(f1), (now - 8 * day, now - 8 * day))
    
    # 2. OHLCV 데이터 (100일 전 -> 유지 대상)
    f2 = tmp_path / "ohlcv_past_005930.json"
    f2.write_text("{}")
    os.utime(str(f2), (now - 100 * day, now - 100 * day))
    
    # 3. OHLCV 데이터 (400일 전 -> 삭제 대상)
    f3 = tmp_path / "ohlcv_past_000660.json"
    f3.write_text("{}")
    os.utime(str(f3), (now - 400 * day, now - 400 * day))
    
    mgr.cleanup_old_files(days=7)
    
    assert not f1.exists()
    assert f2.exists()
    assert not f3.exists()

