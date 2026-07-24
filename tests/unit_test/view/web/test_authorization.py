import pytest

from view.web.authorization import (
    ADMIN,
    OPERATOR,
    VIEWER,
    required_role_for_request,
    role_allows,
)


@pytest.mark.parametrize(
    ("actual", "required", "allowed"),
    [
        (VIEWER, VIEWER, True),
        (VIEWER, OPERATOR, False),
        (OPERATOR, VIEWER, True),
        (OPERATOR, OPERATOR, True),
        (OPERATOR, ADMIN, False),
        (ADMIN, ADMIN, True),
    ],
)
def test_role_hierarchy(actual, required, allowed):
    assert role_allows(actual, required) is allowed


@pytest.mark.parametrize(
    ("path", "method", "required"),
    [
        ("/api/stock/005930", "GET", VIEWER),
        ("/api/ranking/ytd", "GET", VIEWER),
        ("/api/balance", "GET", OPERATOR),
        ("/api/order", "POST", OPERATOR),
        ("/api/operator/alerts/x/resolve", "POST", OPERATOR),
        ("/api/system/shutdown", "POST", ADMIN),
        ("/api/background/ranking/force-update", "POST", ADMIN),
        ("/api/scheduler/start", "POST", ADMIN),
        ("/api/position-sizing/limits", "GET", ADMIN),
        ("/api/kill-switch/reset", "POST", ADMIN),
        ("/api/environment", "POST", ADMIN),
    ],
)
def test_required_role_matrix(path, method, required):
    assert required_role_for_request(path, method) == required
