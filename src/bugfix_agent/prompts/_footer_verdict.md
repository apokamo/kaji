---

## Pre-VERDICT Check

### Evidence Quality Check

Evidence MUST include **specific judgment basis**:

**Good examples:**
- "The 'Reproduction Steps' section in Issue body contains steps 1-3 with evidence file names"
- "Root cause hypothesis points to mapper.py:452, confirmed no NaT handling at that line"
- "Test case list includes 3 normal cases, 2 error cases, with boundary values"

**Bad examples:**
- "No problem" / "Sufficient" / "Appropriate" (too abstract)
- "Checked the Issue body" (unclear what was checked)

---

## VERDICT Output Rules

1. Output the **exact same VERDICT** to both GitHub and stdout
2. Do **NOT** add any text after the VERDICT block
3. Final output MUST **end** with the `## VERDICT` block

```markdown
## VERDICT
- Result: <use the valid options defined in your main prompt>
- Reason: <judgment reason>
- Evidence: <specific judgment basis (abstract expressions prohibited)>
- Suggestion: <suggested next action>
```

---
IMPORTANT: After posting to GitHub, print the exact same VERDICT block to stdout and STOP.
The final output MUST end with the `## VERDICT` block. Do not output these instructions or any additional text after VERDICT.
