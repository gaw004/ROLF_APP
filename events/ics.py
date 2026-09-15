"""一场活动 → 一段 iCalendar 文本（2026-09-14，「Add to calendar」）。

纯函数层，和 `events/recurrence.py`、`events/schedule.py` 同一层：不碰 ORM、
不碰 HTTP、不碰模板。收一串普通的 `Occasion` 记录，交回一个 `str`；视图负责
查询、拼记录、发响应头。边界画在这里，是因为 RFC 5545 的那几条规矩
（折行、转义、UID）每一条错了都**不报错**，只是某一个日历客户端安静地
读错或者干脆不读 —— 而这种东西只有在一个收得下手写输入的纯函数里才测得动。

🔴 **UID 稳定 + SEQUENCE 递增，就是这个功能存在的全部理由。**

   人按下「Add to calendar」，日历里多一条。活动改了时间，人**再按一次**，
   日历里应该是**那一条变了**，不是多出第二条。做到这件事只靠两个字段：
   `UID` 认人（同一场活动永远是同一个字符串），`SEQUENCE` 认新旧
   （大的覆盖小的）。错了任何一个，表现都是「日历里躺着两条时间不一样的
   同名活动」，而人会去赴其中一场 —— 至于是哪一场，看他当天先看到哪一条。

   ⚠️ `UID` 里必须带调用方自己的域名（`uid_for()` 帮着拼）。RFC 5545 §3.8.4.7
      要求它全球唯一，而一个光是 `42` 的 UID 会和别人家日历里的 `42` 撞上 ——
      撞上的表现是对方的活动被我们的覆盖掉。

🔴 **`sequence` 是调用方算好了交进来的，而「算好」比它看上去难。**

   一门课是一个 `Event` 加 N 行 `Session`：VEVENT 的时间取自 `Session`，
   而 SUMMARY / LOCATION / DESCRIPTION 取自**父 `Event`**。于是有人把课改名、
   或者换了教室时，`Session` 那一行的 `updated_at` **一个字都不动** ——
   `sequence` 原地不动，所有已经收下这条日程的日历就继续显示旧名字旧地址，
   而重新下载一遍也没用：SEQUENCE 没涨，客户端认为自己手上的已经是最新的。
   一次「改了、通知了、没人收到」的静默失败。

   所以规矩写在这里，收记录的人照做：**凡是喂进这条 VEVENT 任何一个字段的
   时间戳，全部折进 `sequence` 里** —— 课的那一条就是
   `max(session.updated_at, event.updated_at)`。本模块只认最后那一个整数，
   它没有办法替谁检查这件事，所以它把这件事写在最显眼的地方。

🔴 **时刻一律写 UTC（`...Z`），不写 `TZID=America/Los_Angeles` + `VTIMEZONE`。**

   两条路都合法，这里选前者，理由和代价都如实写下来：

   · 选它，是因为这个文件里的每一场都是**已经落实的绝对时刻**。它不带 RRULE
     —— 一门十二讲的课是十二个 VEVENT（见 `calendar_for`），每一讲的起止在
     `recurrence.occasions()` 那一层就已经按墙钟展开、落成了 aware datetime。
     对一个绝对时刻来说 UTC 是精确的：文件下到一台设成东京时区的电脑上，
     客户端换算出来的仍然是同一个瞬间；夏令时那两个早上也没有任何歧义，
     因为歧义早在上游解决掉了。而 `VTIMEZONE` 要手写 STANDARD / DAYLIGHT
     两个子块加各自的 RRULE，写错了的表现是客户端按自己的猜测摆放这场活动
     —— 三十行代码，换一个**这个文件用不上**的能力。

   · 代价，两条，都是真的：
     一是 UTC 钉死的是瞬间，不是墙钟。哪天美国把夏令时改了法，一个早就
     下到别人日历里的「晚上七点」会变成六点或八点，而 `TZID` 那条路上
     客户端更新了时区库就会自己跟着走。这里认下这个代价，是因为生成窗口
     只有一年（`recurrence.HORIZON_MONTHS`），一年之内改法并且生效的事没有
     发生过；真发生了，重新生成一遍就是了。
     二是这条路**不允许这个文件将来带 RRULE**。`RRULE` + UTC 的 `DTSTART`
     会让每周七点的课在十一月第一个周日之后整体变成六点 —— 正是
     `recurrence.py` 顶上那条 🔴 讲的同一个坑，换了个地方。真要在 .ics 里
     发一条规则出去，就必须先把这里改成 `TZID` + `VTIMEZONE`，没有别的办法。

🔴 **每一条 DESCRIPTION 的末尾都由**本模块**补上一句「这份副本不会自己更新」**
   （`STALE_COPY_NOTE`）。补的人是这里，不是调用方 —— 调用方只负责交进活动
   自己的说明和活动页地址，于是它**没有机会忘记**这一句。理由写在那个常量
   旁边，一句话是：这个文件是活动时间地点的第二份拷贝，而它住在别人手机里。

⚠️ **没有 `ATTENDEE`，而且是故意的。** 这个文件是一个人给**自己**下的一份
   副本，不是基金会发出去的邀请。把下载者的姓名和邮箱写进 ATTENDEE 有两个
   后果：文件被转发出去时它带着这个人的身份一起走；而且有些客户端见到
   ATTENDEE 就认定这是一封会议邀请，开始向 ORGANIZER 那个地址提供
   Accept / Decline 的回执 —— 而那个地址根本没有任何东西在等这些回执。
   `ORGANIZER`（办这场活动的 ministry）留着，它说的是「这是谁办的」。

⚠️ 这个模块对**缓存**没有意见，但它也答不了那个问题：同样一串记录进来永远是
   同样的字节出去，而「这一份是不是某个人专属的」（只挑了几讲的学员）只有
   调用方知道。所以响应头是调用方的事 —— 一份个人化的文件绝不能被共享缓存
   收下。本模块不会、也没有办法替谁挡住这件事。

⚠️ 不引入任何新依赖（不上 `icalendar` 包）。这里要写的是一份属性固定的文本，
   而那个库换来的主要是解析能力和一个对象模型 —— 我们不解析任何人的日历。
   照 D18 的落点规矩，这本来就该是一个纯函数。
"""

