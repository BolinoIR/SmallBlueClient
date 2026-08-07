# Contributing to SmallBlueClient

## Fast local workflow

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[docs,dev]"
pre-commit install
python -m unittest discover -s tests -q
ruff check sbc tests
python -m sphinx -W -b html docs docs/_build/html
```

## Rules for changes

- Keep the public API Pythonic, typed, and enum-first.
- Preserve independent session identity: server + meeting + captured browser session.
- Never commit `.sbc` files, cookies, GraphQL payloads containing credentials,
  media fixtures, exports, or diagnostics with secrets.
- Every new controller/action needs a unit test and a documentation example.
- Record BBB version, deployment/media backend, role, and sanitized error when
  reporting interoperability issues.
- Do not treat an observed behavior from one BBB deployment as universal.

## Compatibility reports

Use the compatibility issue form. Helpful reports include the output of:

```powershell
sbc validate meeting.sbc --json
sbc diagnose meeting.sbc --json
sbc endurance meeting.sbc --minutes 5 --output report.json
```

Remove names, tokens, cookies, meeting IDs, and private chat contents before
attaching a report.
