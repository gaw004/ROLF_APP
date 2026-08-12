#!/usr/bin/env bash
#
# C3.5 —— Render 上的构建步骤。render.yaml 里只有 web 服务跑这个脚本；
# 两个 cron 各自只 pip install，理由写在那边。
#
# ⚠️ 三件事**不在这里**，每一件都是故意的：
#
#   · **npm / 构建前端。** 产物由 CI 构建好推到 deploy 分支（C1.2），而
#     render.yaml 盯的正是那个分支 —— 所以 static/css/app.css 和
#     static/js/app.js 在 checkout 出来的那一刻就已经在了。Render 的 python
#     runtime 不带 Node，把构建搬到 CI 就是为了让这一步不存在。
#
#   · **migrate。** 走 render.yaml 的 preDeployCommand（2026-08-10 改口，
#     原计划在这里，经过见 revisions.md 三十三）。短版理由：三个服务在同一次
#     push 上各自 build，写在这里就是三份并发跑同一套迁移。
#
#   · **compilemessages。** 随 D23 删掉了 —— 界面直接写英文，没有翻译文件。
#
set -o errexit   # 任何一步失败就停。少了它，一个装不上的依赖会一路走到
                 # collectstatic，然后报一个跟真正的原因毫无关系的错。
set -o nounset
set -o pipefail

pip install -r requirements.txt

# ⚠️ 这一步用的是 **prod** 的 settings（DJANGO_SETTINGS_MODULE 由 render.yaml
#    的环境变量组给），也就是 CompressedManifestStaticFilesStorage：它会解析
#    每个 CSS 里的 @import 和 url(),解析不了就 MissingFileError。CI 里那条
#    「collectstatic with the production storage」守的就是这一刻，为的是让它
#    在 PR 上失败而不是在部署当天。
#
# ⚠️ 也正因为走 prod.py，这一步 import settings 时就会 required=True 地读
#    DATABASE_URL 和 R2 那七项。它们**在构建期就必须已经设好** —— 缺一项的
#    表现不是「静态文件收不齐」，是构建直接以
#    "Missing required environment variable: R2_ENDPOINT_URL" 停住。
#
# ⚠️ --noinput 不是图省事：构建环境没有 tty，那句「要清空 staticfiles/，
#    确定吗」会一直等到超时。
python manage.py collectstatic --noinput
