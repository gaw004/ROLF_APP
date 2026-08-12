#!/usr/bin/env bash
#
# C3.6 —— 一次全库 pg_dump，推到 R2 的备份桶。每天由 render.yaml 的
# rolf-backup cron 跑一次。
#
# ⚠️ **它不是 Django management command，这是一条落点规矩不是风格偏好。**
#    判据在 phase-c.md「备份脚本的落点」：备份必须在**应用起不来的时候**照样能跑。
#    写成 management command 就意味着它依赖 Django 能启动、依赖 settings 能加载
#    —— 而需要备份的那天，坏掉的往往正是这些。这个脚本只认两样东西：
#    一个 DATABASE_URL，和一个 pg_dump。
#
# ⚠️ **这个 dump 里是未成年人的姓名、生日、住址、紧急联系电话、家长邮箱的全量明文。**
#    所以桶必须是私有的，密钥是专门的一把（BACKUP_ 前缀，和应用那三个桶不共用）。
#    一个默认公开的桶就是一次全库泄露，而它比删库更难发现：什么都不会坏，
#    什么都不会报错，你只是不知道有人下载过。
#
# ⚠️ **不许 echo 任何一个环境变量，也不要开 `set -x`。** DATABASE_URL 里带密码，
#    而 cron 的日志是平台上任何能登录的人都看得到的。下面所有出错信息都只说
#    变量的**名字**。
set -o errexit
set -o nounset
set -o pipefail

# --- 必须有的五个 ----------------------------------------------------------
# ⚠️ 全部在最前面检查，为的是让「少配了一个变量」这件事在**第一秒**失败并且指名
#    道姓，而不是跑完 pg_dump（可能几十秒）之后才在上传那一步倒下 ——
#    那种失败留下的是一个已经产生、但没有任何地方收着的 dump 文件。
: "${DATABASE_URL:?missing: DATABASE_URL}"
: "${BACKUP_R2_ENDPOINT_URL:?missing: BACKUP_R2_ENDPOINT_URL}"
: "${BACKUP_R2_ACCESS_KEY_ID:?missing: BACKUP_R2_ACCESS_KEY_ID}"
: "${BACKUP_R2_SECRET_ACCESS_KEY:?missing: BACKUP_R2_SECRET_ACCESS_KEY}"
: "${BACKUP_R2_BUCKET:?missing: BACKUP_R2_BUCKET}"

# --- 两个可调的，都有默认值 -------------------------------------------------
# 桶里的前缀。留一个是为了让 R2 的生命周期规则**指得准**（「db/ 下超过 30 天的
# 删掉」），也为了以后这个桶里放别的东西时不会被那条规则误伤。
BACKUP_PREFIX="${BACKUP_PREFIX:-db}"

# ⭐ 一个 dump 里至少要有这么多张表的数据段，少于它就判定这份备份不算数。
#    ⚠️ 这一条防的是本项目验收口径里点名的那种失败：**「文件在、看着有几十兆」
#    而它其实是一个空库的 dump**。连错库、连到一个刚建好还没 migrate 的库、
#    或者迁移只跑了一半，产出的都是一个**完全合法、可以上传成功**的档案文件。
#    没有这个下限，那种备份会安静地积累三十天，直到你真的需要它。
#    本项目现在 37 张表；20 这个下限留了余量，同时远高于「空库」。
#
#    ⚠️ **它查的是「schema 在不在」，不是「有没有数据」，两者要分清楚**（实测于
#    2026-08-12）：一个刚 migrate 完、一行业务数据都没有的库，dump 出来同样是
#    37 个 TABLE DATA 段 —— 所以这条检查**不会**在上线第一天误伤，
#    但它也**拦不住**「连到了一个 schema 齐全却空着的库」。那一种只有
#    恢复演练查得出来，而演练是人做的事，不是脚本能替的。
BACKUP_MIN_TABLES="${BACKUP_MIN_TABLES:-20}"

# --- 1. 导出 ----------------------------------------------------------------
# ⚠️ **先落地成文件，不 `pg_dump | aws s3 cp -`。** 流式上传省一个临时文件，
#    但它同时让「pg_dump 中途失败」变成**一个已经躺在桶里的、被截断的档案** ——
#    errexit 会让这次 cron 变红，可那个坏文件还在，而且它是桶里最新的一个。
#    恢复演练那天拿到的就是它。落成文件才有下面第 2 步的验证可做。
#    core.tests 有一条守卫盯着这个管道不许出现。
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="rolf-${stamp}.dump"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
dump="${workdir}/${name}"

