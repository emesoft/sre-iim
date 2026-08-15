# IIM — Intelligent Incident Management

A smart SRE assistant: automatically gathers incident context from AWS, uses an LLM to read and
**reason about the root cause**, and presents everything on a **single pane of glass**.
AWS-native, no third parties (New Relic/Datadog), near-zero running cost.

> EmeSoft Hackathon entry · Sprint Jul 23 – Aug 22 · Demo Day Aug 28

---

## Problem

During an incident, SRE/on-call has to jump between many tools (CloudWatch, ECS console, logs,
deploy history) to understand "what is on fire + why". Slow and effortful under pressure → higher MTTR.

## Solution

IIM automatically gathers the full context when an alert fires, runs it through an AI layer for
analysis, and displays it on one board. On-call opens it and immediately sees a summary, the likely
root cause, and a recommended action — without touching the console.

```
Alert (CloudWatch / Metric filter) → EventBridge → Lambda (read-only context collection)
      → AI analysis layer (Bedrock)  → DynamoDB → Board (React on S3)
                                      → Azure DevOps ticket (one-way)
```

## Repo structure

```
iim/
├── backend/               # FastAPI + LangChain backend (uv), incl. reused Step 0 brain
│   └── ai/                # Incident analysis AI layer (Step 0)
│       ├── analyze_incident.py
│       └── samples/       # Sample context (anonymized, NOT real data)
│           ├── infra_oom.json         # infra case: OOM after a deploy
│           └── apicost_overage.json   # third-party API cost case
├── frontend/              # React (Vite) + shadcn/ui board
├── iac/                   # Terraform later (empty for now)
├── .github/workflows/     # CI + DevSecOps (lint, test, security scan)
└── docker-compose.yml     # full stack: docker compose up --build
```

## Run the AI brain directly (Step 0 — dev harness)

In the full app the analysis runs **inside the backend** (FastAPI) when an incident is ingested — you
don't call it by hand. This standalone invocation is just a dev shortcut to exercise the Step 0 brain on
a sample while the backend is being built:

```bash
pip install boto3
export AWS_REGION=ap-southeast-1        # needs Amazon Bedrock permission

cd backend/ai
python analyze_incident.py samples/infra_oom.json
python analyze_incident.py samples/apicost_overage.json
```

The script runs twice: run 1 calls the LLM (cache miss), run 2 returns instantly from the cache (0 tokens).

**Before running:** open the Bedrock console and enable *Model access* for the model in `MODEL_ID`
(defaults to Haiku for cost). Region ap-southeast-1 may require an inference-profile id.

## Run the full stack (Docker)

From the repo root:

```bash
docker compose up --build     # db + backend :8000 + frontend :5173
# open http://localhost:5173
```

The app and login screen load without credentials, but **AI analysis needs an LLM provider**: set
AWS Bedrock creds (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`) or
`LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY` in a `.env` file at the repo root.

To run the whole SRE flow locally on free-tier LLM keys and a synthetic log source (`DEMO_LOGS=true`
— no AWS account needed), follow **[DEMO.md](DEMO.md)**.

## CI / DevSecOps

`.github/workflows/ci.yml` runs on every PR and push to `main`:

- **backend** — `ruff` lint + `pytest` (against a pgvector service container).
- **frontend** — `tsc` typecheck + Vite build.
- **security** — Gitleaks (secrets), Semgrep (SAST), Trivy filesystem (SCA + misconfig), Hadolint
  (Dockerfile lint). Findings go to the **Security** tab (SARIF).

**Gate:** a leaked secret fails the pipeline; lower-severity SAST/SCA findings are reported but do
not block. (Docker image build + publish is not part of CI for now.)

## ⚠️ Security — read carefully

- **Do NOT commit secrets** (Azure DevOps PAT, AWS keys, tokens). Keep them in AWS Secrets Manager
  or environment variables. `.gitignore` already blocks the sensitive files.
- **Do NOT use real customer data.** Every sample in `backend/ai/samples/` is fake/anonymized. For real
  testing, use a self-built sandbox that generates fake incidents (see Step 3 in the build plan).

## Cost

The serverless infrastructure (Lambda + DynamoDB + S3) is nearly free because it only runs when an
incident occurs. The only meaningful cost is LLM calls — controlled by: calling only for
CRITICAL/threshold-breaching incidents, compact filtered input, and cache-first + dedup to avoid
duplicate calls. At internal scale: under a few tens of dollars per month.
