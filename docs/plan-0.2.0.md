# 0.2.0 执行计划（决定已拍板）

> 基线：`main` @ `060301a`（v0.1.1 + 15 个未发布提交）。254 tests / 91% coverage / `ruff` + `format` 干净。
> 上游分析见 [OPTIMIZATION-PLAN.md](../OPTIMIZATION-PLAN.md)（历史稿，基线停在 e891841，只用于溯源）。
> 本文件是**可执行清单**，不是分析。仍然不写代码；每个 PR 写清文件、测试、验收、回滚。

---

## 0. 三条决定及其连锁

| # | 决定 | 直接后果 | 被否决的替代 |
| --- | --- | --- | --- |
| D1 | **第一用户 = 研究者，KPI = 可复现** | manifest/verify 的语义优先于 interop；`--urls`、`--glob` 移到 3 个月；`verify --against-remote` 提前到 0.2.0 之后第一件事 | 若选流水线作者，R12 提前、`--json` 字段优先于 manifest 字段 |
| D2 | **manifest 契约按本文件第 1 节执行** | schema 升 3；`not-selected`/`planned` 为一等状态；`--against-remote` 默认关闭 | 只写本次 results（会让 manifest 不再描述 record，被否） |
| D3 | **删除 Figshare** | 新增墓碑适配器 + `--checksum` 才能平移校验能力；`File.fallbacks` 随之为死代码 | 保留为可选 extra（维护 WAF 三角，被否） |

版本号：**0.2.0**。依据 [docs/releasing.md](releasing.md)：删除一个源属于 minor，即使 pre-1.0 也不能算 patch。

**D3 的隐藏耦合（必须先处理）**：`10.6084/...` 是 DOI。删掉适配器后，`registry.resolve` 会走 `doi` → doi.org 返回 `figshare.com/articles/...` 落地页 → 落到 `direct` → **把 HTML 当数据下载**（R01 的同一个 bug）。所以墓碑必须在 `doi.match` **之前**拦截，否则删源等于把 figshare 用户送进 R01。

---

## 1. manifest 契约（D2 的规范条文）

> 这 10 条是验收标准，不是建议。任何 PR 触碰 manifest 都必须在描述里逐条勾选受影响项。

**M-1** manifest 永远描述 **record**，不描述单次运行。未被本次选中的文件照常出行：
`selected: false`、`state: "not-selected"`、`verified_reason: "not-selected"`、`sha256: null`、`bytes: 0`。
**M-2** `verify` 把 `not-selected` 与 `planned` 归为 `skipped`：打印 ` skipped <name>`，不计入 `bad`，计入 `summary.skipped`；只有 skipped 行时 exit 0。
**M-3** `--require-all` 把 skipped 提升为 `bad`（严格模式）。这是给“我要一份完整快照”的人用的，不是默认。
**M-4** `acadfetch manifest`（只取元数据）每行写 `state: "planned"`，不再是 `missing`。
**M-5** 每行新增 append-only 字段：`final_url`、`etag`、`retrieved_at`（RFC 3339 UTC）、`http_status`（无传输时为 `null`）。
**M-6** 顶层新增：`schema: 3`、`selected_only: bool`、`remote_checked_at`。`READABLE = (1, 2, 3)`。
**M-7** `REPORT_SCHEMA` 1 → 2；`docs/json-report.md` 声明 `state` 可增长，消费者必须把未知值当非终态。
**M-8** `verify --against-remote` 默认 **关闭**；打开后按 manifest 的 source/target 重解析并对比公布 checksum。行级结论 `remote_match` / `remote_mismatch` / `remote_unavailable` / `remote_no_checksum`：
- `remote_mismatch` **即使本地 hash 相同也 exit 1**（这正是防篡改的意义）；
- `remote_unavailable` 是警告，不是 bad；加 `--require-remote` 才升级为 bad；
- 网络不可用时必须说“未复核”，不允许给出假绿。
**M-9** `--only` 不与旧 manifest 合并。规则：manifest = 该 record 的当前选择。需要完整清单就不带 `--only`，或事后 `--require-all` 看缺口。
**M-10** `manifest.target`、`files[].url`、`final_url`、stdout、默认输出目录默认脱敏 presigned query（`X-Amz-Signature` 等），并写 `url_redacted: true`；`--record-full-url` 保留原始 query。

