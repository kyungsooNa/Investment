# tests/integration_test/ctx.py
from typing import Any

# conftest의 autouse 픽스처가 런타임에 값을 주입합니다.
ki: Any = None
spy_get: Any = None
spy_post: Any = None
expected_url_for_quotations: Any = None
expected_url_for_account: Any = None
patch_post_with_hash_and_order: Any = None
extract_src_from_balance_payload: Any = None   # ← 추가
resolve_trid: Any = None   # ← 추가
to_int: Any = None                              # ← 추가
make_http_response: Any = None                              # ← 추가
