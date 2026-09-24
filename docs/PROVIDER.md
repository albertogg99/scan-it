# Model provider and probes

The backend accepts an OpenAI-compatible streaming chat endpoint. Defaults are `LLM_BASE_URL=https://api.publicai.co/v1`, `LLM_MODEL=swiss-ai/apertus-v1.5-70b-thinking`, and `LLM_IMAGE_INPUT=unknown`. A missing key permits startup but message submission returns `llm_not_configured`. Backend key precedence is `LLM_API_KEY`, then `SWISSCOM_API_KEY`, then `PUBLIC_AI_API_KEY`; Compose also forwards `SWISSCOM_API_KEY` as `LLM_API_KEY` when the generic key is empty. These are server-side secrets, never frontend `PUBLIC_*` settings.

## Verified native probe observations

Sanitized [fixtures](../probe-samples/) from a previous native Swisscom probe target `https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1` with model `swiss-ai/Apertus-v1.5-70B`. That key was historically held under the `PUBLIC_AI_API_KEY` variable name; the name did **not** mean that the Public AI endpoint accepted it. A request to the default Public AI endpoint with that key returned HTTP 401. Swisscom text, tool and image requests returned HTTP 200; image response identified the probe PNG color. Reasoning and `reasoning_kwargs` returned HTTP 429, so reasoning output/format is **not proven**. The current [summary](../probe-samples/summary.json) contains only those later reasoning retries: do not interpret it as a full all-case report. Other endpoints, models, accounts and rate limits may behave differently.

Set the provider values in your existing `.env` for Compose (create it from `.env.example` only if absent; edit keys locally, do not commit them):

```dotenv
LLM_BASE_URL=https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1
LLM_MODEL=swiss-ai/Apertus-v1.5-70B
SWISSCOM_API_KEY=<YOUR-SWISSCOM-KEY>
LLM_API_KEY=
PUBLIC_AI_API_KEY=
LLM_IMAGE_INPUT=supported
```

`LLM_IMAGE_INPUT=supported` is an **optional Swisscom override** justified only by the recorded image probe for that endpoint/model. Leave it `unknown` for an unverified provider: image submission is then rejected, rather than silently attempting an unsupported call. Do not claim reasoning support from the 429 responses. For native backend, set `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` and, only for that verified Swisscom configuration if desired, `LLM_IMAGE_INPUT=supported` in the backend PowerShell terminal. `SWISSCOM_API_KEY` alone is accepted by the backend, but the probe script reads only `LLM_API_KEY` or `PUBLIC_AI_API_KEY`.

Run a **new** probe locally from the repository root with Python 3.12/uv and your secret in your environment or `.env` (the script reads `.env` without overriding exported variables):

```powershell
uv run --project backend --python 3.12 python scripts/provider_probe.py text tool image reasoning reasoning_kwargs
```

The script writes sanitized per-case JSONL in `probe-samples/` and rewrites `probe-samples/summary.json` with **only the cases passed in that invocation**. A subset retry destroys the prior combined summary, but other per-case files remain until individually probed again. Review HTTP statuses, chunk types, finish reasons and image output in the corresponding fixture; a 200 alone does not prove all model features. The probe does not print the key, but raw provider error bodies and generated text may be sensitive; inspect before sharing. The probe injects the OS trust store if installed for TLS-inspecting networks. For backend outbound HTTPS, set `USE_OS_TRUSTSTORE=true` only if the local OS trust store is actually needed; never disable certificate verification (`verify=False`).