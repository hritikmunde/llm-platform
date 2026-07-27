from flask import Flask, request
from kubernetes import client, config as k8s_config
from github import Github, Auth
import boto3
import json as _json
import os
import time
import re

app = Flask(__name__)

# ---- config (env-overridable) ----
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
AWS_REGION       = os.environ.get("AWS_REGION", "us-east-1")
GITHUB_REPO      = os.environ.get("GITHUB_REPO", "hritikmunde/llm-platform")
VLLM_MANIFEST    = os.environ.get("VLLM_MANIFEST", "manifests/vllm.yaml")

# The bounded set of fixes the agent may propose.
# The LLM only CHOOSES one of these; deterministic code makes the actual edit.
ALLOWED_ACTIONS = [
    "lower_gpu_memory_utilization",  # gpu-memory-utilization too high → OOM/crash
    "lower_max_model_len",           # max-model-len too big for the GPU → crash
    "rollback_deployment",           # a bad recent change
    "no_action_needed",              # healthy / transient
]


def load_kube():
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()


def get_pod_logs(deployment, namespace, tail=60):
    """Fetch status + recent logs for the pods of a deployment, selected by label
    (app=<deployment>) so 'vllm' does not accidentally match 'vllm-ui'."""
    load_kube()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(
        namespace=namespace, label_selector=f"app={deployment}"
    ).items
    if not pods:
        return f"No pods found for app={deployment} in namespace '{namespace}'."
    out = []
    for p in pods:
        name = p.metadata.name
        phase = p.status.phase
        # surface waiting/termination reasons (CrashLoopBackOff, OOMKilled, etc.)
        reasons = []
        for cs in (p.status.container_statuses or []):
            if cs.state and cs.state.waiting and cs.state.waiting.reason:
                reasons.append(f"waiting={cs.state.waiting.reason}")
            if cs.state and cs.state.terminated and cs.state.terminated.reason:
                reasons.append(f"terminated={cs.state.terminated.reason}")
            if cs.last_state and cs.last_state.terminated and cs.last_state.terminated.reason:
                reasons.append(f"lastTerminated={cs.last_state.terminated.reason}")
        rtxt = (" [" + ", ".join(reasons) + "]") if reasons else ""
        out.append(f"--- pod {name} (phase={phase}){rtxt} ---")
        # try current logs, fall back to previous (crash-looped) logs
        try:
            logs = v1.read_namespaced_pod_log(name=name, namespace=namespace, tail_lines=tail)
            if not logs:
                logs = v1.read_namespaced_pod_log(
                    name=name, namespace=namespace, tail_lines=tail, previous=True
                )
            out.append(logs or "(no logs)")
        except Exception:
            try:
                logs = v1.read_namespaced_pod_log(
                    name=name, namespace=namespace, tail_lines=tail, previous=True
                )
                out.append(logs or "(no logs)")
            except Exception as e:
                out.append(f"(could not read logs: {e})")
    return "\n".join(out)