import datetime
import html
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from core.timeutils import local_now

#: RFC 5545 §3.1：内容行之间一律 CRLF，不是 `\n`。
#:
#: ⚠️ 只写 `\n` 的文件在多数客户端上照常打开，所以它测不出来也看不出来 ——
#:    直到遇上一个严格的解析器（实测最爱挑剔的是 Exchange 那条路），
#:    表现是「导入了 0 个活动」，没有任何一行说明哪里不对。
CRLF = "\r\n"

#: 一行最多几个**字节**（RFC 5545 §3.1「lines SHOULD NOT be longer than
#: 75 octets, excluding the line break」）。
#:
#: 🔴 **是字节不是字符，而这个区别只有非 ASCII 的内容才暴露得出来。**
#:    一行中文摘要按「75 个字符」折，实际是 225 字节，超出两倍；按字节折但
#:    折在一个多字节字符的**中间**，则是把一个 UTF-8 序列劈成两半 —— 有的
#:    客户端整条拒绝，有的画出一串乱码，而两种都不会告诉你原因。
#:    `_fold()` 退到字符边界处理这件事。
FOLD_LIMIT = 75

#: 提前多久响。⚠️ 一小时是拍板的默认值，不是配置：这个文件下出去之后就归
#:    对方的日历管了，我们改不动它 —— 所以它只需要是一个不讨人厌的值。
ALARM_LEAD = datetime.timedelta(hours=1)

#: 🔴 **这句话不是客套，删掉它会让这个功能变成本项目一直在拆的那种东西。**
#:
#:    一份下出去的 .ics 是活动时间和地点的**第二份拷贝，而且住在别人手机里**。
#:    基金会一改活动，那份拷贝当场就是错的 —— 而且错得没有声音，就错在这个人
#:    唯一会去看的那个地方。整个项目为「同一件事有两处记录」的形状返工过好几轮
#:    （D14：规则只许有一处）。这一份拷贝躲不掉，那它至少要**承认自己是拷贝**。
#:
#:    所以它由本模块追加，不由调用方拼 —— 拼得出来的东西就忘得掉。
#:
#: ⚠️ 用词是定死的：两句短话，不惊叹、不道歉，说清两件事 ——
#:    (a) 活动改了这一条不会跟着改；(b) 最新的说法在活动页上，地址就在眼前。
#:    再长就没人读，而没人读的免责声明等于没有。
#:
#: ⚠️ 它同时是两条转义规则的交叉点，`{url}` 和那个空行各占一条：换行要变成
#:    字面的 `\n`（TEXT 转义），而里面那个网址**不能**按 URI 编码 —— 它此刻
#:    身处一个 TEXT 值里，`:` 和 `/` 在这里都是普通字符。
STALE_COPY_NOTE = ("This entry will not change if the event does. "
                   "The current details are on the event page: {url}")

