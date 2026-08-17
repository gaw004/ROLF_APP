"""Report what this deployment is actually configured with, safe to paste.

⚠️ **The whole design constraint is that the output can be shared.** The way
   somebody checks a deployment today is a screenshot of the dashboard, and a
   screenshot of that page carries the SMTP password and the object-store keys
   in it — so the act of asking "did I fill this in right?" is what leaks them.
   Nothing here prints a secret. Where the value matters it prints a shape
   ("set, 32 characters", "an address at example.org"); where it does not, it
   prints nothing at all.

⚠️ And it answers a different question from the dashboard. A dashboard says the
   row exists. This says the port and the encryption agree, the From: address is
   at a domain the provider was told about, and — with --send-to — that the
   provider accepts a message. Every one of those can be wrong while the
   dashboard looks perfect.

Run it on the deployment, where the real environment is:

    python manage.py check_deployment
    python manage.py check_deployment --send-to somebody@example.org
"""

import os

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.message import EmailMessage
from django.core.management.base import BaseCommand

#: Substrings in a From: address that mean it is the provider's domain rather
#: than the foundation's. Sending from one of these works and is still wrong:
#: switching provider then changes the address every recipient has seen, and
#: whatever sending reputation was built up stays behind.
BORROWED_SENDER_DOMAINS = ("brevo", "sendinblue", "amazonses", "onrender.com",
                           "sendgrid", "mailgun", "postmarkapp")

OK, WARN, BAD = "  ok  ", " warn ", " WRONG"


