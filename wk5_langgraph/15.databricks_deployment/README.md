# 15. Databricks Deployment

Deploy a LangGraph agent as a **Databricks Model Serving endpoint** — packaging the graph with MLflow and serving it via the same OpenAI-compatible API used throughout the course.

## How Deployment Works

Databricks Model Serving lets you host any MLflow-logged model behind a managed REST endpoint. The deployment pipeline for a LangGraph agent has four stages:

1. **Define the agent (`agent.py`)**
   The agent graph (nodes, edges, tools, LLM binding) lives in a single self-contained Python file. At the bottom of this file, `mlflow.models.set_model(graph)` declares which object MLflow should serialize. The file reads `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and `DATABRICKS_MODEL` from environment variables — at serving time, Databricks injects these automatically via endpoint environment configuration.

2. **Log to MLflow (models-from-code)**
   Rather than pickling the graph, MLflow uses *models-from-code* logging: it stores `agent.py` as a source artifact and re-executes it at load time. The call is:
   ```python
   mlflow.langchain.log_model(lc_model="agent.py", name="langgraph_agent", ...)
   ```
   This records the model artifact, its dependencies (pip requirements), and an input/output signature in an MLflow experiment run — either locally or on a Databricks-hosted MLflow tracking server.

3. **Register in Unity Catalog**
   The logged model is promoted to a versioned entry in Unity Catalog (e.g. `main.default.cs4603_langgraph_agent`). Unity Catalog provides governance (ACLs, lineage, audit logs) and makes the model addressable by the serving layer. Registration uses:
   ```python
   mlflow.register_model(model_uri=model_info.model_uri, name="main.default.cs4603_langgraph_agent")
   ```

   **Why this step matters:** If you only run your agent locally (no Databricks deployment), you don't need `register_model` at all — `log_model` already saves the artifact and you can load it back with `mlflow.langchain.load_model(model_uri)` for local testing. Registration is specifically for deployment: it publishes the model into Unity Catalog's registry so that Databricks Model Serving can discover it, pull the artifact, and spin up a container to serve it. Think of `log_model` as saving a snapshot and `register_model` as publishing that snapshot to a shared catalog where the serving infrastructure can find it.

4. **Create a Model Serving endpoint**
   A serving endpoint wraps a registered model version behind an auto-scaling container. Databricks pulls the model artifact from Unity Catalog, installs its dependencies, imports `agent.py`, and exposes the graph via an **OpenAI-compatible chat completions API** (`POST /serving-endpoints/<name>/invocations`). Key settings:
   - **Workload size:** `Small` (1 replica, suitable for dev/test)
   - **Scale to zero:** enabled — no cost when idle, cold-starts in ~60 seconds
   - **Environment variables:** `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_MODEL` are injected so the agent's internal LLM client can call other model-serving endpoints on the same workspace

   **What happens behind the scenes when you create an endpoint:**

   When you call `serving-endpoints create`, Databricks kicks off an asynchronous provisioning pipeline. This is what takes time (typically **3-8 minutes** for the first deployment):

   1. **Container allocation** — Databricks provisions a container on its managed infrastructure with the requested workload size.
   2. **Dependency installation** — The platform reads the `requirements.txt` / conda environment captured by MLflow during `log_model` and installs all Python packages (LangChain, LangGraph, OpenAI SDK, etc.) into the container.
   3. **Model loading** — Databricks imports `agent.py` (the models-from-code artifact). This executes the file top-to-bottom: it creates the `ChatOpenAI` LLM client (using the injected environment variables), defines the tools, builds the LangGraph `StateGraph`, compiles it, and calls `mlflow.models.set_model(graph)` to register the servable object.
   4. **Health check** — The platform verifies the model loaded successfully and the container can accept requests. If `agent.py` fails to import (e.g. missing env vars, import errors), the endpoint enters `DEPLOYMENT_FAILED` state.
   5. **Ready** — Once healthy, the endpoint transitions to `READY` and begins accepting inference requests.

   You can monitor this with `databricks serving-endpoints get <name>`. The `state.ready` field shows `NOT_READY` during provisioning and `READY` once live. The `state.config_update` field shows `IN_PROGRESS` while a deployment or update is running.

   After the endpoint scales to zero (due to inactivity), the next request triggers a **cold start** that repeats steps 1-5 but takes only **60-90 seconds** since container images and packages are cached.

Once live, any OpenAI-compatible client (Python `openai` SDK, `curl`, Streamlit app) can call the endpoint identically to how it calls foundation models — the agent's tool-calling loop runs server-side and returns a final assistant message.

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Self-contained LangGraph CS4603 study assistant with 4 tools (calculate, temperature conversion, text analysis, course topic lookup). MLflow serializes this file directly via models-from-code. |
| `deploy_setup.sh` | **CLI deployment script (bash)** — uses Databricks CLI for model registration and endpoint management. Requires Linux/macOS/WSL. |
| `deploy_setup.ps1` | **CLI deployment script (Windows PowerShell)** — same as the bash script but runs natively in PowerShell. |
| `deploy_setup.py` | Python deployment script — alternative to the shell/batch scripts; uses the Python SDK. |
| `deployment.ipynb` | Interactive notebook walkthrough of the full deployment pipeline (define → log → test → register → serve → call). |
| `streamlit_app.py` | Chat UI to talk to the deployed serving endpoint via the OpenAI-compatible API. |

## Prerequisites

1. **Databricks CLI (v1.x, the new Go-based CLI)** installed and authenticated:
   ```bash
   # Windows (recommended):
   winget install --id Databricks.DatabricksCLI -e

   # macOS / Linux:
   brew tap databricks/tap && brew install databricks

   databricks auth login --host https://<your-workspace>.databricks.com
   ```

   > **Do not use `pip install databricks-cli`.** That installs the deprecated
   > legacy CLI (v0.18.x), which lacks the `serving-endpoints` command and will
   > fail with `Error: No such command 'serving-endpoints'`. If a legacy
   > `databricks.exe` is present in your venv (`.venv-cs4603\Scripts\`), remove it
   > so it doesn't shadow the real CLI. Note also that the Databricks VS Code
   > extension bundles its own CLI, but that is only on PATH inside VS Code's
   > integrated terminal — installing the standalone CLI above makes `databricks`
   > available in every terminal. Confirm with `databricks --version` (expect v1.x).

2. **Python environment** with project dependencies:
   ```bash
   uv venv -n .venv-cs4603
   .venv-cs4603\Scripts\activate        # Windows
   source .venv-cs4603/bin/activate     # macOS/Linux
   uv pip install -r requirements.txt
   ```

3. **`.env` file** at the repo root with:
   ```
   DATABRICKS_TOKEN="dapi..."
   DATABRICKS_HOST="https://<workspace-id>.databricks.com"
   DATABRICKS_MODEL="databricks-qwen35-122b-a10b"
   ```

4. **Authenticate and set up a CLI profile** for the workspace you want to
   deploy to. Do this **before** running the deployment script:
   ```bash
   # Log in and create a named profile (interactive browser login)
   databricks auth login --host https://<your-workspace>.databricks.com --profile my-profile

   # Verify the profile works
   databricks auth profiles
   databricks current-user me --profile my-profile
   ```
   The deployment scripts use `.env` by default (so students can run the
   notebooks unchanged), but you can force deployment to use this profile with
   the `--profile` flag (see below). The profile takes precedence over the
   `DATABRICKS_HOST`/`DATABRICKS_TOKEN` values in `.env`.

## Deployment Steps

Three options are provided — pick whichever suits your environment:

| Option | Script | Platform | Best for |
|--------|--------|----------|----------|
| **A** | `deploy_setup.sh` | Linux / macOS / WSL | CLI-first workflow, CI/CD pipelines |
| **A-win** | `deploy_setup.ps1` | Windows (native PowerShell) | Windows CLI-first workflow |
| **B** | `deploy_setup.py` | Any (Windows, macOS, Linux) | Python-native workflow |
| **C** | `deployment.ipynb` | Any (VS Code or Databricks) | Learning — step-by-step with explanations |

All four perform the same steps (sanity-check → log → register → serve) and produce the same endpoint.

---

### Option A — Shell Script (Databricks CLI)

The recommended approach. **Requires Linux, macOS, or WSL** — this is a bash script and will not run natively in PowerShell or Windows Command Prompt. On Windows, use WSL or Git Bash, or use Option B (Python script) instead.

Run from the **repo root**:

```bash
bash wk5_langgraph/15.databricks_deployment/deploy_setup.sh
```

**What it does:**

| Step | Action | Tool |
|------|--------|------|
| 1 | Resolve your Databricks username | `databricks current-user me` |
| 2 | Log the agent model to MLflow | Python (minimal inline — no CLI equivalent) |
| 3 | Register the model in Unity Catalog | `databricks registered-models create` / `databricks model-versions create` |
| 4 | Create or update the serving endpoint | `databricks serving-endpoints create` / `update-config` |

**Options:**

```bash
# Custom model name and endpoint:
bash wk5_langgraph/15.databricks_deployment/deploy_setup.sh \
    --model-name main.default.my_agent \
    --endpoint-name my-agent-endpoint

