# Style and conventions
- Python codebase with strict typing: `mypy` strict mode enabled.
- Ruff is configured with line length 100 and lint rules `E,F,I,W,B,UP`; `E501` ignored.
- Tests use pytest and classify cases with `@pytest.mark.small`, `medium`, `large`.
- Development workflow is issue-driven: design -> review/fix/verify -> implement -> review/fix/verify -> doc-check -> PR -> close.
- Design docs must include Primary Sources, What/Constraint focus, minimal implementation detail, and test strategy phrased as verification perspectives rather than raw test lists.