#: 没有活动页地址时只说得出前半句。
#:
#: ⚠️ 这是一个**退化的情况**，不是一个选项：调用方应当永远给得出 `url`，
#:    因为「这一条可能已经过时」而又说不出该去哪里看，是一句只制造不安、
#:    不提供出路的话。留着它，是因为一条没有地址的记录仍然不该悄无声息。
STALE_COPY_NOTE_WITHOUT_A_LINK = "This entry will not change if the event does."


@dataclass(frozen=True)
class Occasion:
    """要写进日历的一场，已经和 ORM 断开了关系。

    ⚠️ 不是 `schedule.Occurrence`。那个里面装着一个真的 `Event` 对象（它画的是
       屏幕上的卡片，随时可以再问模型一句）；这个里面只有字符串和时刻，
       因为这个模块不许碰数据库 —— 它要能用一串手写的记录测完。

    ⚠️ `start` / `end` 必须是 aware datetime。naive 的会被 `_utc()` 当场拒绝，
       而不是按服务器本地时区猜一个 —— 猜出来的那个在这台笔记本上是对的，
       在 Render 上（UTC）差七八个小时，两边都不报错。
    """

    uid: str
    summary: str
    start: datetime.datetime
    end: datetime.datetime
    location: str = ""
    description: str = ""
    url: str = ""
    #: 见模块顶上那条 🔴：调用方把喂进这条 VEVENT 的**所有**时间戳折成一个数。
    sequence: int = 0
    cancelled: bool = False
    #: 一个邮箱地址或者一个 URI，没有就是 None。
    #:
    #: ⚠️ 只收地址，不收「名字 <地址>」。显示名在 iCalendar 里是一个 `CN=`
    #:    参数，而参数值一旦含有 `:` `;` `,` 就得用双引号裹起来，且双引号本身
    #:    **无论如何都放不进去**（RFC 5545 §3.2）—— 一条为了一个显示名而单开的
    #:    转义规则，不值得。
    organizer: str | None = None


def occasion(*, uid, summary, start, end, location="", description="", url="",
             sequence=0, cancelled=False, organizer=None):
    """造一条记录。全部关键字参数。

    ⚠️ 只收关键字，是因为这些字段里有四个连着的字符串（location / description
       / url / organizer）—— 按位置传，两个换了位置的参数在任何一层都不会报错，
       只是活动的地点栏里写着它的说明。
    """
    return Occasion(
        uid=uid, summary=summary, start=start, end=end, location=location,
        description=description, url=url, sequence=int(sequence),
        cancelled=bool(cancelled), organizer=organizer,
    )


def uid_for(kind, pk, host):
    """一个稳定的 UID：`"session-7@rolf-v.org"`。

    🔴 **它必须只由「这是哪一行」决定。** 不许掺进时间、随机数、或者任何一个
       「这次下载」才知道的东西 —— 掺了之后每按一次按钮都是一条**新**活动，
       日历里于是排着同一场活动的五个副本，而这正是整个功能要防的那一件事。

    ⚠️ `kind` 分命名空间：`Event` 的 42 号和 `Session` 的 42 号是两件事，
       而光有主键的话它们是同一个 UID，后下载的那个会覆盖掉前一个。

    ⚠️ 带上 `host`，UID 才是全球唯一的（RFC 5545 §3.8.4.7）。不带的话，
       别人家日历里恰好也有一个 `session-7` 的活动就会被我们的顶掉。
    """
    return f"{kind}-{pk}@{host}"


# --- RFC 5545 的三条硬规矩 ---------------------------------------------------


