# Model provider and probes

The backend accepts an OpenAI-compatible streaming chat endpoint. Defaults are `LLM_BASE_URL=https://api.publicai.co/v1`, `LLM_MODEL=swiss-ai/apertus-v1.5-70b-thinking`, and `LLM_IMAGE_INPUT=unknown`. A missing key permits startup but message submission returns `llm_not_configured`. Backend key precedence is `LLM_API_KEY`, then `SWISSCOM_API_KEY`, then `PUBLIC_AI_API_KEY`; Compose also forwards `SWISSCOM_API_KEY` as `LLM_API_KEY` when the generic key is empty. These are server-side secrets, never frontend `PUBLIC_*` settings.

## Verified native probe observations

The deployed Swisscom Apertus endpoint rejects canonical tool histories containing empty argument objects with HTTP 400 `Invalid tool call`; nonempty calls also require a `name` on tool-result messages. For Swisscom Apertus only, the adapter adds result names and sends empty argument objects as `{"_no_arguments": true}` so the round stays a structured tool call (flattening it to text taught the model to fake tool calls in prose). The model echoes that key on later calls, so it is stripped from every model-emitted call before validation. Rounds with malformed or non-object arguments are still converted into a completed-result message, without synthetic tool-request JSON. Results stay untrusted context, include denial/error text, and never trigger tool re-execution. Database history remains in canonical OpenAI format. Other providers are unchanged. This compatibility mapping can be removed when Swisscom fixes its serving template.

Sanitized [fixtures](../probe-samples/) from a previous native Swisscom probe target `https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1` with model `swiss-ai/Apertus-v1.5-70B`. That key was historically held under the `PUBLIC_AI_API_KEY` variable name; the name did **not** mean that the Public AI endpoint accepted it. A request to the default Public AI endpoint with that key returned HTTP 401. Swisscom text, tool and image requests returned HTTP 200; image response identified the probe PNG color. Reasoning and `reasoning_kwargs` returned HTTP 429, so reasoning output/format is **not proven**. The current [summary](../probe-samples/summary.json) contains only those later reasoning retries: do not interpret it as a full all-case report. Other endpoints, models, accounts and rate limits may behave differently.

Set the provider values in your existing `.env` for Compose (create it from `.env.example` only if absent; edit keys locally, do not commit them):

```dotenv
LLM_BASE_URL=https://api.swisscom.com/products/swiss-ai-weeks/apertus-1.5-70b/v1
LLM_MODEL=swiss-ai/Apertus-v1.5-70B
SWISSCOM_API_KEY=<YOUR-SWISSCOM-KEY>
LLM_API_KEY=
PUBLIC_AI_API_KEY=
LLM_IMAGE_INPUT=supported
LLM_AUDIO_INPUT=supported
```

A manual probe (2026-09-24) on the same Swisscom endpoint confirmed native audio: a spoken 16-bit PCM WAV sent as `{"type": "input_audio", "input_audio": {"data": <base64>, "format": "wav"}}` was understood, also combined with an image and in streaming requests with tools. Voice-only messages are sent as a bare audio part: adding an instruction text such as "[Voice message: listen to the attached audio...]" made Apertus reply that it cannot process audio, while the bare audio part (with the normal system prompt and context) was understood.

In practice Apertus still misheard clean recordings (Whisper transcribed them verbatim), so an optional speech-to-text step is available: set `STT_BASE_URL` (OpenAI-compatible, e.g. vLLM serving `whisper-large-v3`), `STT_MODEL` and optionally `STT_API_KEY`. Voice messages are then transcribed once in the run worker, the transcript is cached on the attachment (`attachments.transcript`, never returned by the API) and sent to the LLM as plain user text. The UI is unchanged: users still see only the audio player. Transcription failures end the run with `stt_error`. Without `STT_BASE_URL`, audio is forwarded natively as `input_audio`. The browser records with `MediaRecorder` and converts to 16 kHz mono WAV before upload (`audio/wav` is the only accepted audio type); `LLM_AUDIO_INPUT` defaults to `unknown` and gates the microphone button and submission (`audio_input_unsupported`).

`LLM_IMAGE_INPUT=supported` is an **optional Swisscom override** justified only by the recorded image probe for that endpoint/model. Leave it `unknown` for an unverified provider: image submission is then rejected, rather than silently attempting an unsupported call. Do not claim reasoning support from the 429 responses. For native backend, set `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` and, only for that verified Swisscom configuration if desired, `LLM_IMAGE_INPUT=supported` in the backend PowerShell terminal. `SWISSCOM_API_KEY` alone is accepted by the backend, but the probe script reads only `LLM_API_KEY` or `PUBLIC_AI_API_KEY`.

Run a **new** probe locally from the repository root with Python 3.12/uv and your secret in your environment or `.env` (the script reads `.env` without overriding exported variables):

```powershell
uv run --project backend --python 3.12 python scripts/provider_probe.py text tool image reasoning reasoning_kwargs
```

The script writes sanitized per-case JSONL in `probe-samples/` and rewrites `probe-samples/summary.json` with **only the cases passed in that invocation**. A subset retry destroys the prior combined summary, but other per-case files remain until individually probed again. Review HTTP statuses, chunk types, finish reasons and image output in the corresponding fixture; a 200 alone does not prove all model features. The probe does not print the key, but raw provider error bodies and generated text may be sensitive; inspect before sharing. The probe injects the OS trust store if installed for TLS-inspecting networks. For backend outbound HTTPS, set `USE_OS_TRUSTSTORE=true` only if the local OS trust store is actually needed; never disable certificate verification (`verify=False`).