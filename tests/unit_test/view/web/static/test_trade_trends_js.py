import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "view" / "web" / "static" / "js" / "trade_trends.js"


def run_trade_trends_js(expression: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable is required for trade_trends.js unit tests")
    code = f"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync({json.dumps(str(SCRIPT))}, 'utf8');
const sandbox = {{ module: {{ exports: {{}} }}, console }};
sandbox.exports = sandbox.module.exports;
vm.runInNewContext(source, sandbox, {{ filename: 'trade_trends.js' }});
const result = (() => {{
{expression}
}})();
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [node, "-e", code],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_chart_model_defaults_to_latest_published_phase_and_uses_daily_average():
    result = run_trade_trends_js(
        """
const { buildTradeTrendChartModel } = sandbox.module.exports;
const rows = [
  {
    period_label: '2026년 8월 1~10일',
    phase: 'customs_10d',
    published_at: '2026-08-11',
    export_amount_100m_usd: 213,
    export_daily_avg_100m_usd: 30.4,
    import_amount_100m_usd: 999,
    semiconductor_export_amount_100m_usd: 100,
    semiconductor_daily_avg_100m_usd: 14.3,
    working_days_current: 7
  },
  {
    period_label: '2026년 8월 1~20일',
    phase: 'customs_20d',
    published_at: '2026-08-21',
    export_amount_100m_usd: 360,
    export_daily_avg_100m_usd: 32,
    import_amount_100m_usd: 980,
    semiconductor_export_amount_100m_usd: 160,
    working_days_current: 10
  }
];
const model = buildTradeTrendChartModel(rows, null);
return {
  selectedPhase: model.selectedPhase,
  labels: model.items.map((item) => item.label),
  exportDaily: model.items[0].export_daily_avg_100m_usd,
  chipDaily: model.items[0].semiconductor_daily_avg_100m_usd,
  maxValue: model.maxValue
};
"""
    )

    assert result == {
        "selectedPhase": "customs_20d",
        "labels": ["2026.08"],
        "exportDaily": 32,
        "chipDaily": 16,
        "maxValue": 32,
    }


def test_quarter_model_sums_monthly_rows_and_derives_working_day_average():
    result = run_trade_trends_js(
        """
const { buildTradeTrendChartModel } = sandbox.module.exports;
const rows = [
  {
    period_label: '2026년 7월',
    phase: 'customs_monthly',
    published_at: '2026-08-01',
    export_amount_100m_usd: 608,
    import_amount_100m_usd: 542,
    semiconductor_export_amount_100m_usd: 242,
    working_days_current: 20
  },
  {
    period_label: '2026년 8월',
    phase: 'customs_monthly',
    published_at: '2026-09-01',
    export_amount_100m_usd: 592,
    import_amount_100m_usd: 530,
    semiconductor_export_amount_100m_usd: 238,
    working_days_current: 20
  }
];
const model = buildTradeTrendChartModel(rows, 'quarter');
const item = model.items[0];
return {
  selectedPhase: model.selectedPhase,
  label: item.label,
  exportAmount: item.export_amount_100m_usd,
  importAmount: item.import_amount_100m_usd,
  chipAmount: item.semiconductor_export_amount_100m_usd,
  workingDays: item.working_days_current,
  exportDaily: item.export_daily_avg_100m_usd,
  chipDaily: item.semiconductor_daily_avg_100m_usd
};
"""
    )

    assert result == {
        "selectedPhase": "quarter",
        "label": "2026 Q3",
        "exportAmount": 1200,
        "importAmount": 1072,
        "chipAmount": 480,
        "workingDays": 40,
        "exportDaily": 30,
        "chipDaily": 12,
    }


def test_month_progress_model_scales_nested_phase_cards_by_daily_average():
    result = run_trade_trends_js(
        """
const { buildTradeTrendChartModel } = sandbox.module.exports;
const rows = [
  {
    period_label: '2026년 8월 1~10일',
    phase: 'customs_10d',
    published_at: '2026-08-11',
    export_amount_100m_usd: 213,
    export_daily_avg_100m_usd: 30.4,
    import_amount_100m_usd: 195,
    semiconductor_export_amount_100m_usd: 100,
    semiconductor_daily_avg_100m_usd: 14.3,
    working_days_current: 7
  },
  {
    period_label: '2026년 8월 1~20일',
    phase: 'customs_20d',
    published_at: '2026-08-21',
    export_amount_100m_usd: 400,
    export_daily_avg_100m_usd: 31.2,
    import_amount_100m_usd: 370,
    semiconductor_export_amount_100m_usd: 180,
    semiconductor_daily_avg_100m_usd: 14.1,
    working_days_current: 12.8
  }
];
const model = buildTradeTrendChartModel(rows, 'month_progress');
return {
  selectedPhase: model.selectedPhase,
  monthCount: model.items.length,
  nestedCount: model.items[0].progressRows.length,
  maxValue: model.maxValue
};
"""
    )

    assert result == {
        "selectedPhase": "month_progress",
        "monthCount": 1,
        "nestedCount": 2,
        "maxValue": 31.2,
    }
