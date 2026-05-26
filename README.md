# Umanitek Agent Guardian

Umanitek Agent Guardian is a local agent-security fork built on Hermes and the
OriginTrail DKG. Its goal is to run as a Guardian supervisor that audits other
local agents, records their activity, detects risky behavior, and stores the
resulting audit history in DKG.

This repository is the product fork. The Guardian Hermes plugin is an internal
part of the fork, not a separately published plugin.

## What Guardian Does

Guardian V1 is observational. It does not block or quarantine agent actions yet.
It audits agent activity and reports:

- prompt-injection language in prompts, model output, and tool output
- sensitive filesystem access such as `~/Documents`, `~/Desktop`, `~/Downloads`,
  `~/.ssh`, cloud credentials, browser profiles, and system paths
- dependency install commands from package managers such as `pip`, `uv`, `npm`,
  `pnpm`, `yarn`, `bun`, `cargo`, and `brew`
- vulnerable dependency intelligence enriched from OSV, CISA KEV, NVD, and EPSS
- protected-agent status for Guardian-supervised Hermes and OpenClaw paths

Audit data is split by privacy boundary:

- Private Guardian graph: local events, findings, agent identity, workspace/path
  classification, remediation state, and query history.
- Public vulnerability graph: reusable dependency intelligence only, after
  privacy validation. Local prompts, paths, usernames, secrets, and machine
  identifiers must not be published publicly.

## Architecture

Guardian can audit another agent only when it has an interception point:

1. Guardian starts the child agent through a supervised launcher.
2. The child runtime loads a Guardian-aware adapter or hook.
3. The child routes tools through an audited MCP/proxy layer.

It cannot passively observe arbitrary already-running Cursor, Codex, Hermes, or
OpenClaw processes unless those processes are launched through Guardian,
instrumented with compatible hooks, or routed through an audited tool/proxy
path.

Current V1 coverage:

- Hermes child agents launched with `hermes guardian run-hermes`
- Hermes hook telemetry for session, model, and tool activity
- OpenClaw adapter events emitted through the DKG adapter
- DKG daemon Guardian API and Node UI Guardian dashboard

## Repository Layout

```text
agent-guardian/
├── plugins/guardian/        # Guardian supervisor + Hermes audit hooks
├── dkg/                     # OriginTrail DKG fork/submodule with Guardian API/UI
├── run_agent.py             # Hermes agent runtime
├── model_tools.py           # Hermes tool dispatch and hook invocation
├── tests/plugins/           # Guardian Hermes tests
└── README.md                # This fork-level guide
```

## Local Setup

From the repository root:

```bash
cd dkg
corepack prepare pnpm@10.28.1 --activate
pnpm install --frozen-lockfile
pnpm build

cd ..
source .venv/bin/activate
pip install -e .
```

If `.venv` does not exist, create it first:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run The Local Guardian UI

Start the DKG daemon from the local fork:

```bash
cd dkg
DKG_NO_BLUE_GREEN=1 node packages/cli/dist/cli.js start --foreground
```

Then open:

```text
http://127.0.0.1:9200/ui
```

If port `9200` is already in use, set a separate DKG home with a custom
`config.json` containing another `apiPort`, then open that port instead. During
testing we often use temporary ports such as `9320` or `9321` to avoid touching a
developer's normal `~/.dkg` node.

## Run A Supervised Hermes Child

With the DKG daemon running:

```bash
source .venv/bin/activate

OPENAI_API_KEY=... \
HERMES_HOME="${TMPDIR:-/tmp}/guardian-parent-hermes" \
DKG_DAEMON_URL=http://127.0.0.1:9200 \
GUARDIAN_DKG_DAEMON_URL=http://127.0.0.1:9200 \
hermes guardian run-hermes \
  --query "Use the terminal tool to print the current directory, then stop." \
  --workdir "${TMPDIR:-/tmp}/guardian-agent-workspace" \
  --child-home "${TMPDIR:-/tmp}/guardian-child-hermes" \
  --keep-home \
  --dkg-url http://127.0.0.1:9200 \
  --model gpt-4o-mini \
  --api-mode chat_completions \
  --enabled-toolsets terminal,file
```

The parent Guardian process records launch/exit events. The child Hermes process
emits session, model, and tool-call events through Guardian hooks. The model API
key is passed through the child environment and is not placed in the command
line.

## Connect Agents

Guardian is agent-to-agent supervision, not passive process surveillance. An
agent becomes visible in the dashboard when one of these integration paths sends
events to `POST /api/guardian/events`:

- Hermes: launch the child with `hermes guardian run-hermes`. Guardian owns the
  child environment, injects telemetry settings, and records which child ran
  each command or model request.
- OpenClaw: use the Guardian-aware DKG adapter so OpenClaw prompt, session, and
  tool-call events are emitted to the same Guardian endpoint.
- Other agents: route tool calls through an audited MCP/proxy path or add a
  compatible adapter. Until then, the dashboard must show them as not connected.

DKG stores local audit events and findings in the private Guardian graph. Only
reusable vulnerable-dependency intelligence is eligible for the public
vulnerability graph after privacy validation.

## Useful API Checks

The DKG daemon writes an auth token to its DKG home. For the default local node:

```bash
TOKEN=$(grep -v '^#' ~/.dkg/auth.token | head -n1)

curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9200/api/guardian/summary

curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9200/api/guardian/events?limit=20"

curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9200/api/guardian/findings?limit=50"

curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9200/api/guardian/fix-prompt
```

## Testing

Hermes Guardian tests:

```bash
source .venv/bin/activate
python -m pytest tests/plugins/test_guardian_plugin.py
```

DKG Guardian tests and build:

```bash
cd dkg
pnpm --filter @origintrail-official/dkg-node-ui exec vitest run test/guardian.test.ts
pnpm --filter @origintrail-official/dkg test -- guardian-routes
pnpm --filter @origintrail-official/dkg-node-ui build
pnpm --filter @origintrail-official/dkg build
```

## Current Limitations

- V1 is audit-only.
- Cursor/Codex coverage requires a compatible adapter or audited MCP/proxy path.
- Public vulnerability publishing requires a DKG identity capable of registering
  or writing to the public Guardian vulnerability context graph. Local edge-node
  tests without that identity store dependency intelligence locally and mark
  public publish as skipped.
- Guardian currently supervises Hermes directly. OpenClaw support is through the
  DKG adapter event surface.

## Upstream Credits

This fork builds on:

- Hermes Agent by Nous Research
- OriginTrail DKG V10 by OriginTrail

Guardian-specific audit, UI, and graph behavior is maintained by Umanitek in
this fork.
