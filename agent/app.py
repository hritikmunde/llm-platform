from flask import Flask, request
from kubernetes import client, config as k8s_config
from github import Github
import requests
import json as _json
import os
import time
import re
import boto3

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

app = Flask(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "hritikmunde/llm-platform")
MANIFEST_PATH = "manifests/stub-model.yaml"

ALLOWED_ACTIONS = [
    "scale_up_replicas",
    "increase_memory_limit",
    "rollback_deployment",
    "adjust_vllm_config",
    "no_action_needed",
]


def load_kube():
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()


def get_pod_logs(deployment, namespace, tail=50):
    load_kube()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace=namespace).items
    matching = [p for p in pods if p.metadata.name.startswith(deployment)]
    if not matching:
        return f"No pods found for deployment '{deployment}' in namespace '{namespace}'."
    out = []
    for p in matching:
        name = p.metadata.name
        phase = p.status.phase
        out.append(f"--- pod {name} (phase={phase}) ---")
        try:
            logs = v1.read_namespaced_pod_log(name=name, namespace=namespace, tail_lines=tail)
            out.append(logs or "(no logs)")
        except Exception as e:
            out.append(f"(could not read logs: {e})")
    return "\n".join(out)


def diagnose(alertname, deployment, namespace, summary, context):
    prompt = f"""You are an SRE remediation agent for a Kubernetes platform.
An alert has fired. Diagnose the root cause and choose ONE fix from the allowed list.

ALERT: {alertname}
DEPLOYMENT: {deployment}
NAMESPACE: {namespace}
SUMMARY: {summary}

GATHERED CONTEXT (pod status and logs):
{context}

ALLOWED ACTIONS (choose exactly one):
{", ".join(ALLOWED_ACTIONS)}

Respond ONLY with a JSON object, no other text, in this exact shape:
{{"root_cause": "<one sentence>", "action": "<one of the allowed actions>", "reason": "<one sentence why this fix>"}}"""

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    resp = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=_json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    payload = _json.loads(resp["body"].read())
    raw = payload["content"][0]["text"]
    try:
        return _json.loads(raw)
    except Exception:
        return {"root_cause": "unparseable", "action": "no_action_needed",
                "reason": f"LLM returned non-JSON: {raw[:200]}"}

def read_token():
    env = os.environ.get("GITHUB_TOKEN")
    if env:
        return env.strip()
    with open(os.path.join(os.path.dirname(__file__), ".github_token")) as f:
        return f.read().strip()


def apply_fix_to_manifest(text, action):
    """Given the manifest text and an action, return (new_text, change_description).
    Only a bounded set of edits is allowed — the LLM cannot write arbitrary YAML."""
    if action == "scale_up_replicas":
        m = re.search(r"(replicas:\s*)(\d+)", text)
        if m:
            current = int(m.group(2))
            new = max(current + 1, 1)
            new_text = text[:m.start()] + f"{m.group(1)}{new}" + text[m.end():]
            return new_text, f"scale replicas {current} -> {new}"
    if action == "increase_memory_limit":
        m = re.search(r"(memory:\s*\")(\d+)(Mi\")", text)
        if m:
            current = int(m.group(2))
            new = current * 2
            new_text = text[:m.start()] + f'{m.group(1)}{new}{m.group(3)}' + text[m.end():]
            return new_text, f"increase memory limit {current}Mi -> {new}Mi"
    # actions with no safe automatic edit yet
    return None, f"no automatic edit implemented for action '{action}'"


def open_pr(diagnosis, alertname, deployment):
    action = diagnosis.get("action")
    if action in ("no_action_needed",):
        return "no PR opened (action = no_action_needed)"

    gh = Github(read_token())
    repo = gh.get_repo(GITHUB_REPO)
    default_branch = repo.default_branch

    # read current manifest from the default branch
    file = repo.get_contents(MANIFEST_PATH, ref=default_branch)
    current_text = file.decoded_content.decode()

    new_text, change_desc = apply_fix_to_manifest(current_text, action)
    if new_text is None:
        return f"no PR opened ({change_desc})"

    # create a new branch
    branch = f"agent-fix-{action}-{int(time.time())}"
    base_sha = repo.get_branch(default_branch).commit.sha
    repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)

    # commit the change to the new branch
    repo.update_file(
        path=MANIFEST_PATH,
        message=f"agent: {change_desc} for {alertname}",
        content=new_text,
        sha=file.sha,
        branch=branch,
    )

    # open the PR with the diagnosis as the description
    body = f"""### Automated remediation by the SRE agent

**Alert:** {alertname}
**Deployment:** {deployment}

**Root cause:** {diagnosis.get('root_cause')}
**Action:** {action}
**Reason:** {diagnosis.get('reason')}

**Change applied:** {change_desc}

---
This PR was opened automatically. Review before merging."""

    pr = repo.create_pull(title=f"agent: fix {alertname} ({change_desc})",
                          body=body, head=branch, base=default_branch)
    return f"PR opened: {pr.html_url}"


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/alert", methods=["POST"])
def alert():
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        return {"error": "invalid json", "detail": str(e)}, 400
    if not payload:
        return {"error": "empty payload"}, 400

    status = payload.get("status")

    for a in payload.get("alerts", []):
        labels = a.get("labels", {})
        annotations = a.get("annotations", {})
        alertname = labels.get("alertname")
        deployment = labels.get("deployment")
        namespace = labels.get("namespace")
        severity = labels.get("severity")
        summary = annotations.get("summary")

        print("=" * 50)
        print(f"ALERT RECEIVED  status={status}  alertname={alertname}  deployment={deployment}")
        print("=" * 50)

        if status != "firing":
            print("Alert resolved — no diagnosis needed.")
            continue

        try:
            context = get_pod_logs(deployment, namespace)
        except Exception as e:
            context = f"ERROR gathering context: {e}"
        print("---- GATHERED CONTEXT ----")
        print(context)

        try:
            diagnosis = diagnose(alertname, deployment, namespace, summary, context)
        except Exception as e:
            diagnosis = {"root_cause": "diagnosis failed", "action": "no_action_needed", "reason": str(e)}
        print("---- DIAGNOSIS ----")
        print(f"  root_cause: {diagnosis.get('root_cause')}")
        print(f"  action    : {diagnosis.get('action')}")
        print(f"  reason    : {diagnosis.get('reason')}")

        try:
            result = open_pr(diagnosis, alertname, deployment)
        except Exception as e:
            result = f"PR step failed: {e}"
        print("---- PR ----")
        print(f"  {result}")
        print("=" * 50)

    return {"received": True}, 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)