# Skip endpoint creation (just log + register):
bash wk5_langgraph/15.databricks_deployment/deploy_setup.sh --skip-endpoint
```

### Option A-win — PowerShell Script (Databricks CLI)

Same as Option A but runs natively in Windows PowerShell — no WSL/Git Bash required. Reads credentials from `.env` automatically.

Run from the **repo root**:

```powershell
.\wk5_langgraph\15.databricks_deployment\deploy_setup.ps1
```

**Options:**

```powershell
# Custom model name and endpoint:
.\wk5_langgraph\15.databricks_deployment\deploy_setup.ps1 -ModelName main.default.my_agent -EndpointName my-agent-endpoint

# Skip endpoint creation (just log + register):
.\wk5_langgraph\15.databricks_deployment\deploy_setup.ps1 -SkipEndpoint
```

### Option B — Python Script

Make sure you have authenticated and created a CLI profile first (see
Prerequisites step 4).

```bash
# --api-key is REQUIRED: a PAT for the target workspace's serving endpoints.
python wk5_langgraph/15.databricks_deployment/deploy_setup.py --api-key dapi...
python wk5_langgraph/15.databricks_deployment/deploy_setup.py --api-key dapi... --model-name my_agent --skip-endpoint

# Deploy using a specific Databricks CLI profile instead of .env:
python wk5_langgraph/15.databricks_deployment/deploy_setup.py --profile my-profile --api-key dapi...
```

The `--profile` flag routes both the Databricks SDK and MLflow
(tracking + Unity Catalog registry) through the named profile in
`~/.databrickscfg`, overriding the `.env` credentials for that run.
The `--api-key` flag is **required** and supplies the personal access token
(PAT) the agent's LLM client uses to call the target workspace's model serving
endpoints — needed because an OAuth profile (`databricks auth login`) has no
static token for model inference.

### Option C — Interactive Notebook

Open `deployment.ipynb` in VS Code or Databricks and run cells sequentially. This is best for learning — each step is explained inline.

## Architecture

### Agent Graph

```mermaid
graph LR
    START((Start)) --> assistant
    assistant -->|tool_calls?| tools
    assistant -->|no tool calls| END((End))
    tools --> assistant

    subgraph tools [Tools]
        direction TB
        calculate
        convert_temperature
        analyze_text
        lookup_cs4603_topic
    end
