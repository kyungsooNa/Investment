# KIS Fixture Notes

`inquire_daily_ccld_output1_*.json` files are automatically included in the
`OrderExecutionReport.from_order_query()` regression tests.

## Add A Fixture

1. If you already have a raw response JSON file, convert it like this.

```powershell
python -m utils.kis_inquire_daily_ccld_fixture_utils `
  --input path\to\raw_inquire_daily_ccld.json `
  --output tests\fixtures\kis\inquire_daily_ccld_output1_paper_captured.json `
  --fixture-name paper_captured `
  --mode paper
```

2. You can also generate a fixture by calling the API directly.

```powershell
python -m utils.kis_inquire_daily_ccld_fixture_utils `
  --output tests\fixtures\kis\inquire_daily_ccld_output1_real_captured.json `
  --fixture-name real_captured `
  --mode real `
  --start-date 20260424 `
  --end-date 20260424 `
  --side-code 00 `
  --stock-code 005930
```

## Default Behavior

- Order numbers and original order numbers are sanitized by default.
- `--mask-stock-code` also masks stock codes.
- `--keep-raw` keeps each row unchanged.
- Generated fixtures are auto-loaded by `tests/unit_test/common/test_types.py`.
