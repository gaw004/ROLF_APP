"""Django's own email backend. The fallback that depends on nothing external.

⚠️ Email only. A message asked for over SMS is reported as not accepted rather
   than quietly dropped — with this backend configured, an SMS-only recipient
   is a real gap, and D22 requires that gaps be visible rather than silent.

⚠️ **Nothing in here raises.** One address failing must not cost the caller the
   answers for the other ninety-nine — it records them, and a batch that dies
   halfway leaves it with no way to tell who got a message and who did not.
   That was already how NovuBackend behaved; this one used to be the exception,
   with fail_silently=False on every send. Reporting a failure as
   accepted=False is not "failing silently": the caller writes it down (see
   events/services.py::notify_event_change) and the notification record shows
   it. Swallowing means nobody is told; this means the *right* thing is told.

⚠️ One SMTP connection for the whole batch, not one per message. A hundred
   signups used to mean a hundred connect / authenticate / quit cycles, which
   providers rate-limit in their own right — the failure would arrive as
   refused connections partway down a list that had been fine a moment earlier.

🔴 **整批有时间预算，因为这个循环跑在请求路径上**（2026-09-18）。
   `EMAIL_TIMEOUT`（prod.py）管的是**一条**消息能卡多久；没有这里这个数的话，
   最坏情况只是从「永远」变成「N × 每条超时」，而 `notify_event_change()` 的 N
   是一场活动报名的人数。两个上限缺一不可。

   ⚠️ 这件事**上面那一层接不住**：`--worker-class gthread` 让 `--timeout 60`
      成为心跳超时而不是请求超时，一个卡住的线程不会被 arbiter 回收。整台机器
      一共 4 个线程（workers 1 × threads 4）。

   ⚠️ 超预算的消息**仍然各自带一个 verdict 回去**，返回的列表永远是满长度。
      调用方确实会把缺的补成失败，但它那段注释把短列表定义成「backend 坏了」，
      而这不是坏，是一个说得出理由的拒绝。
"""

import time

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

from .base import EMAIL, DeliveryResult, Message

#: 超出预算、因而**根本没有尝试**的那些消息带的说明。
#: ⚠️ 要说得出「为什么没轮到你」——「没发出去」和「这一批超时了」是两件事，
#:    而管理员拿着这句话决定下一步（再发一次？还是先去问 provider）。
OUT_OF_TIME = ("Not attempted: this batch ran out of its time budget "
               "(EMAIL_BATCH_BUDGET_SECONDS). Send to the remaining people again.")


class DjangoEmailBackend:
    def __init__(self, budget_seconds=None, clock=time.monotonic):
        """
        ⚠️ 两个参数都有默认值，因为 `get_backend()` 是 `import_string(...)()`
           —— 不传参数地构造。它们存在是为了让测试能注入一个按剧本走的时钟。

        ⚠️ **注入时钟，而不是全局 mock 掉 `time.monotonic`**：这个进程里还有
           别的东西在读同一个时钟（logging、数据库驱动），全局打补丁会把它们
           一起改掉，而那种失败根本指不回这里。同 `check_in(at=...)` 的理由。
        """
        self.budget_seconds = (settings.EMAIL_BATCH_BUDGET_SECONDS
                               if budget_seconds is None else budget_seconds)
        self.clock = clock

    def _out_of_time(self, started):
        """这一批的预算用完了没有。

        🔴 **`<= 0` 是关闭，不是「预算为零、一封都不发」。** 这是在两个失败里
           挑伤害小的那个：当成「全部拦下」的话，有人在面板上手误填一个 0，
           表现是**全站邮件静默停发** —— 没有任何东西报错，人就是没收到。
           当成关闭，最坏只是退回到没有整批上限，而每条仍有 EMAIL_TIMEOUT。
           ⚠️ 同 `MEMORY_PROBE_SECONDS` 那条口径：0 是一个被支持的值，
              不是一种把它弄坏的方式。而它不会成为发布出去的默认值 ——
              `core.tests.UnboundedWaitGuardTests` 钉着 ≥ 20 秒。
        """
        if self.budget_seconds <= 0:
            return False
        return self.clock() - started >= self.budget_seconds

    def send(self, messages: list[Message]) -> list[DeliveryResult]:
        # ⚠️ 计时**从这里开始，在建连之前**。`EMAIL_BATCH_BUDGET_SECONDS` 说的是
        #    整批的上限；把建连那一段排除在外的话，最坏总时长其实是
        #    `EMAIL_TIMEOUT + 预算`，而不是它字面写的那个数。
        started = self.clock()
        connection = get_connection(fail_silently=False)
        # OSError covers what SMTP actually goes wrong with: smtplib.SMTPException
        # is a subclass of it, and so are the socket and TLS errors underneath.
        try:
            connection.open()
            opening_failed = ""
        except OSError as error:
            # Nothing was sent, so every message in the batch is a failure with
            # the same cause. Reported rather than raised, for the reason above.
            opening_failed = str(error)

        try:
            results = []
            for message in messages:
                # ⚠️ 在每一条**之前**问，不是之后：一条刚刚花掉剩余全部预算的
                #    消息本身是发出去了的，它必须拿到自己真实的 verdict。
                #
                # 🔴 **连不上的时候不问预算**，因为两个原因可以同时成立，而要报的
                #    是**根因**那一个。说「这一批超时了」是真的，但它是后果：
                #    管理员拿着它会去重发一次，而重发同样连不上；拿着
                #    「connection refused」才会去看 provider 那边。
                if not opening_failed and self._out_of_time(started):
                    results.append(DeliveryResult(
                        message=message, accepted=False, detail=OUT_OF_TIME))
                    continue
                results.append(self._one(connection, message, opening_failed))
            return results
        finally:
            connection.close()

    def _one(self, connection, message: Message, opening_failed: str) -> DeliveryResult:
        if message.channel != EMAIL:
            return DeliveryResult(
                message=message, accepted=False,
                detail="This backend can only send email.",
            )
        if opening_failed:
            return DeliveryResult(
                message=message, accepted=False, detail=opening_failed)

        mail = EmailMessage(
            subject=message.subject,
            body=message.body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[message.to],
            connection=connection,
        )
        try:
            sent = mail.send(fail_silently=False)
        except OSError as error:
            # ⚠️ Where a daily quota lands. The provider answers one message
            #    with a refusal, and the ones after it usually get the same —
            #    which is exactly why this returns instead of raising: the
            #    caller ends up with a list of who did and did not make it,
            #    rather than an exception where a record should have been.
            return DeliveryResult(message=message, accepted=False, detail=str(error))
        return DeliveryResult(
            message=message, accepted=bool(sent),
            detail="" if sent else "The mail server did not accept it.",
        )
