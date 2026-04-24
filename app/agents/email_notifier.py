import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.getenv("SMTP_HOST", "smtpus.qualcomm.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 25))
EMAIL_FROM = os.getenv("AKDW_EMAIL_FROM", "akdw-agent@qti.qualcomm.com")
EMAIL_TO = "nandam@qti.qualcomm.com"
EMAIL_LOG = os.path.join(os.path.dirname(__file__), "../data/email_log.json")


class EmailNotifier:

    EMAIL_TYPES = {
        "REVIEW_REQUIRED": {
            "subject": "🔍 [AKDW] Human Review Required — Agent Paused",
            "color": "#f59e0b",
            "icon": "🔍",
        },
        "MANUAL_TEST": {
            "subject": "🧪 [AKDW] Manual Testing Required",
            "color": "#3b82f6",
            "icon": "🧪",
        },
        "ARCHITECT_OBSERVATION": {
            "subject": "🏛️ [AKDW] Architect Observation / Enhancement Proposed",
            "color": "#8b5cf6",
            "icon": "🏛️",
        },
        "PHASE_COMPLETE": {
            "subject": "✅ [AKDW] Phase Complete — Next Phase Starting",
            "color": "#10b981",
            "icon": "✅",
        },
        "TASK_SUMMARY": {
            "subject": "📊 [AKDW] Agent Run Complete — Full Summary",
            "color": "#3b82f6",
            "icon": "📊",
        },
    }

    def send(
        self,
        email_type: str,
        subject_suffix: str,
        body_html: str,
        task_context: dict = None,
    ):

        meta = self.EMAIL_TYPES.get(
            email_type,
            self.EMAIL_TYPES["TASK_SUMMARY"],
        )
        subject = meta["subject"]
        if subject_suffix:
            subject += f": {subject_suffix}"

        html = f"""
          <html><body style="font-family:monospace;
                             background:#0a0e1a;color:#f1f5f9;
                             padding:24px">
            <div style="background:rgba(255,255,255,0.04);
                        border:1px solid rgba(255,255,255,0.08);
                        border-left:4px solid {meta['color']};
                        border-radius:12px;padding:24px;
                        max-width:720px">
              <h2 style="color:{meta['color']};margin-top:0">
                {meta['icon']} {subject}
              </h2>
              <p style="color:#94a3b8;font-size:12px">
                Generated: {datetime.utcnow().isoformat()} UTC |
                AKDW Dual Agent System
              </p>
              <hr style="border-color:rgba(255,255,255,0.08)">
              {body_html}
              {"" if not task_context else self._render_context(task_context)}
              <hr style="border-color:rgba(255,255,255,0.08)">
              <p style="color:#64748b;font-size:11px">
                This is an automated message from the AKDW
                Senior Architect Agent.<br>
                Reply to this email to provide approval or
                feedback (human-in-the-loop gate).
              </p>
            </div>
          </body></html>
          """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html, "html"))

        ok = False
        err = ""
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
            ok = True
            err = "sent"
        except Exception as e:
            print(f"[EmailNotifier] WARN: {e}")
            err = str(e)

        self._append_log(
            {
                "email_type": email_type,
                "subject": subject,
                "timestamp": datetime.utcnow().isoformat(),
                "ok": ok,
                "message": err,
            }
        )
        return ok, err

    def _append_log(self, row: dict):
        os.makedirs(os.path.dirname(EMAIL_LOG), exist_ok=True)
        logs = []
        if os.path.exists(EMAIL_LOG):
            try:
                with open(EMAIL_LOG, encoding="utf-8") as f:
                    logs = json.load(f)
            except Exception:
                logs = []
        logs.append(row)
        logs = logs[-50:]
        with open(EMAIL_LOG, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)

    def get_recent_logs(self, limit: int = 5):
        if not os.path.exists(EMAIL_LOG):
            return []
        try:
            with open(EMAIL_LOG, encoding="utf-8") as f:
                logs = json.load(f)
            return list(reversed(logs[-limit:]))
        except Exception:
            return []

    def _render_context(self, ctx: dict) -> str:
        rows = "".join(
            f"<tr><td style='color:#94a3b8;padding:4px 8px'>{k}</td>"
            f"<td style='color:#f1f5f9;padding:4px 8px'>{v}</td></tr>"
            for k, v in ctx.items()
        )
        return f"""
          <table style="width:100%;border-collapse:collapse;
                        margin-top:16px">
            <thead><tr style="border-bottom:1px solid
                               rgba(255,255,255,0.08)">
              <th style="text-align:left;color:#64748b;
                         padding:4px 8px">Field</th>
              <th style="text-align:left;color:#64748b;
                         padding:4px 8px">Value</th>
            </tr></thead>
            <tbody>{rows}</tbody>
          </table>"""
