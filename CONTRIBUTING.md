# Contributing to llm-platform

Thanks for your interest! llm-platform is early-stage, so bug reports, ideas and small PRs all genuinely shape where it goes.

## Ways to help

- **Report a bug.** Open an [issue](https://github.com/hritikmunde/llm-platform/issues/new) with what you ran, what you expected, and what happened. Logs from the agent pod (`kubectl logs deploy/agent`) are very helpful.
- **Suggest a failure mode.** Describe an incident the agent should handle: the symptom, the alert that would fire, and the fix a human would make.
- **Send a PR.** Small, focused changes are easiest to review.

## Adding a remediation action

Most new capabilities are new actions. Each action is a pair:

1. A name in `ALLOWED_ACTIONS` in [`agent/app.py`](agent/app.py). This is the only thing the LLM can choose.
2. A deterministic branch in `apply_fix_to_manifest()` that makes the exact, bounded edit to the manifest.

Guidelines:

- The LLM must never generate manifest content. It only picks an action name.
- Edits should be minimal and predictable (change one value, not rewrite a file).
- If the action needs a new signal, add the alert rule in [`manifests/alert-rules.yaml`](manifests/alert-rules.yaml).
- In your PR, describe how you triggered the failure and show the PR the agent opened.

## Development setup

```bash
cd agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Running the full loop needs an EKS cluster (see the [README](README.md#running-it)). A local [kind](https://kind.sigs.k8s.io/) cluster (`kind-config.yaml`) works for testing the agent's webhook and Kubernetes calls without a GPU.

## Ground rules

- Never commit secrets: tokens, kubeconfigs, `*.tfstate`, `.env` files. The `.gitignore` covers the common ones.
- Be kind and constructive in issues and reviews.

Questions? Email [contact@llm-platform.tech](mailto:contact@llm-platform.tech).
