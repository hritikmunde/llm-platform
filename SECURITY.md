# Security policy

llm-platform is early-stage software and has no supported release branches yet. Fixes land on `master`.

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Email **[contact@llm-platform.tech](mailto:contact@llm-platform.tech)** with:

- a description of the issue and its impact
- steps to reproduce, or a proof of concept
- any suggested fix

You'll get an acknowledgement within a few days. Please give us reasonable time to fix the problem before you disclose it publicly.

Areas of particular interest: the agent's RBAC scope, the handling of its GitHub token, prompt injection through pod logs, and anything that would let the agent make changes outside its allowed action set.
