<!--
Never include taxpayer data in this description — no full SSN, no real names,
no attached tax document. GitHub retains it.
-->

## What this changes, and why

<!-- Explain the why. If this fixes a wrong number, say which direction it was
     wrong in and who it affected. -->

## Verification

<!-- What you ran, and what it printed. "Tests pass" is not verification;
     the actual count the run printed — `2621 passed, 2 skipped` — is. -->

- [ ] `source .venv/bin/activate && python3 -m pytest tests/ -q` passes locally
- [ ] Added tests for new behavior, with worksheet comments on hand-computed values

## Checklist

- [ ] No taxpayer data anywhere in the diff or this description
- [ ] No runtime files committed (profile, result, documents, summaries, generated PDFs, tax-law cache)
- [ ] No hardcoded tax rate, bracket, cap, or threshold — figures are fetched and cached
- [ ] No AI assistant credited as an author, in commits, title, body, or files
- [ ] Dependency changes edit `requirements.in` and regenerate `requirements.txt` with the documented flags
- [ ] Documentation updated if behavior changed

## Bias direction

<!-- Required if this touches agents/lib/. When data is missing or ambiguous,
     which way does this code fail? A fallback that OVERSTATES the number a
     taxpayer acts on is a hard error; one that understates it degrades with a
     loud banner. State which case applies, or "not applicable — no fallback
     path added". -->