class Command(BaseCommand):
    help = "Print a shareable report of this deployment's configuration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send-to",
            help="Send one real test message to this address. ⚠️ Use an address "
                 "that is not yours: your own provider lets your own mail "
                 "through, which is the one result that proves nothing.",
        )

    def handle(self, *args, **options):
        self.problems = []
        # ⚠️ 不是生产 settings 时，下面每一条「必须为 True」都会变成红的 ——
        #    而在一台开发机上它们本来就该是 False。留着不管的下场和 C3.4 那条
        #    HSTS 警告一样：报告永远是红的，于是没有人再读它。所以这里把严重性
        #    整体降一级，并且**在最上面说清楚为什么**。
        # ⚠️ `or ""`: under override_settings, SETTINGS_MODULE is None rather
        #    than the module's name, so reading it directly makes the command
        #    crash in exactly the place a test would exercise it.
        module = settings.SETTINGS_MODULE or os.getenv("DJANGO_SETTINGS_MODULE", "")
        self.is_deployment = "prod" in module
        self.stdout.write("")
        self.section("Settings module")
        self.line(
            OK if self.is_deployment else WARN,
            "DJANGO_SETTINGS_MODULE", module or "(unset)",
            "" if self.is_deployment
            else "not the production settings, so nothing below is a fault — "
                 "these values belong to this machine. Run this on the "
                 "deployment to check the deployment")

        self.email()
        self.https()
        self.errors()

        if options["send_to"]:
            self.send_one(options["send_to"])

        self.stdout.write("")
        if self.problems:
            self.stdout.write(self.style.ERROR(
                f"{len(self.problems)} thing(s) to fix:"))
            for problem in self.problems:
                self.stdout.write(f"  - {problem}")
        elif self.is_deployment:
            self.stdout.write(self.style.SUCCESS(
                "Nothing to fix in what this can see from here. ⚠️ What it "
                "cannot see: whether the DNS records are the provider's, and "
                "whether a stranger's inbox puts the message in spam. Those "
                "need --send-to and a look at the folder."))
        else:
            self.stdout.write(
                "No faults, but this is a development machine — the report "
                "above describes it, not the deployment.")
        self.stdout.write("")

    # --- the sections -----------------------------------------------------

    def email(self):
        self.section("Sending mail (C3.3)")
        host = getattr(settings, "EMAIL_HOST", "")
        port = getattr(settings, "EMAIL_PORT", None)
        user = getattr(settings, "EMAIL_HOST_USER", "")
        password = getattr(settings, "EMAIL_HOST_PASSWORD", "")
        sender = getattr(settings, "DEFAULT_FROM_EMAIL", "")

        # The host is not a secret and naming it is how a wrong one gets spotted.
        self.line(
            BAD if not host or host == "localhost" else OK,
            "EMAIL_HOST", host or "(empty)",
            "" if host and host != "localhost"
            else "still Django's default — the deployment is trying to send "
                 "through a mail server on its own machine, and there is none")
        # ⚠️ 端口和加密方式必须自洽，而它们不自洽时不报错，是**卡住**。
        encryption = "implicit TLS" if getattr(settings, "EMAIL_USE_SSL", False) else (
            "STARTTLS" if getattr(settings, "EMAIL_USE_TLS", False) else "none")
        self.line(
            OK if (port, encryption) in {(587, "STARTTLS"), (465, "implicit TLS"),
                                         (2525, "STARTTLS"), (25, "STARTTLS")}
            else WARN,
            "EMAIL_PORT", f"{port} with {encryption}",
            "" if port in {25, 465, 587, 2525}
            else "an unusual port; if the provider's page says otherwise, "
                 "believe the provider")

        for name, value in (("EMAIL_HOST_USER", user),
                            ("EMAIL_HOST_PASSWORD", password)):
            self.line(BAD if not value else OK, name, self.shape(value),
                      "" if value else "empty — the provider will refuse every "
                                       "message with an authentication error")

        # The From: address travels on every message this system sends, so it is
        # printed in full: it is the least secret thing here and the most often
        # wrong.
        borrowed = any(mark in sender.lower() for mark in BORROWED_SENDER_DOMAINS)
        self.line(
            BAD if not sender else (WARN if borrowed else OK),
            "DEFAULT_FROM_EMAIL", sender or "(empty)",
            "" if not borrowed else
            "this is the provider's own domain, not the foundation's. It works "
            "today and costs you the address and the reputation on the day you "
            "switch provider (C3.0)")
        if sender and "@" in sender and host:
            self.stdout.write(
                f"         └─ recipients will see mail from “{sender.split('@')[-1]}”; "
                "that domain is the one whose DKIM/SPF records have to exist")

        backend = getattr(settings, "EMAIL_BACKEND", "")
        if "console" in backend or "locmem" in backend or "dummy" in backend:
            self.line(WARN, "EMAIL_BACKEND", backend,
                      "nothing is actually sent with this backend")

    def https(self):
        self.section("HTTPS strictness (C3.4 / C5)")
        seconds = getattr(settings, "SECURE_HSTS_SECONDS", 0)
        a_year = 31536000
        self.line(
            OK if seconds else BAD, "SECURE_HSTS_SECONDS",
            f"{seconds} ({'provisional, C3.4' if seconds < a_year else 'C5 length'})",
            "" if seconds else "HSTS is off")
        if seconds >= a_year:
            # ⚠️ 过了这条线，静默自动失效，所以这两个必须是 True，否则
            #    check --deploy 会开始报警 —— 这正是设计要的提醒。
            for name in ("SECURE_HSTS_INCLUDE_SUBDOMAINS", "SECURE_HSTS_PRELOAD"):
                value = getattr(settings, name, False)
                self.line(OK if value else WARN, name, str(value),
                          "" if value else "C5 raised the max-age, so this one "
                                           "is now expected to be on too")
        for name in ("SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE",
                     "CSRF_COOKIE_SECURE"):
            value = getattr(settings, name, False)
            self.line(OK if value else BAD, name, str(value),
                      "" if value else "must be True in production")
        # ⚠️ 这两条一起看才有意义 —— C5 挂域名时只改一半的表现是「页面打得开、
        #    所有 POST 被拒」，读起来像表单坏了。
        self.line(OK, "ALLOWED_HOSTS", ", ".join(settings.ALLOWED_HOSTS) or "(empty)")
        self.line(
            OK if settings.CSRF_TRUSTED_ORIGINS else WARN,
            "CSRF_TRUSTED_ORIGINS",
            ", ".join(settings.CSRF_TRUSTED_ORIGINS) or "(empty)",
            "" if settings.CSRF_TRUSTED_ORIGINS
            else "every POST will be rejected while every page still loads")

    def errors(self):
        self.section("Error visibility (C3.8)")
        # The DSN carries a key, so only its presence is reported.
        dsn = os.getenv("SENTRY_DSN", "")
        self.line(OK if dsn else WARN, "SENTRY_DSN", self.shape(dsn),
                  "" if dsn else "unset, so nothing is reported anywhere but "
                                 "the log; that is a choice, not a fault")

    def send_one(self, address):
        self.section("Live send")
        self.stdout.write(f"  Sending one message to {address} …")
        # ⚠️ Its own connection, opened here, so that a failure is reported as
        #    itself. Going through the notification layer would answer a
        #    different question and hide this one behind an adapter.
        try:
            connection = get_connection(fail_silently=False)
            sent = EmailMessage(
                subject="River of Life Foundation — delivery test",
                body="If you are reading this, the deployment can send mail.\n"
                     "Nothing else was done and nobody's account was changed.\n",
                to=[address],
                connection=connection,
            ).send()
        except Exception as error:  # noqa: BLE001 — the point is to report it
            # ⚠️ `regardless`: a send that was explicitly asked for and did not
            #    happen is a fault on any machine. Softening it the way the
            #    configuration lines are softened would answer "did it work?"
            #    with a yellow line and a summary saying there is nothing to fix.
            self.line(BAD, "result", type(error).__name__, str(error),
                      regardless=True)
            return
        self.line(OK if sent else BAD, "result",
                  "accepted by the provider" if sent else "not accepted",
                  "" if sent else "the provider took the connection and "
                                  "refused the message",
                  regardless=True)
        self.stdout.write(
            "         └─ ⚠️ accepted is not delivered. Check the inbox, and "
            "check the spam folder — first messages from a shared IP pool land "
            "there, and nothing anywhere reports it.")

    # --- output -----------------------------------------------------------

    def section(self, title):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(title))

    def line(self, status, name, value, problem="", regardless=False):
        if status is BAD and not self.is_deployment and not regardless:
            # Right answer, wrong machine. See handle().
            status = WARN
        style = {OK: self.style.SUCCESS, WARN: self.style.WARNING,
                 BAD: self.style.ERROR}[status]
        self.stdout.write(f"  [{style(status)}] {name:<32} {value}")
        if problem:
            self.stdout.write(f"         └─ {problem}")
        if problem and status is BAD:
            self.problems.append(f"{name}: {problem}")

    @staticmethod
    def shape(value):
        """A secret's shape. ⚠️ Never any part of the secret itself.

        Not even the first few characters: a prefix is enough to identify which
        key it is, and this output exists to be pasted into a chat window.
        """
        return f"set, {len(value)} characters" if value else "(empty)"
