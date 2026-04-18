"""Convert exported Claude Code session JSONL into a single self-sufficient markdown.

Run from the session directory:
    python _convert.py
Produces: session.md
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

if len(sys.argv) > 1:
    HERE = Path(sys.argv[1])
else:
    HERE = Path(__file__).parent
# Discover the main JSONL by scanning for top-level *.jsonl files
_jsonls = [p for p in HERE.glob("*.jsonl")]
if not _jsonls:
    print(f"no .jsonl found in {HERE}", file=sys.stderr)
    sys.exit(1)
MAIN_JSONL = _jsonls[0]
MAIN_ID = MAIN_JSONL.stem
SUBAGENT_DIR = HERE / MAIN_ID / "subagents"
META_PATH = HERE / "metadata.json"
OUT_PATH = HERE / "session.md"

TRUNC_VALUE = 400
TRUNC_RESULT_LINES = 40
TRUNC_RESULT_CHARS = 2500

SKILL_PREFIX = "Base directory for this skill:"
TASK_NOTIF_RE = re.compile(r"<task-notification>", re.I)
SYS_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def trunc(s: str, n: int = TRUNC_VALUE) -> str:
    if s is None:
        return ""
    if len(s) <= n:
        return s
    return s[:n] + f"\n… (truncated, {len(s) - n} more chars)"


def clean_reminders(s: str) -> str:
    return SYS_REMINDER_RE.sub("", s).strip()


def fmt_tool_args(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return f"`{trunc(str(inp))}`"
    if name == "Bash":
        cmd = inp.get("command", "")
        desc = inp.get("description", "")
        bg = " [bg]" if inp.get("run_in_background") else ""
        head = f"*{desc}*{bg}" if desc else ""
        return f"{head}\n```bash\n{trunc(cmd, 1200)}\n```"
    if name in ("Read",):
        fp = inp.get("file_path", "")
        extras = []
        for k in ("offset", "limit", "pages"):
            if k in inp:
                extras.append(f"{k}={inp[k]}")
        tail = f" ({', '.join(extras)})" if extras else ""
        return f"`{fp}`{tail}"
    if name == "Write":
        fp = inp.get("file_path", "")
        content = inp.get("content", "")
        return f"`{fp}`\n```\n{trunc(content, 1500)}\n```"
    if name == "Edit":
        fp = inp.get("file_path", "")
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        ra = " (replace_all)" if inp.get("replace_all") else ""
        return (
            f"`{fp}`{ra}\n"
            f"```diff\n- {trunc(old, 700)}\n+ {trunc(new, 700)}\n```"
        )
    if name in ("Grep",):
        pat = inp.get("pattern", "")
        extras = [f"pattern=`{pat}`"]
        for k in ("path", "glob", "type", "output_mode", "-i", "-n", "-A", "-B", "-C"):
            if k in inp:
                extras.append(f"{k}={inp[k]}")
        return " ".join(extras)
    if name == "Glob":
        pat = inp.get("pattern", "")
        path = inp.get("path", "")
        return f"`{pat}`" + (f" in `{path}`" if path else "")
    if name == "Agent":
        st = inp.get("subagent_type", "general-purpose")
        desc = inp.get("description", "")
        prompt = inp.get("prompt", "")
        return (
            f"**subagent_type:** `{st}` — *{desc}*\n\n"
            f"<details><summary>prompt</summary>\n\n```\n{trunc(prompt, 4000)}\n```\n\n</details>"
        )
    if name == "Skill":
        return f"skill=`{inp.get('skill','?')}`"
    if name == "mcp__ccd_session__mark_chapter":
        return inp.get("title", "")
    if name == "TodoWrite":
        todos = inp.get("todos", [])
        lines = []
        for t in todos:
            status = t.get("status", "?")
            mark = {"completed": "x", "in_progress": "~", "pending": " "}.get(status, "?")
            lines.append(f"- [{mark}] {t.get('content','')}")
        return "\n".join(lines)
    # fallback
    try:
        return "```json\n" + trunc(json.dumps(inp, indent=2), 800) + "\n```"
    except Exception:
        return trunc(str(inp))


def fmt_tool_result(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "image":
                    parts.append("[image omitted]")
                else:
                    parts.append(str(c))
            else:
                parts.append(str(c))
        text = "\n".join(parts)
    elif isinstance(content, str):
        text = content
    else:
        text = str(content)
    text = clean_reminders(text)
    lines = text.splitlines()
    truncated = False
    if len(lines) > TRUNC_RESULT_LINES:
        lines = lines[:TRUNC_RESULT_LINES]
        truncated = True
    text = "\n".join(lines)
    if len(text) > TRUNC_RESULT_CHARS:
        text = text[:TRUNC_RESULT_CHARS]
        truncated = True
    if truncated:
        text += "\n… (truncated)"
    return text


def is_skill_injection(s: str) -> bool:
    return s.lstrip().startswith(SKILL_PREFIX)


def skill_name_from_injection(s: str) -> str:
    first = s.splitlines()[0] if s else ""
    # "Base directory for this skill: ...\skills\<name>"
    m = re.search(r"skills[\\/]([^\\/\s]+)", first)
    return m.group(1) if m else "unknown"


def is_task_notification(s: str) -> bool:
    return bool(TASK_NOTIF_RE.search(s or ""))


def extract_user_text(msg_content: Any) -> tuple[str, bool]:
    """Return (text, is_real_user_prompt). Filters skill injections & notifications."""
    if isinstance(msg_content, str):
        txt = msg_content
    elif isinstance(msg_content, list):
        parts = []
        for c in msg_content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        txt = "\n".join(parts)
    else:
        return "", False
    txt = txt.strip()
    if not txt:
        return "", False
    if is_task_notification(txt):
        return f"_[task-notification elided]_", False
    if is_skill_injection(txt):
        return f"_[skill loaded: **{skill_name_from_injection(txt)}**]_", False
    # Remove bare system-reminder wrappers that sometimes appear in user text
    cleaned = clean_reminders(txt)
    return cleaned, True


def is_tool_result_user(entry: dict) -> bool:
    c = entry.get("message", {}).get("content")
    if isinstance(c, list):
        return any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)
    return False


def collect_tool_results(entries: list[dict]) -> dict[str, dict]:
    """Map tool_use_id -> {content, is_error}."""
    m = {}
    for e in entries:
        if e.get("type") != "user":
            continue
        c = e.get("message", {}).get("content")
        if not isinstance(c, list):
            continue
        for x in c:
            if isinstance(x, dict) and x.get("type") == "tool_result":
                m[x.get("tool_use_id")] = {
                    "content": x.get("content"),
                    "is_error": x.get("is_error", False),
                }
    return m


def load_subagents() -> dict[str, dict]:
    """Map description -> {meta, entries}. Subagent meta has no toolUseId, but the
    `description` field matches the parent Agent tool_use input.description."""
    out = {}
    if not SUBAGENT_DIR.exists():
        return out
    for meta_path in SUBAGENT_DIR.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        jsonl_path = SUBAGENT_DIR / (meta_path.name.replace(".meta.json", ".jsonl"))
        if jsonl_path.exists():
            key = meta.get("description", meta_path.stem)
            out[key] = {
                "meta": meta,
                "entries": load_jsonl(jsonl_path),
            }
    return out


# ---------- rendering ----------

def render_stream(entries: list[dict], tool_results: dict, subagents: dict, indent: int = 0) -> list[str]:
    """Render a list of main-transcript entries into markdown lines. Turns are split
    at real user prompts; tool_use blocks render inline. Subagent content is inlined
    after the parent Agent tool_use (for top-level stream only)."""
    ind = "  " * indent
    out: list[str] = []
    turn_idx = 0

    def add(s: str = ""):
        for line in s.splitlines() or [""]:
            out.append(ind + line if line else "")

    for entry in entries:
        et = entry.get("type")
        if et in ("queue-operation", "last-prompt", "custom-title", "attachment"):
            continue
        if et == "system":
            sub = entry.get("subtype")
            if sub == "compact_boundary":
                add("")
                add("---")
                meta = entry.get("compactMetadata", {}) or {}
                add(f"_[conversation compacted — trigger: {meta.get('trigger','?')}, "
                    f"pre {meta.get('preTokens','?')} → post {meta.get('postTokens','?')} tokens]_")
                add("---")
                add("")
            continue

        msg = entry.get("message", {}) if isinstance(entry.get("message"), dict) else {}
        content = msg.get("content")

        if et == "user":
            # tool_result handled alongside assistant tool_use; skip here
            if is_tool_result_user(entry):
                continue
            text, is_real = extract_user_text(content)
            if not text:
                continue
            if is_real:
                turn_idx += 1
                title = text.splitlines()[0][:70].strip()
                if indent == 0:
                    add("")
                    add(f"## Turn {turn_idx} — {title}")
                    add("")
                    add("**User:**")
                    add("")
                    add("> " + text.replace("\n", "\n> "))
                    add("")
                else:
                    add("")
                    add(f"**User:** {text}")
                    add("")
            else:
                add(text)
                add("")
            continue

        if et == "assistant":
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = block.get("text", "").strip()
                    if txt:
                        add("**Assistant:** " + txt if indent == 0 else txt)
                        add("")
                elif btype == "thinking":
                    th = block.get("thinking", "").strip()
                    if not th:
                        continue
                    add("<details><summary><em>thinking</em></summary>")
                    add("")
                    add(th)
                    add("")
                    add("</details>")
                    add("")
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {}) or {}
                    tu_id = block.get("id")
                    if name == "mcp__ccd_session__mark_chapter":
                        title = inp.get("title", "")
                        summary = inp.get("summary", "")
                        add("")
                        prefix = "##" if indent == 0 else "####"
                        add(f"{prefix} Chapter: {title}")
                        if summary:
                            add(f"_{summary}_")
                        add("")
                        continue
                    args_md = fmt_tool_args(name, inp)
                    add(f"🔧 **{name}**")
                    for line in args_md.splitlines():
                        add(line)
                    res = tool_results.get(tu_id)
                    if res is not None:
                        body = fmt_tool_result(res["content"])
                        err = " ⚠️ error" if res.get("is_error") else ""
                        if body.strip():
                            add("")
                            add(f"<details><summary>result{err}</summary>")
                            add("")
                            add("```")
                            for line in body.splitlines():
                                add(line)
                            add("```")
                            add("")
                            add("</details>")
                            add("")
                        else:
                            add(f"_[empty result{err}]_")
                            add("")
                    # Inline subagent content for Agent tool, top-level only (match by description)
                    sa_key = inp.get("description") if name == "Agent" else None
                    if name == "Agent" and indent == 0 and sa_key in subagents:
                        sa = subagents[sa_key]
                        meta = sa["meta"]
                        add("")
                        add(f"### Subagent: {meta.get('description', meta.get('title','?'))}")
                        sub_tool_results = collect_tool_results(sa["entries"])
                        sub_lines = render_stream(
                            sa["entries"], sub_tool_results, subagents, indent=indent + 1
                        )
                        out.extend(sub_lines)
                        add("")
        # ignore other types
    return out


def main() -> int:
    if not MAIN_JSONL.exists():
        print(f"missing {MAIN_JSONL}", file=sys.stderr)
        return 1

    meta = {}
    if META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    entries = load_jsonl(MAIN_JSONL)
    tool_results = collect_tool_results(entries)
    subagents = load_subagents()

    header = []
    header.append(f"# Session: {meta.get('title', 'Untitled')}")
    header.append("")
    header.append("| Field | Value |")
    header.append("|---|---|")
    header.append(f"| session id | `{meta.get('cliSessionId','?')}` |")
    header.append(f"| cwd | `{meta.get('cwd','?')}` |")
    header.append(f"| model | `{meta.get('model','?')}` |")
    header.append(f"| created | {meta.get('createdAt','?')} |")
    header.append(f"| last activity | {meta.get('lastActivityAt','?')} |")
    header.append(f"| completed turns | {meta.get('completedTurns','?')} |")
    header.append(f"| subagents | {len(subagents)} |")
    header.append("")

    body = render_stream(entries, tool_results, subagents, indent=0)

    OUT_PATH.write_text("\n".join(header + body) + "\n", encoding="utf-8")
    size = OUT_PATH.stat().st_size
    print(f"wrote {OUT_PATH} ({size:,} bytes, {len(header)+len(body):,} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