**兼容性**：旧客户端（≤0.1.1）读 v3 manifest 会命中已有的 `unsupported manifest schema` 文案，可接受，但要在 CHANGELOG 写清。`verify` 本身不需要任何适配器，所以**已下载的 figshare 数据仍然可以 verify**——这一点必须写进迁移说明，否则删源会吓到存量用户。

---

## 2. Figshare 删除规格（D3）

### 2.1 用户看到的迁移路径

```
$ acadfetch fetch 10.6084/m9.figshare.32519553
acadfetch: figshare support was removed in 0.2.0.
  Open https://figshare.com/articles/32519553 in a browser, copy the
  download link (…/files/<file_id>/<name>), then:
      acadfetch fetch https://ndownloader.figshare.com/files/<file_id>/<name> \
          --checksum md5:<computed_md5>
  An existing figshare manifest still verifies: `acadfetch verify` needs no adapter.
```

三条路径（`figshare:<id>`、`10.6084/…`、`https://figshare.com/articles/…`）都走这段文案，退出码 1，**不发任何网络请求**。

### 2.2 图一：删什么、留什么

| 处理 | 位置 | 理由 |
| --- | --- | --- |
| 删 | `acadfetch/sources/figshare.py`（86 行） | 适配器本体 |
| 删 | `registry.py:9,14,21`、`doi.py:20-24`（`AGENCY_PREFIXES` 本就是死代码） | 注册表与无用常量 |
| 删 | `cli.py:31,39,46,452` 的 figshare 字样 | 示例/帮助 |
| 删 | `auth.py:18` 的 `figshare` 令牌 | `client.source` 不会再等于 `figshare` |
| 删 | `sources/base.py:16` `fallbacks` + `downloader.py:436,469-472` 的候选循环 | figshare 是唯一生产者；保留是死代码 |
| 留 | `client.is_block_page` / `block_retries` / `transient` / `--retry-403` | 任意 CDN 的 HTML 403 仍需要；改写注释即可 |
| 留 | `tests/fixtures/figshare_403_block.html`、`figshare_403_forbidden.json` | 现在是通用 block-page 夹具，改注释 |
| 留 | `tests/fixtures/figshare_file.pdf` | 唯一的“真字节 + 公布 md5”夹具，`test_downloader.py:13-14` 依赖 |
| 删 | `figshare_article.json` 的解析用途 | 仅当直链测试要复用时保留 |
| 留 | `sources` 输出末尾一行 `figshare  removed in 0.2.0 - pass the ndownloader URL` | 用户仍会先敲 `sources`；这一行是最后一道指路牌 |
| 新增 | `acadfetch/sources/removed.py` | 墓碑：`explain(target) -> str | None`，在 `doi.match` 之前调用 |
| 新增 | `--checksum <algo>:<hex>` | 补回被删掉的“按 API 公布 md5 校验”能力；写入 manifest 并标 `checksum_source: "cli"`；只收 `md5:`/`sha256:`，格式错在发请求前拒 |

### 2.3 顺序要求

墓碑与 `--checksum` **必须和删除同一个 PR**，否则中间态会让 figshare 用户直接落到 HTML。

---

## 3. PR 清单（2 周，约 9 人日）

图例：**S**=半天，**M**=1 天，**L**=1.5 天。每个 PR 一个关注点，符合 `CONTRIBUTING.md:46-48`。

### 第 1 周 —— 契约与身份