def diagnose(alertname, deployment, namespace, summary, context):
    """Send the alert + gathered context to Bedrock and get a structured fix."""
    prompt = f"""You are an SRE remediation agent for a Kubernetes platform serving an LLM with vLLM.
An alert has fired. Diagnose the root cause from the logs and choose ONE fix from the allowed list.

ALERT: {alertname}
DEPLOYMENT: {deployment}
NAMESPACE: {namespace}
SUMMARY: {summary}

GATHERED CONTEXT (pod status and logs):
{context}

ALLOWED ACTIONS (choose exactly one):
{", ".join(ALLOWED_ACTIONS)}

Guidance:
- If the logs show CUDA out of memory / KV cache / GPU memory errors, prefer lower_gpu_memory_utilization.
- If the logs show the model/context length cannot be allocated, prefer lower_max_model_len.
- If the pod is healthy and running normally, choose no_action_needed.

Respond ONLY with a JSON object, no markdown, no code fences, in exactly this shape:
{{"root_cause": "<one sentence>", "action": "<one of the allowed actions>", "reason": "<one sentence why this fix>"}}"""

    client_br = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    resp = client_br.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=_json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    payload = _json.loads(resp["body"].read())
    raw = payload["content"][0]["text"].strip()

    # strip markdown code fences if Claude wrapped the JSON
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw[3:]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
        raw = raw.strip()

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
    """Deterministically edit vllm.yaml for the chosen action.
    The LLM chose the action; this code makes the exact, bounded change."""
    if action == "lower_gpu_memory_utilization":
        # matches an arg pair:  - "--gpu-memory-utilization"\n  - "0.99"
        m = re.search(r'("--gpu-memory-utilization"\s*\n\s*-\s*")([0-9.]+)(")', text)
        if m:
            new_val = "0.85"
            if m.group(2) == new_val:
                return None, "gpu-memory-utilization already at safe value"
            return (text[:m.start()] + f"{m.group(1)}{new_val}{m.group(3)}" + text[m.end():],
                    f"lower gpu-memory-utilization {m.group(2)} -> {new_val}")
        return None, "could not locate gpu-memory-utilization arg"

    if action == "lower_max_model_len":
        m = re.search(r'("--max-model-len"\s*\n\s*-\s*")([0-9]+)(")', text)
        if m:
            new_val = "4096"
            return (text[:m.start()] + f"{m.group(1)}{new_val}{m.group(3)}" + text[m.end():],
                    f"lower max-model-len {m.group(2)} -> {new_val}")
        return None, "could not locate max-model-len arg"

    return None, f"no automatic edit implemented for action '{action}'"


def open_pr(diagnosis, alertname, deployment):
    action = diagnosis.get("action")
    if action in ("no_action_needed", "rollback_deployment"):
        return f"no PR opened (action = {action})"

    gh = Github(auth=Auth.Token(read_token()))
    repo = gh.get_repo(GITHUB_REPO)
    default_branch = repo.default_branch

    file = repo.get_contents(VLLM_MANIFEST, ref=default_branch)
    current_text = file.decoded_content.decode()

    new_text, change_desc = apply_fix_to_manifest(current_text, action)
    if new_text is None:
        return f"no PR opened ({change_desc})"

    branch = f"agent-fix-{action}-{int(time.time())}"
    base_sha = repo.get_branch(default_branch).commit.sha
    repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)

    repo.update_file(
        path=VLLM_MANIFEST,
        message=f"agent: {change_desc} for {alertname}",
        content=new_text,
        sha=file.sha,
        branch=branch,
    )

    body = f"""### Automated remediation by the SRE agent

**Alert:** {alertname}
**Deployment:** {deployment}

**Root cause:** {diagnosis.get('root_cause')}
**Action:** {action}
**Reason:** {diagnosis.get('reason')}

**Change applied:** {change_desc}

---
Diagnosed via AWS Bedrock (Claude Haiku). This PR was opened automatically — review before merging."""

    pr = repo.create_pull(
        title=f"agent: fix {alertname} ({change_desc})",
        body=body, head=branch, base=default_branch,
    )
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
        namespace = labels.get("namespace") or "default"
        severity = labels.get("severity")
        summary = annotations.get("summary")

        print("=" * 50)
        print(f"ALERT RECEIVED  status={status}  alertname={alertname}  deployment={deployment}")
        print("=" * 50)

        if status != "firing":
            print("Alert resolved — no diagnosis needed.")
            continue

        # ignore cluster-control-plane / non-workload alerts with no deployment target
        if not deployment or deployment == "None":
            print("No deployment target in alert — skipping.")
            continue

        try:
            context = get_pod_logs(deployment, namespace)
        except Exception as e:
            context = f"ERROR gathering context: {e}"
        print("---- GATHERED CONTEXT ----")
        print(context[:2000])

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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))