def _escape_text(value):
    """TEXT 值的转义：反斜杠、分号、逗号、换行。

    🔴 **反斜杠必须第一个换。** 放在后面的话，前面几步刚插进去的那些反斜杠
       会被再转义一遍：`a,b` 先变成 `a\\,b`，再把反斜杠翻倍就成了 `a\\\\,b` ——
       客户端读出来是「a\\」和「b」两个值，而不是一句带逗号的话。
       这是这段代码里唯一一个顺序敏感的地方。

    ⚠️ **冒号不转义。** RFC 2445 当年要求转义它，RFC 5545 取消了这条 ——
       现在的 ABNF 里冒号是合法的 TEXT 字符。照旧转义的后果是所有带时间的
       摘要都长出反斜杠：`Prayer \\: 7pm`，肉眼可见，但只在生产的日历里可见。

    ⚠️ 先把 CRLF / CR 都归一成 LF 再换。少了这一步，一段 Windows 换行的说明
       会留下一个孤零零的 `\\r` 在行里，而它在 iCalendar 里是**折行**的一半。
    """
    text = str(value or "")
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;").replace(",", "\\,")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", "\\n")


def _fold(line):
    """一行超过 75 **字节**就折，而且绝不折在一个字符中间。

    折法是 RFC 5545 §3.1 的那一条：CRLF 加一个空格，续行的那个空格
    **算在它自己的 75 里面** —— 所以第一段能装 75 字节，后面每段只有 74。
    这半字节的差别是规矩里最容易漏掉的一格，漏了就是每一条续行都超一格。

    🔴 退回字符边界靠的是「续字节的高两位是 10」（`0x80`/`0xC0`）。
       不退的话，一段中文摘要会被从某个字的第二个字节劈开 —— 解析器拿到两个
       都不合法的 UTF-8 片段，有的客户端整份文件不读，有的画出乱码。
       两种失败都不会说出「第 3 行折错了」这句话。

    ⚠️ 折在转义序列（`\\,`）中间是**允许**的：解开折行发生在解析属性之前，
       两半拼回去就是原来那一对。这里不为它多写一条规则。
    """
    raw = line.encode("utf-8")
    if len(raw) <= FOLD_LIMIT:
        return line
    pieces, start, room = [], 0, FOLD_LIMIT
    while start < len(raw):
        cut = min(start + room, len(raw))
        # ⚠️ `cut < len(raw)` 不能省：正好切在末尾时 raw[cut] 会越界。
        while cut < len(raw) and raw[cut] & 0xC0 == 0x80:
            cut -= 1
        pieces.append(raw[start:cut])
        start, room = cut, FOLD_LIMIT - 1
    return (CRLF + " ").join(piece.decode("utf-8") for piece in pieces)