#### PR-1 `manifest: schema 3`（M）
- **文件**：`acadfetch/manifest.py`、`downloader.py`（Result 传递）、`cli.py`（`--require-all`、summary）、`docs/manifest.md`（新）
- **测试**：v1/v2 仍可读；`not-selected` 让 verify exit 0；`--require-all` 让它 exit 1；`planned` 同理（空清单的 `ok_run` 归 PR-8）
- **验收**：`fetch --only a.bin` 成功后 `verify` exit 0 且输出 `1 ok, 1 skipped`；`--require-all` exit 1
- **回滚**：`READABLE` 保持含 1、2 即可降级读取；写入端回滚只需改 `SCHEMA`
- **依赖**：无。**先做**，因为后面所有 PR 都写这些字段

#### PR-2 `manifest: provenance 字段与 URL 脱敏`（M）
- **文件**：`manifest.py`、`downloader.py`（`Result.final_url/etag/http_status/retrieved_at`）、`cli.py`（`--record-full-url`）
- **测试**：下载成功行四字段齐全；`-n` 时全为 `null`；presigned URL 在 manifest/stdout/目录名里 0 处 query；`--record-full-url` 保留
- **验收**：`X-Amz-Signature=SECRET` 三个出口都不出现
- **回滚**：字段可为 `null`，读者不受影响
- **依赖**：PR-1

#### PR-3 `feat!: 删除 figshare，加墓碑与 --checksum`（M）
- **文件**：见 2.2；`docs/migration-0.2.md`（新）、README/troubleshooting/CHANGELOG
- **测试**：三条目标形式各 1 条墓碑用例且 `client = None` 也不炸（证明零请求）；`--checksum md5:<正确>` 通过、错误则 MISMATCH；`--checksum md5:zz` 在发请求前被拒；**旧 figshare schema-2 manifest 仍 verify 通过**
- **验收**：`acadfetch fetch figshare:1` 打印迁移命令、exit 1；`sources` 末尾有 removed 行（格式见 2.2）
- **回滚**：恢复 `figshare.py` + 注册表三行 + `auth.py` 一行
- **依赖**：无（但建议在 PR-1 之后，避免 manifest 冲突）

#### PR-4 `fix: 目标身份——不把落地页当数据`（L）
- **文件**：`doi.py`、`registry.py`、`direct.py`、`cli.py:406-409`、`downloader.py:375-386`
- **内容**：
  1. DOI 解析出的 URL 若没有任何仓库适配器认领，`HEAD` 一次：`text/html` → 拒绝并打印落地 URL 与“把文件 URL 给我”
  2. 默认输出目录改为 `<source>-<host>-<id-or-8位哈希>`；同一 basename 不同主机会分居
  3. `fetch_file` 的“已存在即跳过”只在 checksum 命中，或（无 checksum 时）`size` 命中 **且** 三选一成立：`-o` 显式给出 / 目录名含目标哈希 / 旧 manifest 同 URL
- **测试**：真实 Mendeley DOI `10.17632/z8g47vzt4p.1` 拒绝 HTML；两个不同主机同 `data.csv` 同大小 → 两目录两份字节；`-o` 显式时沿用旧跳过行为
- **验收**：上两条本地可复现；`test_downloader.py:375-386` 相关用例仍绿
- **回滚**：目录规则变更只影响新运行；`--force` 可绕过跳过
- **依赖**：PR-3（墓碑先吸收 figshare DOI）

#### PR-5 `fix: 直链校验头`（S）
- **文件**：`client.py:258-264`、`client.head`、`direct.py:37-38`
- **测试**：小写 `content-md5`/`x-amz-meta-sha256` 可解析；`Content-MD5` base64→hex；错误 base64 只降级为 `unparsable`
- **验收**：三个小写对象各自校验为 `true`
- **回滚**：纯函数，还原即可
- **依赖**：无。**与 PR-3 的 `--checksum` 组合**才让迁移用户有校验

