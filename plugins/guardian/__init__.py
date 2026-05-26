"""Umanitek Guardian audit plugin for Hermes.

The plugin is observational only. It records Hermes tool/model/session activity
to the local DKG daemon's Guardian ingest route and fails open on every error.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DAEMON_URL = "http://127.0.0.1:9200"
_MAX_QUEUE = 1000
_MAX_TEXT = 2000
_SUPERVISOR_ID = os.environ.get("GUARDIAN_SUPERVISOR_ID", "")
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|authorization|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=_MAX_QUEUE)
_worker_started = False
_worker_lock = threading.Lock()
_dropped = 0


def _guardian_enabled() -> bool:
    raw = os.environ.get("HERMES_GUARDIAN_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:
        return Path.home() / ".hermes"


def _load_daemon_url() -> str:
    env = os.environ.get("DKG_DAEMON_URL") or os.environ.get("GUARDIAN_DKG_DAEMON_URL")
    if env and env.strip():
        return env.strip().rstrip("/")
    cfg_path = _hermes_home() / "dkg.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            value = data.get("daemon_url") or data.get("daemonUrl")
            if isinstance(value, str) and value.strip():
                return value.strip().rstrip("/")
        except Exception:
            pass
    return _DEFAULT_DAEMON_URL


def _dkg_home() -> Path:
    env = os.environ.get("DKG_HOME")
    return Path(env).expanduser() if env else Path.home() / ".dkg"


def _load_token() -> Optional[str]:
    env = os.environ.get("DKG_API_TOKEN") or os.environ.get("DKG_AUTH_TOKEN")
    if env and env.strip():
        return env.strip()
    token_path = _dkg_home() / "auth.token"
    try:
        if token_path.exists():
            for line in token_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except Exception:
        return None
    return None


def _sanitize_text(value: str, max_len: int = _MAX_TEXT) -> str:
    text = _BEARER_RE.sub("Bearer [REDACTED]", value)
    text = re.sub(r"sk-[A-Za-z0-9]{16,}", "[REDACTED_API_KEY]", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9_]{20,}", "[REDACTED_GITHUB_TOKEN]", text)
    text = re.sub(r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]", text)
    if len(text) > max_len:
        return text[:max_len] + "...[truncated]"
    return text


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated-depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in list(value)[:50]]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, child in value.items():
            key_str = str(key)
            if _SECRET_KEY_RE.search(key_str):
                out[key_str] = "[REDACTED]"
            elif key_str.lower() in {"content", "body", "input", "prompt"} and isinstance(child, str):
                out[key_str] = _sanitize_text(child, 1200)
            else:
                out[key_str] = _redact(child, depth + 1)
        return out
    return _sanitize_text(str(value))


def _summarize_messages(messages: Any) -> list:
    if not isinstance(messages, list):
        return []
    out = []
    for msg in messages[-12:]:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                elif isinstance(part, str):
                    text_parts.append(part)
            content = "\n".join(text_parts)
        out.append({
            "role": msg.get("role"),
            "content": _sanitize_text(content, 1000) if isinstance(content, str) else _redact(content),
            "tool_calls": _redact(msg.get("tool_calls") or msg.get("toolCalls")),
        })
    return out


def _start_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker, name="guardian-audit", daemon=True)
        thread.start()
        _worker_started = True


def _enqueue(event: Dict[str, Any]) -> None:
    global _dropped
    if not _guardian_enabled():
        return
    _start_worker()
    try:
        _queue.put_nowait(event)
    except queue.Full:
        _dropped += 1
        if _dropped % 100 == 1:
            logger.warning("Guardian audit queue full; dropped %s event(s)", _dropped)


def _worker() -> None:
    while True:
        event = _queue.get()
        try:
            _post_event(event)
        except Exception as exc:
            logger.debug("Guardian audit event dropped: %s", exc)
        finally:
            _queue.task_done()


def _post_event(event: Dict[str, Any]) -> None:
    daemon_url = _load_daemon_url()
    token = _load_token()
    data = json.dumps(event, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{daemon_url}/api/guardian/events",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as response:
            response.read(512)
    except urllib.error.HTTPError as exc:
        body = exc.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"Guardian daemon responded {exc.code}: {body}") from exc


def _base_event(event_type: str, **kwargs: Any) -> Dict[str, Any]:
    instance_id = os.environ.get("GUARDIAN_AGENT_INSTANCE_ID") or _SUPERVISOR_ID or f"hermes-{os.getpid()}"
    agent_name = os.environ.get("GUARDIAN_AGENT_NAME") or "Hermes"
    event = {
        "type": event_type,
        "occurredAt": int(time.time() * 1000),
        "sourceAgent": {
            "framework": "hermes",
            "name": agent_name,
            "instanceId": instance_id,
        },
        **kwargs,
    }
    if _SUPERVISOR_ID:
        event.setdefault("metadata", {})
        if isinstance(event["metadata"], dict):
            event["metadata"]["guardianSupervisorId"] = _SUPERVISOR_ID
    return event


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None:
    _enqueue(_base_event(
        "tool_call",
        idempotencyKey=f"hermes:pre-tool:{session_id}:{task_id}:{tool_call_id}:{tool_name}",
        sessionId=session_id,
        taskId=task_id,
        toolCallId=tool_call_id,
        toolName=tool_name,
        title=f"Tool call requested: {tool_name}",
        data={"stage": "pre", "args": _redact(args or {})},
    ))


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: int = 0,
    **_: Any,
) -> None:
    _enqueue(_base_event(
        "tool_call",
        idempotencyKey=f"hermes:post-tool:{session_id}:{task_id}:{tool_call_id}:{tool_name}:{duration_ms}",
        sessionId=session_id,
        taskId=task_id,
        toolCallId=tool_call_id,
        toolName=tool_name,
        title=f"Tool call completed: {tool_name}",
        data={
            "stage": "post",
            "durationMs": duration_ms,
            "args": _redact(args or {}),
            "result": _redact(result),
        },
    ))


def _on_pre_api_request(**kwargs: Any) -> None:
    _enqueue(_base_event(
        "api_request",
        idempotencyKey=(
            f"hermes:api-request:{kwargs.get('session_id','')}:"
            f"{kwargs.get('task_id','')}:{kwargs.get('api_call_count','')}"
        ),
        sessionId=str(kwargs.get("session_id") or ""),
        taskId=str(kwargs.get("task_id") or ""),
        title="Model request prepared",
        data={
            "platform": kwargs.get("platform"),
            "provider": kwargs.get("provider"),
            "model": kwargs.get("model"),
            "baseUrl": kwargs.get("base_url"),
            "apiMode": kwargs.get("api_mode"),
            "apiCallCount": kwargs.get("api_call_count"),
            "messageCount": kwargs.get("message_count"),
            "toolCount": kwargs.get("tool_count"),
            "approxInputTokens": kwargs.get("approx_input_tokens"),
            "requestCharCount": kwargs.get("request_char_count"),
            "maxTokens": kwargs.get("max_tokens"),
            "userMessage": _sanitize_text(str(kwargs.get("user_message") or ""), 1200),
            "requestMessages": _summarize_messages(kwargs.get("request_messages")),
        },
    ))


def _on_post_api_request(**kwargs: Any) -> None:
    assistant = kwargs.get("assistant_message")
    content = getattr(assistant, "content", "") if assistant is not None else ""
    _enqueue(_base_event(
        "api_response",
        idempotencyKey=(
            f"hermes:api-response:{kwargs.get('session_id','')}:"
            f"{kwargs.get('task_id','')}:{kwargs.get('api_call_count','')}"
        ),
        sessionId=str(kwargs.get("session_id") or ""),
        taskId=str(kwargs.get("task_id") or ""),
        title="Model response received",
        data={
            "platform": kwargs.get("platform"),
            "provider": kwargs.get("provider"),
            "model": kwargs.get("model"),
            "responseModel": kwargs.get("response_model"),
            "apiMode": kwargs.get("api_mode"),
            "apiCallCount": kwargs.get("api_call_count"),
            "apiDuration": kwargs.get("api_duration"),
            "finishReason": kwargs.get("finish_reason"),
            "usage": _redact(kwargs.get("usage")),
            "assistantContentChars": kwargs.get("assistant_content_chars"),
            "assistantToolCallCount": kwargs.get("assistant_tool_call_count"),
            "assistantSample": _sanitize_text(str(content or ""), 1200),
        },
    ))


def _on_session_start(session_id: str = "", **kwargs: Any) -> None:
    _enqueue(_base_event(
        "session",
        idempotencyKey=f"hermes:session-start:{session_id}:{int(time.time())}",
        sessionId=session_id,
        title="Hermes session started",
        data=_redact(kwargs),
    ))


def _on_session_end(session_id: str = "", completed: bool = True, interrupted: bool = False, **kwargs: Any) -> None:
    _enqueue(_base_event(
        "session",
        idempotencyKey=f"hermes:session-end:{session_id}:{int(time.time())}",
        sessionId=session_id,
        title="Hermes session ended",
        data={"completed": completed, "interrupted": interrupted, **_redact(kwargs)},
    ))


def _post_supervisor_event(event: Dict[str, Any]) -> None:
    try:
        _post_event(event)
    except Exception as exc:
        logger.debug("Guardian supervisor event dropped: %s", exc)


def _setup_cli(parser) -> None:
    subparsers = parser.add_subparsers(dest="guardian_command")

    run_hermes = subparsers.add_parser(
        "run-hermes",
        help="Launch a child Hermes agent under Guardian audit",
        description=(
            "Start a supervised child Hermes agent with isolated HERMES_HOME "
            "and forced Guardian telemetry. The child writes audit events to "
            "the configured DKG daemon while this parent records launch/exit "
            "supervisor events."
        ),
    )
    run_hermes.add_argument("--query", required=True, help="Prompt for the child Hermes agent")
    run_hermes.add_argument("--workdir", default=os.getcwd(), help="Working directory for the child agent")
    run_hermes.add_argument("--child-home", default="", help="Existing HERMES_HOME for the child; defaults to a temp directory")
    run_hermes.add_argument("--keep-home", action="store_true", help="Do not delete an auto-created child HERMES_HOME")
    run_hermes.add_argument("--dkg-url", default="", help="DKG daemon URL; defaults to plugin discovery rules")
    run_hermes.add_argument("--model", default="gpt-4o-mini", help="OpenAI-compatible model for the child")
    run_hermes.add_argument("--base-url", default="https://api.openai.com/v1", help="OpenAI-compatible base URL")
    run_hermes.add_argument("--api-mode", default="chat_completions", help="Child Hermes API mode")
    run_hermes.add_argument("--max-turns", type=int, default=6, help="Maximum child tool/API iterations")
    run_hermes.add_argument("--enabled-toolsets", default="terminal,file", help="Comma-separated child toolsets")
    run_hermes.add_argument("--disabled-toolsets", default="", help="Comma-separated child disabled toolsets")
    run_hermes.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing the child model API key")
    run_hermes.set_defaults(func=_cmd_run_hermes)


def _cmd_run_hermes(args) -> int:
    """Launch a supervised child Hermes process.

    This is the agent-to-agent audit path: the Guardian parent owns the child
    runtime environment and forces the child to emit Guardian audit events.
    """
    supervisor_id = f"guardian-supervisor-{int(time.time())}-{os.getpid()}"
    daemon_url = (args.dkg_url or _load_daemon_url()).rstrip("/")
    child_home = Path(args.child_home).expanduser() if args.child_home else Path(tempfile.mkdtemp(prefix="guardian-hermes-child-"))
    auto_home = not bool(args.child_home)
    child_home.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update({
        "HERMES_HOME": str(child_home),
        "HERMES_GUARDIAN_ENABLED": "1",
        "GUARDIAN_SUPERVISOR_ID": supervisor_id,
        "GUARDIAN_AGENT_INSTANCE_ID": f"{supervisor_id}:hermes-child",
        "GUARDIAN_AGENT_NAME": "Hermes child",
        "DKG_DAEMON_URL": daemon_url,
        "GUARDIAN_DKG_DAEMON_URL": daemon_url,
        "HERMES_ENABLE_PROJECT_PLUGINS": env.get("HERMES_ENABLE_PROJECT_PLUGINS", "0"),
    })
    if args.api_key_env and args.api_key_env in os.environ:
        env["OPENAI_API_KEY"] = os.environ[args.api_key_env]

    child_spec = {
        "query": args.query,
        "base_url": args.base_url,
        "api_mode": args.api_mode,
        "model": args.model,
        "max_turns": args.max_turns,
        "enabled_toolsets": [p.strip() for p in args.enabled_toolsets.split(",") if p.strip()],
        "disabled_toolsets": [p.strip() for p in args.disabled_toolsets.split(",") if p.strip()],
    }
    env["GUARDIAN_CHILD_SPEC"] = json.dumps(child_spec)
    child_code = (
        "import json, os, sys\n"
        "from run_agent import AIAgent\n"
        "spec=json.loads(os.environ['GUARDIAN_CHILD_SPEC'])\n"
        "agent=AIAgent(base_url=spec['base_url'], api_key=os.environ.get('OPENAI_API_KEY'), "
        "api_mode=spec['api_mode'], model=spec['model'], max_iterations=int(spec['max_turns']), "
        "enabled_toolsets=spec['enabled_toolsets'] or None, "
        "disabled_toolsets=spec['disabled_toolsets'] or None, "
        "skip_memory=True, skip_context_files=True, quiet_mode=True)\n"
        "result=agent.run_conversation(spec['query'])\n"
        "print(result.get('final_response') or '')\n"
        "sys.exit(0 if result.get('completed') else 1)\n"
    )
    cmd = [sys.executable, "-c", child_code]

    _post_supervisor_event({
        "type": "agent_activity",
        "idempotencyKey": f"{supervisor_id}:launch",
        "occurredAt": int(time.time() * 1000),
        "sourceAgent": {"framework": "guardian", "name": "Guardian Supervisor", "instanceId": supervisor_id},
        "title": "Guardian launched child Hermes agent",
        "summary": "A child Hermes process was started under Guardian audit.",
        "data": {
            "childFramework": "hermes",
            "childHome": str(child_home),
            "workdir": str(Path(args.workdir).expanduser()),
            "model": args.model,
            "baseUrl": args.base_url,
            "apiMode": args.api_mode,
            "enabledToolsets": args.enabled_toolsets,
            "command": " ".join(shlex.quote(part) for part in [sys.executable, "-c", "<guardian-child-runner>"]),
        },
    })

    print(f"Guardian supervisor: {supervisor_id}")
    print(f"Child HERMES_HOME: {child_home}")
    print(f"DKG daemon: {daemon_url}")
    proc = subprocess.run(cmd, cwd=str(Path(args.workdir).expanduser()), env=env, text=True)

    _post_supervisor_event({
        "type": "agent_activity",
        "idempotencyKey": f"{supervisor_id}:exit:{proc.returncode}",
        "occurredAt": int(time.time() * 1000),
        "sourceAgent": {"framework": "guardian", "name": "Guardian Supervisor", "instanceId": supervisor_id},
        "severity": "info" if proc.returncode == 0 else "medium",
        "title": "Guardian child Hermes agent exited",
        "summary": f"Child Hermes process exited with code {proc.returncode}.",
        "data": {
            "childFramework": "hermes",
            "childHome": str(child_home),
            "returnCode": proc.returncode,
        },
    })

    if auto_home and not args.keep_home:
        try:
            import shutil
            shutil.rmtree(child_home, ignore_errors=True)
        except Exception:
            pass
    return int(proc.returncode or 0)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_api_request", _on_pre_api_request)
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_cli_command(
        "guardian",
        "Launch and inspect Guardian-supervised child agents",
        _setup_cli,
    )
