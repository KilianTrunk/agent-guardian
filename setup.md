# Agent Guardian Setup

## 1. Clone

```bash
git clone --recurse-submodules <repo-url> agent-guardian
cd agent-guardian
```

If you already cloned:

```bash
git submodule update --init --recursive
```

## 2. Install Hermes

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[web,pty,mcp]"
```

## 3. Set API Key

```bash
export OPENAI_API_KEY="your-key"
```

## 4. Build DKG

```bash
cd dkg
corepack enable
corepack prepare pnpm@10.28.1 --activate
pnpm install --frozen-lockfile
pnpm build
pnpm --filter @origintrail-official/dkg-node-ui build:ui
cd ..
```

## 5. Start Guardian DKG UI

Terminal 1:

```bash
cd dkg
DKG_NO_BLUE_GREEN=1 node packages/cli/dist/cli.js start --foreground
```

Open:

```text
http://127.0.0.1:9200/ui
```

## 6. Start Hermes Web UI

Terminal 2:

```bash
source .venv/bin/activate

export DKG_DAEMON_URL="http://127.0.0.1:9200"
export GUARDIAN_DKG_DAEMON_URL="http://127.0.0.1:9200"
export HERMES_GUARDIAN_ENABLED="1"
export GUARDIAN_AGENT_NAME="Hermes Web"

hermes dashboard --tui --host 127.0.0.1 --port 9119
```

Open:

```text
http://127.0.0.1:9119/chat
```

## 7. Test

Paste this into Hermes Chat:

```text
Use the terminal tool to print the current working directory, then stop.
```

Then check Guardian:

```text
http://127.0.0.1:9200/ui
```