#### PR-6 `fix(arxiv): 许可与 legacy ID`（S）
- **文件**：`sources/arxiv.py:58-67`
- **测试**：`solv-int/9701001v2` → `solv-int/9701001.pdf`、DOI `10.48550/arXiv.solv-int/9701001`、version `2`；无许可元数据 → `unknown` 而不是硬编码
- **验收**：真实 `solv-int/9701001v2` 的 DOI 可解析；同一 `sol.pdf` 不再吞第二篇
- **回滚**：单文件
- **依赖**：无

### 第 2 周 —— 失败隔离与首屏

#### PR-7 `fix: 一个坏文件只坏一个文件`（M）
- **文件**：`downloader.py:558-570,578-579`、`client.py:226-232`、`cli.py:539-547`
- **测试**：目录占名 + checksum → 该文件 failed、其余成功、exit 2、manifest 行数不变；`get_json` 遇 `IncompleteRead` → 一行错误而非 traceback；`-o` 是文件 → `-o must be a directory`
- **验收**：10 文件批次 9 成 1 败；`--json` 下 6 种早失败各恰好 1 份文档、0 traceback
- **回滚**：`--verbose` 保留原 traceback
- **依赖**：PR-1

#### PR-8 `fix(cli): 失败契约`（M）
- **内容**：argparse 用法错误 exit 64（`2` 只留给 partial）；`--only` 无匹配列候选名；`verify` rows 带 `sha256`；空清单 `ok_run: false`；`-n` 磁盘不足只标记 `disk.ok=false` 不失败
- **测试**：`--rate 0` exit 64；`--only nope` exit 1 且含候选名；`verify --json` rows 含 `sha256`
- **验收**：README 的退出码表与实际一致
- **依赖**：PR-7

#### PR-9 `fix: 预占控制文件名`（S）
- **内容**：`manifest.json`、`manifest.json.tmp`、所有 `*.part`/`*.part.meta.json` 进 `taken`；或 sidecar 收进 `.acadfetch/`
- **测试**：仓库含 `manifest.json`/`x.part` 时产物完整、无混合字节
- **验收**：两条复现脚本不再破坏
- **依赖**：PR-4

#### PR-10 `docs: 首屏不再骗人`（S）
- **内容**：README 三条真实记录 + 一条 URL 形态示例；`sources` 同步；`fetch` 无参打印 3 条命令；`fetch` 加 epilog；平台矩阵改实际值；dev install 加 `pytest`；“before the first request”“renamed after checksum”“every 5 s”改准
- **验收**：首屏 5 条命令全部通过；5 人走查 0 卡点
- **依赖**：PR-3/PR-4 的最终文案

#### PR-11 `fix(client): scheme 降级与 Cookie`（S）
- **内容**：https→http 同主机也剥 `Authorization`（或 `--allow-http` 显式放行）；跨主机剥 `Cookie`/`Proxy-Authorization`；`downgrades` 进 stderr
- **测试**：同主机降级后 header 为空；跨主机 Cookie 消失；stderr 有一行警告
- **依赖**：PR-4

#### PR-12 `release: 0.2.0`（M）
- CHANGELOG 加 `### Removed`（含迁移命令）与 `### Changed`（schema 3、exit 64、`not-selected`）；两处版本号；tag on main；real smoke
- **验收**：`smoke` 真实下载 1 个记录并 `verify`；`pip install acadfetch==0.2.0` 后 `--version` 正常

### 依赖图（只列硬依赖）

```
PR-1 ─┬─ PR-2
      ├─ PR-7 ── PR-8
      └─ PR-12
PR-3 ── PR-4 ─┬─ PR-9
              ├─ PR-10
              └─ PR-11
PR-5 ── PR-3（校验能力）
PR-6（独立）
```

合并顺序：`1 → 2 → 3 → 5 → 4 → 9 → 6 → 7 → 8 → 11 → 10 → 12`。PR-6 可随时插队。

---

## 4. 怎么证明 0.2.0 真的是对的

不靠“看起来好了”，四层证据：

