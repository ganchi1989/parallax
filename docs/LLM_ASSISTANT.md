# Optional LLM Assistant

The core product remains deterministic and offline. The LLM Assistant is an opt-in creative aid for users who supply their own API key. It recommends one bounded stereo preset and explains that recommendation; it is not the renderer and is never an authority over comfort limits.

## Data sent

Only a compact, non-identifying shot summary is eligible:

```json
{
  "duration_seconds": 3.8,
  "motion_score": 0.72,
  "speech_ratio": 0.05,
  "depth_spread": 0.81,
  "foreground_ratio": 0.23,
  "brightness": 0.46,
  "cut_frequency_context": 0.76,
  "camera_movement": "lateral",
  "depth_reliability": 0.91
}
```

Frames, thumbnails, audio, dialogue text, subtitle text, depth maps, filenames, paths, project names, and user identity are excluded.

## Valid output

The provider must satisfy a strict structured-output schema:

```json
{
  "preset": "action_controlled",
  "narrative_importance": 0.6,
  "stereo_emphasis": "medium",
  "reason": "High motion and a short duration favor controlled depth.",
  "confidence": 0.84
}
```

`preset` is restricted to `dialogue_subtle`, `action_controlled`, `vista_deep`, `closeup_flat`, or `neutral`; emphasis is `low`, `medium`, or `high`; numeric values are finite and in `[0,1]`; explanation length is bounded. No numerical stereo/render parameters are accepted.

## Decision flow

```text
shot statistics → strict provider request → validated recommendation
       │                     │                        │
       └──────── failure / refusal / timeout ────────┤
                                                     ↓
                                    deterministic preset parameters
                                                     ↓
                                      mandatory Comfort Guard
```

A valid recommendation below the confidence threshold falls back to `neutral`. Provider failures, refusals, timeouts, invalid schemas, missing credentials, and unknown presets fall back to deterministic rules. Recommendation text is ephemeral in the editor; only a preset the editor applies becomes part of the normal versioned stereo script. Raw provider responses and chain-of-thought are not stored.

## Key handling

- The webview sends a new key once to a named Rust command over Tauri IPC.
- Rust stores it under the application service identity in Windows Credential Manager on the certified target.
- JavaScript can query only `configured: true|false`; there is no getter.
- Rust injects the key into the supervised worker process environment and never writes it to JSONL.
- The worker redacts credential-shaped text from errors and never logs request headers.
- Delete/replace operations are explicit and serialized.
- Environment-variable credentials are accepted for development and CI but are not persisted by the app.

## Initial OpenAI provider

The desktop adapter uses the Responses API with strict JSON Schema output and pins the release-approved model to `gpt-5.6-terra`. Model availability and cost are shown in settings and must be reviewed for each release. The official endpoint, timeout, and response-size cap are fixed or tightly validated, and requests are not automatically retried—users cannot turn the app into an arbitrary HTTP client.

Official references: <https://developers.openai.com/api/docs/models> and <https://platform.openai.com/docs/quickstart/make-your-first-api-request>.

## Product requirements before launch

- Consent copy appears before the first remote request.
- “Test connection” sends no project or shot data.
- The UI shows which provider/model will receive data and that usage may incur provider charges.
- Provider terms, retention controls, supported regions, request IDs, retry behavior, and account-side deletion controls are reviewed for the launch jurisdiction.
- Mocked contract/evaluation fixtures cover every preset, malformed output, refusal, timeout, rate limit, authentication failure, and low confidence.
