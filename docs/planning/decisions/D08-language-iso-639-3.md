# D8 · Language 自建表（ISO 639-3），不用 `django-languages-plus`

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D8` 指的就是本文件。

`languages-plus` 的表键在 2 字母 ISO 639-1 码上，**排除了 Mandarin (cmn)、Cantonese (yue)、
Hmong 等**，而这些正是基金会最常服务的语言。所以自建 `Language` 表，
由数据迁移从 `pycountry` 灌入约 7900 行 ISO 639-3，并加 `pin_rank` 字段让常用语言排在下拉最前面。
**代价**：多一张自己维护的表 —— 但换来的是能正确记录服务对象的语言，这是刚需。
