"""Read supported app-server history into files, without loading it into chat context."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


class Rpc:
    def __init__(self, command: str, timeout: float = 30):
        self.process = subprocess.Popen([command, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        self.queue = queue.Queue()
        self.counter, self.timeout = 0, timeout
        def read():
            for line in self.process.stdout:
                try:
                    self.queue.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.queue.put({"closed": True})
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "codex-project-handoff", "version": "1.1.0-beta.3"}, "capabilities": {"experimentalApi": True}})
            self.notify("initialized", {})
        except BaseException:
            self.close()
            raise

    def notify(self, method, params):
        self.process.stdin.write(json.dumps({"method": method, "params": params}) + "\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.counter += 1
        identifier = self.counter
        self.process.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(method + " timed out; detail capture is incomplete")
            message = self.queue.get(timeout=remaining)
            if message.get("closed"):
                raise RuntimeError("app-server ended during capture")
            if message.get("id") != identifier:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
            return message.get("result", {})

    def pages(self, method, params, limit=100000):
        output, cursor, seen = [], None, set()
        for _ in range(limit):
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            result = self.call(method, query)
            output.extend(result.get("data", result.get("turns", [])))
            cursor = result.get("nextCursor")
            if not cursor:
                return output
            if cursor in seen:
                raise RuntimeError("pagination returned a repeated cursor")
            seen.add(cursor)
        raise RuntimeError("history pagination limit reached")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def capture(rpc, project_paths: list[str], expected_ids: list[str] | None = None) -> dict:
    roots = [os.path.normcase(os.path.abspath(value)) for value in project_paths]
    def matches(thread):
        cwd = thread.get("cwd")
        if not cwd:
            return False
        value = os.path.normcase(os.path.abspath(cwd))
        return any(value == root or value.startswith(root + os.sep) for root in roots)
    summaries, details, warnings, seen = [], [], [], set()
    for archived in (False, True):
        try:
            # An empty sourceKinds list uses server defaults; request known desktop,
            # CLI and IDE kinds, falling back to all documented interactive kinds.
            try:
                threads = rpc.pages("thread/list", {"archived": archived, "limit": 100, "sourceKinds": ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview", "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"]})
            except RuntimeError:
                threads = rpc.pages("thread/list", {"archived": archived, "limit": 100})
                warnings.append("Server rejected source filters; desktop coverage must be compared with the app task inventory.")
            for thread in threads:
                if not matches(thread) or thread["id"] in seen:
                    continue
                seen.add(thread["id"])
                summaries.append(thread)
                identifier = thread["id"]
                try:
                    try:
                        turns = rpc.pages("thread/turns/list", {"threadId": identifier, "limit": 50, "itemsView": "full"})
                        detail = {"id": identifier, "thread": thread, "turns": turns, "captureMethod": "thread/turns/list:full"}
                    except RuntimeError:
                        result = rpc.call("thread/read", {"threadId": identifier, "includeTurns": True})
                        detail = {"id": identifier, "thread": result.get("thread", result), "captureMethod": "thread/read:includeTurns"}
                    details.append(detail)
                except (RuntimeError, TimeoutError, queue.Empty) as exc:
                    warnings.append(f"Task {identifier}: full history unavailable: {exc}")
                    details.append({"id": identifier, "error": str(exc)})
        except (RuntimeError, TimeoutError, queue.Empty) as exc:
            warnings.append(f"Task inventory (archived={archived}) incomplete: {exc}")
    detailed = sum(not item.get("error") for item in details)
    missing = sorted(set(expected_ids or []) - seen)
    if missing:
        warnings.append("App inventory tasks missing from history capture: " + ", ".join(missing))
    return {"schemaVersion": 2, "capturedAt": dt.datetime.now().astimezone().isoformat(), "threadSummaries": summaries, "detailedThreads": details, "warnings": warnings, "coverage": {"complete": not warnings, "taskCount": len(summaries), "detailedTaskCount": detailed}, "limitation": "Captured through supported read-only app-server calls. Product UI import, account sign-in and machine-bound credentials are not included."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-command", required=True)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--expected-thread-ids-file", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    rpc = Rpc(args.codex_command, args.timeout)
    try:
        expected = json.loads(args.expected_thread_ids_file.read_text(encoding="utf-8")) if args.expected_thread_ids_file else None
        context = capture(rpc, args.project, expected)
    finally:
        rpc.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(context["coverage"]))
    if context["warnings"]:
        print("WARNING: history coverage is incomplete; inspect warnings in the saved context.")


if __name__ == "__main__":
    main()
