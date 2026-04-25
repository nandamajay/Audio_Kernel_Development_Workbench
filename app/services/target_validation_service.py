"""Target validation orchestration service for ADB/fastboot workflows."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Iterable


@dataclass
class PlannedCommand:
    argv: list[str]
    display: str


class TargetValidationService:
    """Build and execute target validation plans from natural language input."""

    def check_target_connected(self, serial: str, timeout: int = 8) -> tuple[bool, str]:
        serial = (serial or "").strip()
        if not serial:
            return False, "serial is required"
        try:
            proc = subprocess.run(
                ["adb", "-s", serial, "shell", "echo", "connected"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return False, "adb command is not available on server"
        except subprocess.TimeoutExpired:
            return False, "adb connect command timed out"

        if proc.returncode == 0 and "connected" in (proc.stdout or "").lower():
            return True, (proc.stdout or "connected").strip()
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        return False, stderr or stdout or f"adb exited with rc={proc.returncode}"

    def plan_commands(self, serial: str, nl_command: str) -> list[PlannedCommand]:
        serial = (serial or "").strip()
        query = (nl_command or "").strip().lower()
        commands: list[PlannedCommand] = []

        def add(argv: list[str], display: str):
            commands.append(PlannedCommand(argv=argv, display=display))

        def adb_shell(shell_cmd: str):
            add(
                ["adb", "-s", serial, "shell", "sh", "-c", shell_cmd],
                f"adb -s {serial} shell sh -c {shell_cmd}",
            )

        add(["adb", "-s", serial, "get-state"], f"adb -s {serial} get-state")
        add(
            ["adb", "-s", serial, "shell", "getprop", "ro.product.device"],
            f"adb -s {serial} shell getprop ro.product.device",
        )
        add(
            ["adb", "-s", serial, "shell", "getprop", "ro.build.fingerprint"],
            f"adb -s {serial} shell getprop ro.build.fingerprint",
        )

        if "reboot" in query:
            add(["adb", "-s", serial, "reboot"], f"adb -s {serial} reboot")
            add(["adb", "-s", serial, "wait-for-device"], f"adb -s {serial} wait-for-device")

        if any(word in query for word in ("dmesg", "kernel log", "panic", "oops", "crash")):
            adb_shell("dmesg | tail -n 200")

        if any(word in query for word in ("audio", "alsa", "sound")):
            adb_shell("dumpsys media.audio_flinger | head -n 120")
            adb_shell("dumpsys media.audio_policy | head -n 120")

        if any(word in query for word in ("memory", "ram")):
            adb_shell("cat /proc/meminfo | head -n 80")

        if any(word in query for word in ("cpu", "thermal", "temp", "performance")):
            adb_shell("top -b -n 1 | head -n 80")
            adb_shell("dumpsys thermalservice | head -n 120")

        if any(word in query for word in ("network", "wifi", "bluetooth")):
            adb_shell("dumpsys wifi | head -n 120")
            adb_shell("dumpsys bluetooth_manager | head -n 120")

        if any(word in query for word in ("storage", "disk", "filesystem")):
            adb_shell("df -h")

        if len(commands) <= 3:
            adb_shell("uname -a")
            adb_shell("uptime")
            adb_shell("dmesg | tail -n 120")

        return commands

    def summarize(self, nl_command: str, executed: Iterable[dict], raw_output: str) -> tuple[str, str]:
        combined = ((raw_output or "") + "\n" + (nl_command or "")).lower()
        result = "PASS"
        if any(token in combined for token in ("not found", "no devices", "device offline")):
            result = "ERROR"
        elif any(token in combined for token in ("fail", "failed", "panic", "fatal", "exception")):
            result = "FAIL"

        total = 0
        failures = 0
        for row in executed:
            total += 1
            if int(row.get("returncode", 1)) != 0:
                failures += 1
        if failures > 0 and result == "PASS":
            result = "FAIL"

        summary = self._llm_summarize(nl_command, executed, raw_output, result, total, failures)
        return result, summary

    def _llm_summarize(
        self,
        nl_command: str,
        executed: Iterable[dict],
        raw_output: str,
        result: str,
        total: int,
        failures: int,
    ) -> str:
        api_key = (os.getenv("QGENIE_API_KEY") or "").strip()
        if not api_key:
            ts = datetime.utcnow().isoformat()
            return (
                f"[{ts}] Validation {result}. "
                f"Executed {total} command(s), {failures} non-zero return(s). "
                f"NL intent: {nl_command or 'N/A'}."
            )

        prompt = (
            "You are AKDW validation summarizer. "
            "Return concise report with Overall Result, Key Findings, and Next Action.\n\n"
            f"NL Request:\n{nl_command}\n\n"
            f"Computed Result: {result}\n"
            f"Commands Run: {total}\n"
            f"Failures: {failures}\n\n"
            f"Output:\n{(raw_output or '')[:12000]}"
        )
        try:
            try:
                from qgenie import ChatMessage, QGenieClient
            except Exception:
                from qgenie_sdk import ChatMessage, QGenieClient  # type: ignore

            provider_url = (
                os.getenv("QGENIE_PROVIDER_URL") or "https://qgenie-chat.qualcomm.com/v1"
            ).strip()
            client = QGenieClient(api_key=api_key, base_url=provider_url)
            response = client.chat(
                model=(os.getenv("QGENIE_DEFAULT_MODEL") or "claude-sonnet-4").strip(),
                messages=[ChatMessage(role="user", content=prompt)],
            )
            content = getattr(response, "content", None) or str(response)
            return (content or "").strip()[:4000]
        except Exception:
            ts = datetime.utcnow().isoformat()
            return (
                f"[{ts}] Validation {result}. "
                f"Executed {total} command(s), {failures} non-zero return(s). "
                f"NL intent: {nl_command or 'N/A'}."
            )

    def run_validation_stream(
        self,
        serial: str,
        nl_command: str,
        timeout_per_command: int = 25,
    ) -> Generator[dict, None, tuple[list[dict], str, str, str]]:
        executed: list[dict] = []
        output_chunks: list[str] = []
        plan = self.plan_commands(serial, nl_command)

        yield {
            "type": "meta",
            "message": "Planning validation commands from natural language request",
            "planned_count": len(plan),
        }

        for idx, cmd in enumerate(plan, start=1):
            yield {
                "type": "command_start",
                "index": idx,
                "total": len(plan),
                "command": cmd.display,
            }
            try:
                proc = subprocess.run(
                    cmd.argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout_per_command,
                )
                stdout = (proc.stdout or "").strip()
                stderr = (proc.stderr or "").strip()
                captured = "\n".join(part for part in (stdout, stderr) if part).strip()
                executed.append(
                    {
                        "index": idx,
                        "command": cmd.display,
                        "returncode": int(proc.returncode),
                    }
                )
                output_chunks.append(
                    f"$ {cmd.display}\n{captured or '[no output]'}\n[rc={proc.returncode}]"
                )
                yield {
                    "type": "command_output",
                    "index": idx,
                    "returncode": int(proc.returncode),
                    "output": captured or "[no output]",
                }
            except FileNotFoundError:
                msg = "adb/fastboot command not found on server"
                executed.append(
                    {
                        "index": idx,
                        "command": cmd.display,
                        "returncode": 127,
                    }
                )
                output_chunks.append(f"$ {cmd.display}\n{msg}\n[rc=127]")
                yield {
                    "type": "command_output",
                    "index": idx,
                    "returncode": 127,
                    "output": msg,
                }
                break
            except subprocess.TimeoutExpired:
                msg = f"Command timed out after {timeout_per_command}s"
                executed.append(
                    {
                        "index": idx,
                        "command": cmd.display,
                        "returncode": 124,
                    }
                )
                output_chunks.append(f"$ {cmd.display}\n{msg}\n[rc=124]")
                yield {
                    "type": "command_output",
                    "index": idx,
                    "returncode": 124,
                    "output": msg,
                }

        raw_output = "\n\n".join(output_chunks)
        result, summary = self.summarize(nl_command, executed, raw_output)
        yield {
            "type": "final",
            "result": result,
            "summary": summary,
        }
        return executed, raw_output, summary, result

    @staticmethod
    def encode_sse(payload: dict) -> str:
        return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
