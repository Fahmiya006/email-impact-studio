"""Outgoing email for password resets (standard library smtplib).

Configure with environment variables:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY = starttls (default) | ssl | none
Without SMTP_HOST the message is not sent: its text, including the reset link, is written to the server log.
That fallback is for local use only. Anyone who can read the log can reset passwords.
"""
import os
import smtplib
import sys
from email.message import EmailMessage


class Mailer:
    def __init__(self, host=None, port=587, user=None, password=None, sender=None, security="starttls"):
        self.host, self.port, self.user, self.password = host, int(port), user, password
        self.sender, self.security = sender or user or "no-reply@localhost", security

    @classmethod
    def from_env(cls, env=os.environ):
        return cls(env.get("SMTP_HOST"), env.get("SMTP_PORT", "587"), env.get("SMTP_USER"), env.get("SMTP_PASSWORD"),
                   env.get("SMTP_FROM"), env.get("SMTP_SECURITY", "starttls").lower())

    @property
    def configured(self):
        return bool(self.host)

    def send(self, to, subject, body):
        """Returns True if handed to an SMTP server; False (and logs the message) when SMTP is not configured or fails."""
        if not self.configured:
            sys.stderr.write(f"[mail not configured] To: {to}\nSubject: {subject}\n{body}\n")
            return False
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        try:
            if self.security == "ssl":
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=20)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=20)
            with server:
                if self.security == "starttls":
                    server.starttls()
                if self.user:
                    server.login(self.user, self.password or "")
                server.send_message(msg)
            return True
        except Exception as ex:                       # never surface SMTP details to the user
            sys.stderr.write(f"[mail error] could not send to {to}: {ex!r}\n")
            return False