def _utc(moment):
    """一个瞬间 → `20260914T190000Z`。

    🔴 naive 的当场拒绝，**不**按服务器本地时区猜。`astimezone()` 对 naive 值
       会拿系统时区去补，于是这台笔记本（PT）上算出来是对的，Render 上（UTC）
       整整差七八个小时 —— 两边都不报错，只是日历上的活动挪了大半天。
       D16 讲的是同一件事，这里是它在时间**格式化**一侧的出口。
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(
            f"ics needs timezone-aware datetimes; got a naive {moment!r}")
    return moment.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _line(name, value):
    return _fold(f"{name}:{value}")


# --- 一份日历 ----------------------------------------------------------------


def calendar_for(occasions, *, prodid_host, stamp=None):
    """一串记录 → 一整份 VCALENDAR 文本。

    一门十二讲的课是**一个文件里的十二个 VEVENT**，不是十二个文件。这是
    RFC 5545 本来的形状，也是人要的形状：按一次按钮，十二讲一起进日历。

    `prodid_host` 是调用方的域名，只用来拼 PRODID。

    ⚠️ `stamp`（DTSTAMP）是这个模块**唯一**问「现在几点」的地方，而且可以注入
       —— 不注入就走 `core.timeutils.local_now()`（D16 只此一处）。可注入是为了
       测试能钉住整份文件的字节，而不是去 mock 一个时钟。

    ⚠️ 空列表会交回一份没有任何组件的 VCALENDAR，而那个东西按 RFC 5545 §3.6
       **是不合法的**（至少要有一个组件）。这里仍然照交不抛异常：调用方拿到
       它的场合是「这个人一讲都没选」，而那时候一个 500 比一份空文件更难解释。
       正确的做法是页面上根本不给这个按钮 —— 但那是页面的事。

    ⚠️ 不写 `METHOD:PUBLISH`。带上它，Outlook 会把这份文件当成一封 iTIP 会议
       消息来处理（连带 ATTENDEE 那一套，见模块顶上）；不带，它就是一份普通的
       导入文件，而这正是我们要的语义。
    """
    stamped = _utc(stamp or local_now())
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        # ⚠️ PRODID 是 TEXT 值，所以域名也要走转义 —— 它是调用方传进来的。
        _line("PRODID", _escape_text(f"-//River of Life//{prodid_host}//EN")),
        "CALSCALE:GREGORIAN",
    ]
    for one in occasions:
        lines.extend(_vevent(one, stamped))
    lines.append("END:VCALENDAR")
    # ⚠️ 末尾也有一个 CRLF：iCalendar 是一个行流，最后一行同样要结束掉。
    return CRLF.join(lines) + CRLF


def _vevent(one, stamped):
    """一场。交回还没拼起来的那几行。"""
    lines = [
        "BEGIN:VEVENT",
        # ⚠️ UID 也是 TEXT（RFC 5545 §3.8.4.7），照样转义 —— 一个带逗号的 UID
        #    不转义就被读成两个值，于是这场活动**每次**都是新的一场。
        _line("UID", _escape_text(one.uid)),
        _line("DTSTAMP", stamped),
        _line("DTSTART", _utc(one.start)),
        _line("DTEND", _utc(one.end)),
        _line("SUMMARY", _escape_text(one.summary)),
        _line("SEQUENCE", int(one.sequence)),
        # 🔴 取消了就说取消了。少了这一行，被取消的活动在日历上和照常举行的
        #    长得一模一样 —— 而人是照着日历出门的。
        _line("STATUS", "CANCELLED" if one.cancelled else "CONFIRMED"),
        _line("TRANSP", "OPAQUE"),
    ]
    # ⚠️ DESCRIPTION 永远写，哪怕活动本身一个字的说明都没有 —— 那一句
    #    「这份副本不会自己更新」是无条件的（见 STALE_COPY_NOTE）。
    lines.append(_line("DESCRIPTION", _escape_text(_described(one))))
    if one.location:
        lines.append(_line("LOCATION", _escape_text(one.location)))
    if one.url:
        # ⚠️ URL 是 URI 值，**不**走 TEXT 转义。转了的话 query string 里的
        #    `,` 会变成 `\,`，客户端点开的是一个 404 —— 而它看起来完全正常。
        #
        # 🔴 于是同一个网址在同一条 VEVENT 里有**两种写法**，而两种都对：
        #    这一行是裸的，而 DESCRIPTION 里那一份（`STALE_COPY_NOTE` 带着它）
        #    是转义过的 —— 在那里它只是一串普通字符，逗号照 TEXT 的规矩走。
        #    看着像不一致，把它们统一成任何一种都会弄坏另一个。
        lines.append(_line("URL", one.url))
    if one.organizer:
        lines.append(_line("ORGANIZER", _cal_address(one.organizer)))
    if not one.cancelled:
        # ⚠️ 取消的那些不响。一个为「已经不办了」的活动在一小时前敲响的闹钟，
        #    是这个功能能造出来的最气人的一种输出。
        lines.extend(_valarm(one))
    lines.append("END:VEVENT")
    return lines


def _described(one):
    """活动自己的说明，后面空一行，再跟那句「这份副本不会自己更新」。

    ⚠️ 顺序是定的：活动自己的话在**上面**。打开一条日程先看到的应该是这场
       活动在说什么，不是一段关于这个文件的声明 —— 一份把免责声明顶在最前面的
       说明，等于把真正的内容推到了折叠线以下。

    ⚠️ 隔一个空行，不是隔一个换行。挨着的两段会被读成同一段的下一句，于是
       「这一条不会自己更新」听起来像是在说这场活动的某件事。

    ⚠️ 这里交回的是**没有转义过**的文本，转义在 `_vevent` 里统一走
       `_escape_text()` —— 在这里先转义一半，那两个换行会被再转义一次。
    """
    note = (STALE_COPY_NOTE.format(url=one.url) if one.url
            else STALE_COPY_NOTE_WITHOUT_A_LINK)
    return f"{one.description}\n\n{note}" if one.description else note


def _valarm(one):
    """提前一小时的提醒。

    🔴 `ACTION:DISPLAY` 的 VALARM **必须**带 `DESCRIPTION`（RFC 5545 §3.6.6 里
       它是 REQUIRED）。漏掉它是这一块最常见的写法错误，而后果是整个 VALARM
       被客户端丢掉 —— 活动照常导入，只是永远不响，没有任何提示。

    ⚠️ `TRIGGER` 的默认值类型就是 DURATION、默认锚在 DTSTART 上，所以
       `-PT1H` 已经是完整的意思，不必再写 `RELATED=START`。
    """
    minutes = int(ALARM_LEAD.total_seconds() // 60)
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        _line("DESCRIPTION", _escape_text(one.summary)),
        _line("TRIGGER", f"-PT{minutes // 60}H" if minutes % 60 == 0
              else f"-PT{minutes}M"),
        "END:VALARM",
    ]


def _cal_address(organizer):
    """ORGANIZER 的值是一个 CAL-ADDRESS，也就是一个 URI。

    ⚠️ 光一个邮箱地址不是 URI —— 少了 `mailto:` 前缀，一部分客户端整条属性
       丢掉，一部分把它当成显示名，两种都不报错。
    """
    return organizer if ":" in organizer else f"mailto:{organizer}"


# --- 两条深链 ----------------------------------------------------------------
#
# 🔴 **两家都只收得下一场活动，而且两家都不认 UID。** 这两件事一起决定了
#    界面能长成什么样，所以写在这里而不是留给调用方去发现：
#
#    · **一次一场。** Google 的 `render?action=TEMPLATE` 和 Outlook 的
#      `deeplink/compose` 都只有一组 `text`/`dates`（`subject`/`startdt`）。
#      一门十二讲的课没有办法用一条链接送出去 —— 要么这两颗按钮只对**单场**
#      活动出现，要么它们指的是「下一讲」而页面得把这一点说出来。
#      整门课只有 .ics 那条路走得通。
#
#    · **按两次就是两条。** 它们打开的是一张**新建活动**的草稿，UID 由对方的
#      日历自己发，所以再点一次得到的是第二条，而不是把第一条改掉。
#      整个功能开头那条 🔴 讲的「更新而不是重复」只在 .ics 这条路上成立。
#      界面上该说清楚哪一颗是「下载」哪两颗是「去网页新建」。
#
#    · 同理，它们也送不出 `STATUS:CANCELLED`。下面用标题前缀顶上去，
#      见 `_deep_link_summary()`。
#
# ⚠️ 两家的参数名和日期格式是 2026-09-14 照各自的文档核过的，都用 UTC，
#    和 .ics 那边的决定一致（见模块顶上）：
#    Google `dates=YYYYMMDDTHHMMSSZ/YYYYMMDDTHHMMSSZ`（去掉 Z 就得配 `ctz`），
#    Outlook `startdt=YYYY-MM-DDTHH:MM:SSZ`（去掉 Z 就按对方本地时区读）。
#    ⚠️ 两种「去掉 Z」的写法都不能用：那是拿**我们**的墙钟时间去喂**对方**的
#       时区，一个在纽约的人会把七点的活动记到晚上十点。

GOOGLE_TEMPLATE = "https://calendar.google.com/calendar/render"
OUTLOOK_COMPOSE = "https://outlook.live.com/calendar/deeplink/compose"


def google_link(one):
    """「Add to Google Calendar」那一颗按钮的地址。

    ⚠️ Google 没有 URL 这个字段，所以活动页的链接只能待在 `details` 里 ——
       而它本来就在那儿：`_described()` 那句话里带着它。没有它的话，一条进了
       日历的活动**回不到**我们的页面，而那是它唯一能看到最新说法的地方。
    """
    params = {
        "action": "TEMPLATE",
        "text": _deep_link_summary(one),
        "dates": f"{_utc(one.start)}/{_utc(one.end)}",
        "details": _deep_link_body(one),
        "location": one.location,
    }
    return f"{GOOGLE_TEMPLATE}?{_query(params)}"


def outlook_link(one):
    """「Add to Outlook」那一颗按钮的地址。

    ⚠️ `path` 和 `rru=addevent` 两个都不能省：少了它们这条链接打开的是收件人的
       日历首页，不是新建草稿 —— 一个「按了没反应」的按钮。

    ⚠️ 用的是 `outlook.live.com`（个人账号）。公司账号那一份在
       `outlook.office.com` 上，参数一模一样，而我们分不出访问者是哪一种 ——
       走错的那一半会被对方跳去登录页再转回来，多一步，但到得了。
       代价如实说：这是两条路里更常见的那一条，不是对所有人都最短的那一条。
    """
    params = {
        "path": "/calendar/action/compose",
        "rru": "addevent",
        "subject": _deep_link_summary(one),
        "startdt": _iso_utc(one.start),
        "enddt": _iso_utc(one.end),
        "body": _deep_link_body(one),
        "location": one.location,
        # ⚠️ 明写 false。不写的话 Outlook 对某些日期格式会自己猜成全天活动，
        #    而一个被记成「全天」的活动在日历上**没有时刻**。
        "allday": "false",
    }
    return f"{OUTLOOK_COMPOSE}?{_query(params)}"


def _iso_utc(moment):
    """Outlook 要的那种写法：`2026-09-14T19:00:00Z`。

    ⚠️ 先借 `_utc()` 挡一道 naive 的（它会抛），再重新排一遍分隔符 ——
       两处各写一份 aware 检查，迟早只有一处会记得改。
    """
    _utc(moment)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deep_link_summary(one):
    """标题。取消了的在前面挂一句。

    ⚠️ 深链送不出 `STATUS:CANCELLED`（见上面那条 🔴），所以取消只能写进标题。
       另一条路是取消时直接交回 None、逼调用方分叉 —— 不选它，是因为那个分叉
       忘了写也不会报错，而忘了的后果是一条看起来完全正常的活动进了别人日历。
       写进标题至少是**这个模块**说得出口的话。
    """
    return f"CANCELLED: {one.summary}" if one.cancelled else one.summary


def _deep_link_body(one):
    """说明 + 那句「这份副本不会自己更新」，按 HTML 处理。

    ⚠️ 走的是和 .ics 同一个 `_described()`，因为**过时的方式一模一样**：
       深链造出来的也是一条住在别人日历里的拷贝。区别只在补救办法 ——
       .ics 重下一次就地更新，而这里再点一次只会多出第二条，所以对深链来说
       那句话里的活动页地址是**唯一**的出路。这个区别写在这里，不写进那句话：
       一句要分两种情况解释的免责声明，两种情况都说不清。

    ⚠️ 两家的这个字段都是**渲染 HTML** 的（Outlook 的文档明写，Google 的文档
       说 `details` 收基本标记并提醒要编码）。所以这里走 `html.escape`、换行变
       `<br>`：不转义的话，一句「Tea & biscuits」里的 `&` 会被当成实体的开头，
       而一段带 `<` 的说明会把后面的字整段吞掉。
       代价如实说：万一哪家其实是纯文本字段，人看到的是字面的 `&amp;` ——
       丑，但读得懂；反过来那一半是内容丢失。宁可丑。
    """
    return html.escape(_described(one), quote=False).replace("\n", "<br>")


def _query(params):
    """拼 query string，空值不写。

    ⚠️ `quote_via=quote` 而不是默认的 `quote_plus`：后者把空格写成 `+`，
       而 `+` 只在 form 编码里读作空格。Outlook 的 `startdt` 之类的值里没有
       空格无所谓，标题里有 —— 一个读成加号的标题是「Bible+Study」。

    ⚠️ `safe="/"` 保住 Google 那个 `dates` 里分隔起止的斜杠。把它编成 `%2F`
       之后 Google 读不出结束时刻，默默按默认时长建一场一小时的活动。
    """
    return urlencode({key: value for key, value in params.items() if value},
                     quote_via=quote, safe="/")
