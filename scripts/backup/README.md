# 备份与恢复（C3.6）

每天一次全库 `pg_dump`，推到 Cloudflare R2 的备份桶。
Render 的 `rolf-backup` cron 跑 `Dockerfile` 构建出来的镜像，镜像跑 `backup.sh`。

**为什么它不是 Django management command**：判据在
[`phase-c.md` 的备份落点](../../docs/planning/phase-c.md#备份脚本的落点) ——
备份必须在**应用起不来的时候**照样能跑。这个脚本只认一个 `DATABASE_URL` 和一个
`pg_dump`。（同一份 `render.yaml` 里另一个 cron `purge_event_images` **是**
management command，两者不矛盾：那个要问 ORM「哪些活动结束了」。）

## 环境变量

| | |
|---|---|
| `DATABASE_URL` | Render 注入 |
| `BACKUP_R2_ENDPOINT_URL` | `https://<account id>.r2.cloudflarestorage.com` |
| `BACKUP_R2_ACCESS_KEY_ID` / `BACKUP_R2_SECRET_ACCESS_KEY` | ⚠️ **专用的一把**，只给备份桶，不和应用那三个桶共用 |
| `BACKUP_R2_BUCKET` | 备份桶名 |
| `BACKUP_PREFIX` | 选填，默认 `db` |
| `BACKUP_MIN_TABLES` | 选填，默认 `20` —— 见下面「它会拒绝什么」 |

## 桶要怎么配

- **私有**。⚠️ dump 里是未成年人的姓名、生日、住址、紧急联系电话、家长邮箱的
  **全量明文**。一个默认公开的桶就是一次全库泄露，而它比删库更难发现：
  什么都不会坏，什么都不会报错，你只是不知道有人下载过；
- **一条生命周期规则：`db/` 前缀下超过 30 天的对象删掉**（2026-08-12 定）。
  ⚠️ **清理是桶的事，不是脚本的事** —— 一个「能删备份的代码路径」和「能传备份的
  代码路径」放在同一个文件里，只要哪天前者的条件写错，它删的正是后者传上去的东西。
  脚本连删除权限都不需要。
- ⚠️ 这条规则**只对备份桶**。memories 桶上不许有任何 lifecycle 规则，
  理由见 [D29](../../docs/planning/decisions/D29-memories-wall.md#三桶为什么-memories-不和活动图片共用一个)。

## 它会拒绝什么（这是这个脚本最值钱的部分）

上传之前先验一遍自己刚导出来的东西：

1. `pg_restore --list` 必须读得通 —— 证明文件没被截断、格式认得出；
2. 归档里的表数据段必须 ≥ `BACKUP_MIN_TABLES`（默认 20，本项目现在 37 张表）。

⚠️ 第 2 条防的是验收口径里点名的那种失败：**「文件在、看着有几十兆」，
而它其实是一个空库的 dump**。连错库、连到一个刚建好还没 migrate 的库、
或者迁移只跑了一半 —— 产出的都是一个**完全合法、能上传成功**的档案。
没有这个下限，那种备份会安静地积累三十天，直到你真的需要它。

传完之后还会回头 `head-object` 比一次字节数：`aws s3 cp` 退出码为 0 只说明
它认为传完了，而备份全部的价值在对方那一侧。

**任何一步失败都是退出码 1**，Render 会把这次 cron 标红。
⚠️ **去 Render 上把 cron 的失败通知打开** —— 否则「标红」只是控制台上的一个颜色。

## ⭐ 恢复演练：三样都做了才算

口径在 [`phase-c.md`](../../docs/planning/phase-c.md#备份什么叫演练过)。
⚠️ **只做第 1 步是最危险的状态**：文件在、几十兆，而它可能是空库的 dump、
或者是用不同 Postgres 大版本导出的。文件存在不等于能恢复，
**而这件事只有真的恢复一次才知道**。

```bash
# 1. 从桶里取回最新那份（不是本地那份 —— 本地那份证明不了桶里的能用）
export AWS_ACCESS_KEY_ID=…            # 备份桶那把钥匙
export AWS_SECRET_ACCESS_KEY=…
export AWS_DEFAULT_REGION=auto
ENDPOINT=https://<account id>.r2.cloudflarestorage.com
LATEST=$(aws s3 ls s3://<bucket>/db/ --endpoint-url $ENDPOINT | sort | tail -1 | awk '{print $4}')
aws s3 cp "s3://<bucket>/db/$LATEST" ./drill.dump --endpoint-url $ENDPOINT

# 2. 灌进一个空库
dropdb --if-exists rolf_restore_drill && createdb rolf_restore_drill
pg_restore --no-owner --no-privileges -d rolf_restore_drill ./drill.dump

# 3. schema 和代码同代吗
DATABASE_URL=postgres://$(whoami)@localhost:5432/rolf_restore_drill \
  python manage.py migrate --check

# 4. 对着恢复出来的库跑一遍测试
DATABASE_URL=postgres://$(whoami)@localhost:5432/rolf_restore_drill \
  python manage.py test
```

**2026-08-12 的演练结果**（拿 MinIO 当 R2、一个临时 Postgres 18 当生产库，
在本机完整走了一遍）：取回 259 KB → 恢复出 37 张表、`pg_restore` **0 个错误** →
`migrate --check` 通过 → **925 条测试全绿**。
⚠️ 这一遍证明的是**脚本和流程**对；**上线之后必须拿真的 R2 和真的生产库再走一遍**，
那一遍才是验收清单上的那一条。

## 本机怎么再演一遍（不碰生产）

```bash
docker network create rolfbk
docker run -d --name pgtest --network rolfbk -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=rolf_test postgres:18-alpine
docker run -d --name minio  --network rolfbk -e MINIO_ROOT_USER=testkey -e MINIO_ROOT_PASSWORD=testsecret123 minio/minio server /data
pg_dump --no-owner --no-privileges rolf_dev | docker exec -i pgtest psql -U postgres -d rolf_test -q

docker build -t rolf-backup:test scripts/backup
docker run --rm --network rolfbk -e AWS_ACCESS_KEY_ID=testkey -e AWS_SECRET_ACCESS_KEY=testsecret123 \
  -e AWS_DEFAULT_REGION=auto rolf-backup:test \
  aws s3 mb s3://rolf-backups-test --endpoint-url http://minio:9000

docker run --rm --network rolfbk \
  -e DATABASE_URL=postgres://postgres:testpw@pgtest:5432/rolf_test \
  -e BACKUP_R2_ENDPOINT_URL=http://minio:9000 \
  -e BACKUP_R2_ACCESS_KEY_ID=testkey -e BACKUP_R2_SECRET_ACCESS_KEY=testsecret123 \
  -e BACKUP_R2_BUCKET=rolf-backups-test rolf-backup:test

docker rm -f pgtest minio && docker network rm rolfbk
```

值得顺便验的四条（2026-08-12 都验过，退出码依次是 1 / 1 / 1 / 1）：
把 `DATABASE_URL` 指向一个空库、少给一个环境变量、给一把错的密钥、
把库的主机名写错 —— **四种都必须红，而且都不能在桶里留下东西**。

## 不在这个脚本范围里的

- **memories 桶里的照片不进备份**。它们既不能重建、又不在 `pg_dump` 里，
  而 R2 没有版本控制 —— 这是一条**主动接受**的已知缺口，记在
  [`phase-c.md`](../../docs/planning/phase-c.md#五已知缺口与处置)，不是这里的遗漏；
- **活动图片也不进备份**。它们按设计就该在活动结束后消失。