1. **契约测试**（离线、进 CI）：每条 M-x 一条反例测试；v1/v2 manifest 回归测试。
2. **身份夹具**：Mendeley DOI + 双直链 + `manifest.json`/`x.part` 撞名，三个脚本进 `tests/`。
3. **首跑剖面**：`scripts/first_success.py` 跑 E1 三次 + E2 三次，修复前后各一份 CSV。
4. **人工走查**：5 个陌生用户按 README 前 5 行操作，记录卡点分钟数；0.1.1 的基线是“第 4 条命令必失败”。

发布门（手写在 PR-12 描述里）：254 + 新增 tests 全绿、coverage ≥ 91%、`ruff`/`format` 干净、`-W error` 干净、`pyproject` 与 `consts` 版本一致、旧 v1/v2 manifest 可读。

---

## 5. 风险登记

| 风险 | 概率 | 影响 | 缓解 | 触发后 |
| --- | --- | --- | --- | --- |
| 删 figshare 后存量用户不知道怎么办 | 高 | 中 | 墓碑文案 + `--checksum` + 迁移文档 + “旧 manifest 仍可 verify” | 0.2.1 补 `--from-figshare-id` |
| 目录规则变更打乱既有脚本 | 中 | 中 | 只影响新运行；CHANGELOG 写清 | 加 `--flat-out` 恢复旧规则 |
| `not-selected` 破坏第三方解析器 | 中 | 中 | `state` 只增不改；`selected` 冗余布尔 | 同时保留 `state:"missing"` + `selected:false`（二选一可切） |
| 逐 future 护栏掩盖真 bug | 中 | 低 | 测试里 re-raise；`--verbose` 打 traceback | 回滚该 PR |
| scheme 降级阻断合法镜像 | 低 | 中 | `--allow-http` | 放宽为只警告 |
| M-8 远程校验被当成新负担 | 中 | 低 | 默认关闭 | 保持 opt-in，不做默认 |

---

## 6. 0.2.0 之后（压缩）

**已完成**：R10 remote verify（0.3.0）、R13 nightly+E2（CI）、R14 JSON 契约（docs/json-report.md）、R15 发布卫生（tag-only、SHA pin）、R16 网络控制（0.4.0）。

**6 周头部（D1 决定）**：R10 `verify --against-remote`（5 天，第一件事）→ R13 nightly smoke + 自动开 issue → R14 schema 文档补齐 → R15 发布卫生（dispatch 强制 tag、pin publish/attest、real smoke、CHANGELOG 结构、内部稿归档）→ R16 `--timeout/--proxy/--ca-bundle`。
**6 周尾部**：R17 verbose、R09 字段补全（若 PR-2 未覆盖）。
**3 个月**：R18 流式哈希/有界队列、R19 命名竞态（issue #5）、R20 类型门或撤 `Typed`、R12 `--urls/--glob`、W4 CITATION/软件 DOI/demo/Snakemake。

---

## 7. 明确不做

- 不加新源；不重写 async/Rust；不拆 README；不引 CoC/CodeQL/feature 模板；不为 Windows ARM 加 CI。
- 不做 manifest 合并（M-9）；不把 `--against-remote` 设默认；不给 figshare 做兼容垫片。
- 不在 0.2.0 碰 R18/R19/R20：它们不改变“拿到的是不是对的东西”。

---

## 8. 已拍板的补充（无待决项）

1. **`--checksum` 语法**：`md5:<hex>` / `sha256:<hex>`，复用 `verify.parse`，错格式在发请求前直接拒；取值写入 manifest，`checksum_source: "cli"`。
2. **`sources` 展示**：保留一行 `figshare  removed in 0.2.0 - pass the ndownloader URL`，排在 active 源之后。
3. 迁移文档 `docs/migration-0.2.md` 必须包含：三条目标形式各自的转换命令、`--checksum` 用法、以及“旧 figshare manifest 仍可 verify”。
