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

## Fresh Install

Clone this repository with the DKG submodule included:

```bash
git clone --recurse-submodules https://github.com/KilianTrunk/agent-guardian.git
cd agent-guardian
```

If you already cloned without submodules:

```bash
git submodule update --init --recursive
```

The submodule is pinned by this repository. Do not replace `dkg/` with upstream
OriginTrail DKG unless you intentionally want to lose Guardian API/UI changes.

Prerequisites:

- Python 3.11+
- Node.js 22+ with Corepack
- pnpm 10.28.1
- an OpenAI-compatible model API key for supervised Hermes test runs

Build DKG and the Guardian UI:

```bash
cd dkg
corepack enable
corepack prepare pnpm@10.28.1 --activate
pnpm install --frozen-lockfile
pnpm build
pnpm --filter @origintrail-official/dkg-node-ui build:ui

cd ..
```

Set up Hermes from this fork:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Start Guardian

Guardian's dashboard and audit API are served by the DKG daemon in the submodule.
Start it from the local build in terminal 1:

```bash
cd dkg
DKG_NO_BLUE_GREEN=1 node packages/cli/dist/cli.js start --foreground
```

Then open:

```text
http://127.0.0.1:9200/ui
```

The Guardian tab is the default screen. The audit API is:

```text
POST http://127.0.0.1:9200/api/guardian/events
GET  http://127.0.0.1:9200/api/guardian/summary
GET  http://127.0.0.1:9200/api/guardian/events
GET  http://127.0.0.1:9200/api/guardian/findings
```

If port `9200` is already in use, set a separate DKG home with a custom
`config.json` containing another `apiPort`, then open that port instead. During
testing we often use temporary ports such as `9320` or `9321` to avoid touching a
developer's normal `~/.dkg` node.

## Connect Agents

Guardian is agent-to-agent supervision, not passive process surveillance. An
agent becomes visible in the dashboard only when it is launched through, or
configured with, a Guardian-aware integration that sends events to
`/api/guardian/events`.

### Observe A Hermes Agent

Use this path when you want Guardian to supervise a new Hermes run. The command
starts a parent Guardian process, then launches a child Hermes process with
Guardian telemetry forced on.

With the DKG daemon running, open terminal 2 from the repository root:

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
emits session, model, and tool-call events through Guardian hooks. The dashboard
should show:

- the child under **Protected Agents**
- tool/API/session events under **Live Audit**
- any risks under **Findings**

The model API key is passed through the child environment and is not placed in
the command line.

### Observe A Hermes Profile Through DKG

Use this path when a user already has a local Hermes profile and wants DKG/Hermes
integration installed:

```bash
cd agent-guardian/dkg
node packages/cli/dist/cli.js hermes setup
```

This writes Hermes DKG adapter state into the selected Hermes home/profile. It
is useful for DKG memory/integration flows, but for Guardian audit coverage of a
new Hermes task, prefer `hermes guardian run-hermes` because it gives Guardian
clear parent/child provenance for the run.

### Observe OpenClaw

Install or connect OpenClaw first so `~/.openclaw/openclaw.json` exists. Then
attach the DKG OpenClaw adapter from either the UI or CLI:

```bash
cd agent-guardian/dkg
node packages/cli/dist/cli.js openclaw setup
```

The DKG UI's **Connect OpenClaw** button runs the same setup flow. It merges the
Guardian-aware DKG adapter into OpenClaw's plugin config, installs the DKG node
skill into the OpenClaw workspace, and elects the DKG adapter into OpenClaw's
memory slot. After OpenClaw is restarted or reloads its config, OpenClaw prompt,
session, and tool-call events are emitted to Guardian.

Verify in the dashboard:

- **Protected Agents** shows `openclaw` after telemetry arrives.
- **Live Audit** shows OpenClaw prompt/session/tool-call events.
- **Findings** shows prompt-injection, sensitive-path, risky-shell, and
  dependency findings generated from those events.

### Other Agents

Cursor, Codex, and arbitrary existing processes are not covered by default. They
must either:

- load a Guardian-aware adapter that emits normalized Guardian events, or
- route tool calls through an audited MCP/proxy path that emits normalized
  Guardian events.

Until one of those paths exists, the UI must treat the agent as not connected.

DKG stores local audit events and findings in the private Guardian graph. Only
reusable vulnerable-dependency intelligence is eligible for the public
vulnerability graph after privacy validation.

## First Verification Run

After starting DKG and running a supervised Hermes child, check that events were
recorded:

```bash
curl http://127.0.0.1:9200/api/guardian/summary
curl "http://127.0.0.1:9200/api/guardian/events?limit=10"
curl "http://127.0.0.1:9200/api/guardian/findings?limit=10"
```

If the dashboard stays empty:

- confirm the child was launched with `hermes guardian run-hermes`
- confirm `DKG_DAEMON_URL` and `GUARDIAN_DKG_DAEMON_URL` point to the running
  daemon
- confirm OpenClaw was restarted after `dkg openclaw setup`
- confirm the DKG daemon is reachable at `http://127.0.0.1:9200`

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