```

### Deployment Pipeline

```mermaid
graph TD
    A["agent.py<br/><i>LangGraph definition</i>"] -->|mlflow.langchain.log_model| B["MLflow Experiment<br/><i>logged model artifact</i>"]
    B -->|register_model / CLI| C["Unity Catalog<br/><i>registered model version</i>"]
    C -->|serving-endpoints create / CLI| D["Model Serving Endpoint<br/><i>OpenAI-compatible REST API</i>"]
    D -->|POST /invocations| E["Client<br/><i>openai.OpenAI / curl</i>"]
```

## Verifying the Deployment

**Check endpoint status:**
```bash
databricks serving-endpoints get cs4603-langgraph-agent
```

**Test with curl:**
```bash
curl -X POST "${DATABRICKS_HOST}/serving-endpoints/cs4603-langgraph-agent/invocations" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Convert 100F to Celsius"}]}'
```

**Test with Python:**
```python
import openai

client = openai.OpenAI(
    api_key=DATABRICKS_TOKEN,
    base_url=f"{DATABRICKS_HOST}/serving-endpoints",
)

resp = client.chat.completions.create(
    model="cs4603-langgraph-agent",
    messages=[{"role": "user", "content": "What is RAG in the context of LLMs?"}],
)
print(resp.choices[0].message.content)
```

**Chat UI (Streamlit):**

```bash
streamlit run wk5_langgraph/15.databricks_deployment/streamlit_app.py
```

Set the host, token, and endpoint name in the sidebar (defaults are read from
`.env`). Point them at the workspace where the endpoint is deployed — if you
deployed with `--profile`, use that workspace's host and a PAT for it.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `DATABRICKS_HOST must be set` | Create a `.env` file at the repo root (see Prerequisites) |
| `databricks: command not found` | Install the standalone CLI: `winget install --id Databricks.DatabricksCLI -e` (Windows) or `brew install databricks` (macOS/Linux), then `databricks auth login`. Reopen your terminal to refresh PATH. |
| `Error: No such command 'serving-endpoints'` | You're on the deprecated legacy CLI (v0.18.x). Remove any pip `databricks-cli` / `.venv-cs4603\Scripts\databricks.exe`, install the standalone v1.x CLI, and verify with `databricks --version`. |
| Endpoint stuck in `NOT_READY` | Wait a few minutes — first cold start takes time; check logs in Databricks UI under Serving |
| `PERMISSION_DENIED` on UC | Ask your workspace admin for `USE CATALOG` + `CREATE MODEL` on the target catalog/schema |
| Model logging fails | Ensure `agent.py` imports cleanly: `python -c "import importlib.util; s=importlib.util.spec_from_file_location('a','agent.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"` |