echo "[backup] pg_dump -> ${name}"
# --format=custom：恢复时能挑表、能并行，自带压缩（2026-08-12 定）。
# --no-owner / --no-privileges：恢复演练是灌进一个**本机的空库**，那里没有
#   生产库的角色名。带着 owner 恢复会在每一句 ALTER ... OWNER TO 上报错 ——
#   一堆吓人的红字，而数据其实是好的。把这个噪音在导出这一侧就去掉。
# ⚠️ pg_dump 只在**客户端比服务端旧**的时候拒绝导出，反过来（客户端更新）是支持的。
#    所以这里不再自己比一次版本号：真出事时 pg_dump 自己的报错
#    （"server version: X; pg_dump version: Y"）比任何自制检查都清楚。
pg_dump \
  --format=custom \
  --no-owner \
  --no-privileges \
  --file="${dump}" \
  "${DATABASE_URL}"

# --- 2. 验证这份 dump 本身 --------------------------------------------------
# ⚠️ 这一步回答的是验收口径里那句话：**「文件存在不等于能恢复」**。
#    `pg_restore --list` 要把归档的目录读出来，所以它同时证明了两件事：
#    文件没被截断、格式是能被 pg_restore 认的。
listing="${workdir}/listing.txt"
pg_restore --list "${dump}" > "${listing}"

# ⚠️ 数的是 "TABLE DATA" 而不是 "TABLE"：后者是 substring，会把 TABLE DATA
#    也算进去，于是每张表被数两次 —— 下限就等于悄悄减半了。
tables="$(grep -c ' TABLE DATA ' "${listing}" || true)"
if [ "${tables:-0}" -lt "${BACKUP_MIN_TABLES}" ]; then
  echo "[backup] REFUSING: the archive holds ${tables} table(s) of data," \
       "fewer than the ${BACKUP_MIN_TABLES} expected." \
       "An empty or half-migrated database produces a perfectly valid dump;" \
       "this is the check that keeps it out of the bucket." >&2
  exit 1
fi

size="$(wc -c < "${dump}" | tr -d ' ')"
echo "[backup] archive ok: ${tables} tables, ${size} bytes"

# --- 3. 上传 ----------------------------------------------------------------
# 凭据走环境变量，不进命令行 —— 命令行在 `ps` 里是所有人可见的。
export AWS_ACCESS_KEY_ID="${BACKUP_R2_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="${BACKUP_R2_SECRET_ACCESS_KEY}"
# R2 没有区域，但 botocore 不给区域就拒绝签名 —— 而它报的是一个签名错误，
# 不是「缺 region」。同 prod.py 里那句 region_name="auto"。
export AWS_DEFAULT_REGION="auto"
# ⚠️ 这两行不是噪音，删掉会在某天变成一句看不懂的 XAmzContentSHA256Mismatch。
#    aws-cli v2（botocore ≥ 1.36）默认给上传加一个流式 CRC32 trailer，而 S3
#    兼容存储对它的支持参差不齐。和 prod.py 里
#    `request_checksum_calculation="when_required"` 是同一个绕法、同一个理由。
export AWS_REQUEST_CHECKSUM_CALCULATION="when_required"
export AWS_RESPONSE_CHECKSUM_VALIDATION="when_required"

key="${BACKUP_PREFIX}/${name}"
echo "[backup] uploading -> s3://${BACKUP_R2_BUCKET}/${key}"
aws s3 cp "${dump}" "s3://${BACKUP_R2_BUCKET}/${key}" \
  --endpoint-url "${BACKUP_R2_ENDPOINT_URL}" \
  --only-show-errors

# --- 4. 回头确认它真的在桶里，而且是整份 -------------------------------------
# ⚠️ 为什么上传成功了还要再问一次：`aws s3 cp` 退出码为 0 只说明它认为传完了。
#    这一步比对的是**桶里那个对象的字节数**和本地的一致 —— 它是这个脚本里唯一
#    一句「从对方的视角看」的检查，而备份这件事全部的价值就在对方那一侧。
remote_size="$(aws s3api head-object \
  --bucket "${BACKUP_R2_BUCKET}" \
  --key "${key}" \
  --endpoint-url "${BACKUP_R2_ENDPOINT_URL}" \
  --query 'ContentLength' \
  --output text)"

if [ "${remote_size}" != "${size}" ]; then
  echo "[backup] FAILED: uploaded object is ${remote_size} bytes," \
       "the local archive is ${size}." >&2
  exit 1
fi

echo "[backup] done: s3://${BACKUP_R2_BUCKET}/${key} (${size} bytes)"

# ⚠️ **这个脚本永远不删任何东西。** 旧备份的清理交给 R2 的生命周期规则
#    （db/ 前缀，30 天，2026-08-12 定）。理由：一个「能删备份的代码路径」和
#    「能传备份的代码路径」放在同一个文件里，只要有一天前者的条件写错，
#    它删的正是后者辛苦传上去的东西。规则写在桶上，脚本连删除权限都不需要。
