"""AI 응답 앞쪽의 '신호: 상|중|하' 줄 파싱 테스트."""
from services.ai_signal import extract_signal


def test_extracts_signal_and_strips_line():
    signal, text = extract_signal("신호: 상\n한줄 요약: 데이터가 우호적입니다.")

    assert signal == "상"
    assert text == "한줄 요약: 데이터가 우호적입니다."


def test_tolerates_markdown_and_spacing_variants():
    assert extract_signal("**신호: 중**\n본문")[0] == "중"
    assert extract_signal("  신호 : 하\n본문")[0] == "하"
    assert extract_signal("- 신호: 상\n본문")[0] == "상"


def test_returns_none_without_signal_line():
    body = "한줄 요약: 신호 줄이 없는 응답"
    assert extract_signal(body) == (None, body)


def test_does_not_confuse_section_headings_with_signal():
    """뉴스 검토 본문의 '긍정 신호'/'위험 신호' 섹션 제목은 신호로 오인하면 안 된다."""
    body = "한줄 요약: 요약\n긍정 신호: 상당한 수주\n위험 신호: 하락 우려"
    assert extract_signal(body) == (None, body)


def test_ignores_signal_line_beyond_first_lines():
    """프롬프트는 첫 줄 출력을 요구하므로 본문 깊숙한 곳의 유사 표현은 무시한다."""
    body = "한줄 요약: 요약\n주요 이슈: 이슈\n추가 확인: 확인\n신호: 상"
    assert extract_signal(body) == (None, body)


def test_does_not_match_longer_words_after_value():
    assert extract_signal("신호: 상승세 지속\n본문") == (None, "신호: 상승세 지속\n본문")


def test_handles_empty_and_none():
    assert extract_signal("") == (None, "")
    assert extract_signal(None) == (None, "")
