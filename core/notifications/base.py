"""The delivery contract: an address, a channel, and some words.

⚠️ A backend knows those three things and nothing else — none of our tables,
   and above all nothing about how old anybody is. The moment one of them
   learns that under-18s are told through a parent instead, changing provider
   means rewriting that rule — and it is a rule about this foundation, which no
   notification platform has ever heard of. core/tests.py greps this package
   for the names it must not contain, so do not spell them out even in a
   comment: the guard scans itself and everything around it.

Deciding who should be told, and at what address, is business logic and lives
in events/services.py::resolve_recipients(). That half is a permanent asset;
this half is replaceable, which is the entire point of splitting them.
See goal.md D22.
"""

from dataclasses import dataclass
from typing import Protocol, Sequence

from django.conf import settings
from django.utils.module_loading import import_string

EMAIL = "email"
SMS = "sms"


@dataclass(frozen=True)
class Message:
    """One thing to send to one address."""

    to: str          # an email address, a phone number, or a provider's id
    channel: str     # EMAIL or SMS
    subject: str
    body: str


@dataclass(frozen=True)
class DeliveryResult:
    """What the backend made of one message.

    ⚠️ "Accepted" is not "delivered". A provider can answer the first and only
       guesses at the second; neither answers "this person has no address at
       all", which is ours to work out and is why unreachable is computed on
       our side rather than read off a receipt (D22 ②).
    """

    message: Message
    accepted: bool
    detail: str = ""
    provider_ref: str = ""


class NotificationBackend(Protocol):
    """⚠️ send() answers with one result per message, in the order given, and
    does not raise: a provider refusing address 47 must still leave the caller
    able to say what happened to the other 99. The caller writes those verdicts
    down, so a backend that raises instead costs a record that cannot be
    rebuilt — the messages that already went out cannot be un-sent.

    🔴 **一个做网络 I/O 的 backend 必须给整批封顶**（2026-09-18），因为
       `send()` 跑在请求路径上，而那台机器一共只有 4 个线程
       （gunicorn workers 1 × threads 4）。单条超时不够：一批 N 条的最坏情况
       是 N 倍的单条上限，而 N 是一场活动报名的人数。
       ⚠️ 上面那一层接不住它 —— `--worker-class gthread` 的 `--timeout` 是
          **心跳**超时，卡住的线程不会被 arbiter 回收。
       ⚠️ 封顶之后**仍然一条消息一个 verdict**：上面那句「顺序对应、长度相等」
          不因为封顶而放宽。没轮到的那些是一个说得出理由的拒绝，不是缺席。
       今天只有 django_email 需要它（见那个文件）；console 和 locmem 不碰网络。
    """

    def send(self, messages: Sequence[Message]) -> list[DeliveryResult]: ...


def get_backend() -> NotificationBackend:
    """The configured backend. settings.NOTIFICATION_BACKEND names the class."""
    return import_string(settings.NOTIFICATION_BACKEND)()
