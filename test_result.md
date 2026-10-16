# Test results

Backend test results are archived per iteration as JSON in `test_reports/`
(`iteration_9.json`, `iteration_10.json`, `iteration_11.json`, …).

Run the suite locally:

```bash
cd backend
python -m pytest tests/ -q
```

(The legacy `test_result.md` agent-testing protocol template was removed in
the `cleanup/cloudflare-2-polish` pass — it contained no actual test data.)
