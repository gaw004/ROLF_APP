# C3.6 —— 每天一次 pg_dump 推到 R2。render.yaml 的 rolf-backup 用这个镜像。
#
# ⚠️ **为什么这一个 cron 是 docker 而另一个是 python runtime**：pg_dump 的客户端
#    版本比服务端旧会**直接拒绝导出**，而库是 18。平台的 python runtime 里带哪个
#    版本的 postgresql-client 不由我们决定，也会随平台升级变 —— 而它变的那天，
#    表现是备份 cron 开始红，或者更糟：继续绿，但导出的东西恢复不了。
#    这一行 FROM 是唯一能把「客户端就是 18」写进 git 的写法。
#    core.tests.RenderBlueprintGuardTests 盯着这个数字和 CI 里那个一致。
FROM postgres:18-alpine

# ⚠️ **这里装出来的是 aws-cli v2（实测 2.34.63）**，也就是 botocore ≥ 1.36 ——
#    它默认给每个上传加一个流式 CRC32 trailer，而 S3 兼容存储历史上会用一句
#    看不懂的 XAmzContentSHA256Mismatch 拒绝它。backup.sh 里那两个
#    AWS_*_CHECKSUM_* 环境变量就是为这一条存在的，和 config/settings/prod.py
#    里 `request_checksum_calculation="when_required"` 是同一个绕法、同一个理由。
#    在这里留一句，是因为那两行环境变量单独看像是可以删掉的噪音。
RUN apk add --no-cache aws-cli

COPY backup.sh /usr/local/bin/backup.sh
# ⚠️ 不指望 COPY 把执行位带进来。git 记的是 100755，但一次 `chmod -x` 的提交、
#    或者一个不保留模式位的检出，表现都是部署当天 "permission denied"，
#    而那时候看到的错和真正的原因（少了一个模式位）长得毫无关系。
RUN chmod +x /usr/local/bin/backup.sh

# ⚠️ 不写 ENTRYPOINT：postgres 镜像自带的 entrypoint 对「第一个参数不是 postgres」
#    的情况就是一句 exec "$@"，所以 CMD 会原样跑起来（实测过）。
#    也不在 render.yaml 里写 startCommand —— 备份的命令行和它要跑的脚本放在一起。
CMD ["backup.sh"]
