# guardian

Observational Hermes audit plugin for Umanitek Guardian. The plugin streams
Hermes session, model, and tool activity to the local DKG daemon so the DKG UI
can show live agent-risk findings without blocking the agent.

## Scope

Guardian V1 is audit-only. It records enough structured metadata to detect:

- prompt-injection patterns in user/tool/model text
- sensitive filesystem access outside normal workspace roots
- dependency installation commands from `pip`, `uv`, `npm`, `pnpm`, `yarn`,
  `bun`, `cargo`, and `brew`
- risky shell patterns, including remote scripts piped into interpreters
- vulnerable dependency intelligence enriched by the DKG daemon

The plugin does not quarantine, block, or modify tool calls. All transport
errors are fail-open so Hermes continues running if DKG is unavailable.

## Hook Coverage

| Hook | Event type | Notes |
|---|---|---|
| `pre_tool_call` | `tool_call` | Captures requested tool name and redacted arguments. |
| `post_tool_call` | `tool_call` | Captures duration, redacted arguments, and redacted result. |
| `pre_api_request` | `api_request` | Captures provider/model metadata and summarized request messages. |
| `post_api_request` | `api_response` | Captures response metadata, usage summary, and sanitized assistant sample. |
| `on_session_start` | `session` | Records a session start marker. |
| `on_session_end` | `session` | Records completion/interruption metadata. |

## Data Handling

The plugin redacts common secret-bearing keys and token formats before sending
events to DKG. Keys matching `api_key`, `token`, `secret`, `password`,
`authorization`, `credential`, or `private_key` are replaced with
`[REDACTED]`. Long text fields are truncated before transport.

The DKG daemon performs another normalization/redaction pass before storage and
uses deterministic event and finding IDs for idempotent retries.

Public DKG publishing is limited to dependency vulnerability intelligence that
passes the daemon privacy split. Local paths, prompts, usernames, raw tool
arguments, secrets, and machine identifiers stay in the private Guardian graph.

## Configuration

Guardian is enabled by default when the bundled plugin is loaded.

| Setting | Purpose |
|---|---|
| `HERMES_GUARDIAN_ENABLED=0` | Disable event capture. |
| `DKG_DAEMON_URL` | Override the daemon URL. Defaults to `http://127.0.0.1:9200`. |
| `GUARDIAN_DKG_DAEMON_URL` | Guardian-specific daemon URL override. |
| `DKG_API_TOKEN` | Bearer token for daemon API auth. |
| `DKG_AUTH_TOKEN` | Alternate bearer token variable. |
| `DKG_HOME` | Used to find `auth.token` when no token env var is set. |

If no URL env var is set, the plugin also checks
`$HERMES_HOME/dkg.json` for `daemon_url` or `daemonUrl`.

## Local Setup

From the repository root:

```bash
cd /Users/kiliantrunk/Projects/umanitek/agent-guardian/dkg
corepack prepare pnpm@10.28.1 --activate
pnpm install --frozen-lockfile
pnpm build
cd packages/cli
node ./scripts/bundle-markitdown-binaries.mjs --build-current-platform
pnpm link --global --filter @origintrail-official/dkg

cd /Users/kiliantrunk/Projects/umanitek/agent-guardian
source .venv/bin/activate
dkg hermes setup
echo 'API_SERVER_ENABLED=true' >> ~/.hermes/.env
hermes gateway run --replace -v
```

Open the DKG UI at `http://127.0.0.1:9200/ui`. Guardian is the default tab.

For monorepo daemon development, prefer running the built local CLI with:

```bash
cd /Users/kiliantrunk/Projects/umanitek/agent-guardian/dkg
DKG_NO_BLUE_GREEN=1 node packages/cli/dist/cli.js start --foreground
```

## Testing

Targeted plugin tests:

```bash
python -m pytest tests/plugins/test_guardian_plugin.py
```

The full repository test runner expects a local Hermes virtualenv. If this
checkout has no `.venv` or `venv`, create one or use a temporary venv for the
targeted test.

Relevant DKG tests live in the submodule:

```bash
cd dkg
pnpm exec vitest run packages/node-ui/test/guardian.test.ts
pnpm exec vitest run packages/cli/test/guardian-routes.test.ts
pnpm exec vitest run packages/adapter-openclaw/test/dkg-client.test.ts
```
