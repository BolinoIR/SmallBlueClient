# Python-only release

This release contains only the publishable Python toolkit:

- the `sbc` package and its type information;
- examples, tests, source-schema generation tools, and Sphinx documentation;
- the project icon used by the package documentation.

The Chrome-extension implementation is intentionally excluded while it is being
reworked. SBC sessions already exported by a prior extension build continue to
load through `sbc.client("meeting.sbc")`.

## Publishing layout

The repository `.gitignore` excludes browser-extension files, local sessions,
BigBlueButton source checkouts, build outputs, and editor state. The Python
release is built from `pyproject.toml` and contains no dependency on
`actions.json` or extension files: action definitions are embedded in
`sbc.operations` at runtime.

```powershell
python -m build --wheel
python -m pip install dist\smallblueclient-*.whl
```

Release wheels built with `SBC_BBB_SCHEMA` freeze the full generated BBB schema
catalog and all wheels embed the generated action type signatures. A public
Python-only checkout still builds with SBC's compact compatibility catalog; set
`SBC_BBB_SCHEMA` when producing a full catalog release. Build source and
documentation before publishing:

```powershell
python -m unittest discover -s tests -q
sphinx-build -E -W -b html docs docs/_build/html
```

Project home: <https://github.com/BolinoIR/SmallBlueClient>
