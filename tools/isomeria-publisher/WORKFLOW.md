# Isomeria Blog Publisher 跨会话流程

本工具位于仓库 `tools/isomeria-publisher/`。新会话请明确指定阅读 `tools/isomeria-publisher/WORKFLOW.md`；原有根目录 `AGENTS.md` 保持不变。适用于 `KokoroLyase/flare-stack-blog` 仓库和 `Isomeria-Blog-Publisher` 云端环境，唯一目标博客为 `https://blog.isomeria.net`。

## 新会话入口与依赖

1. 在 Codex 中选择预期云端环境，检出包含本工具的分支/提交。仅保存在旧任务工作区的文件不会出现在新任务中；初次本地迁移须经用户批准后推送，才能由 GitHub 跨任务复用。
2. 阅读根目录 `AGENTS.md` 和本文件，在当前仓库根目录执行下方命令。`runtime.py` 根据自身位置寻找仓库，不依赖 `/workspace` 或特定检出目录。
3. 需要 Linux、Python 3.10+ 的标准库，以及仓库原有的 `.agents/skills/flare-stack-blog/scripts/fsb.py` 和同目录的 `tiptap.py`。没有新增第三方 Python 依赖。原项目 skill 和两个脚本均不修改。
4. `probe.py` 依赖同目录 `runtime.py`；`tests/test_runtime.py` 使用离线模拟和 `tests/sample.md` 样例，不需要真实凭据或网络。不要迁入旧工作区的 API 响应、凭据配置、检查报告或草稿正文。
5. 管理员密钥由运行平台向进程注入；它不属于 Git 仓库，也不会随检出分支自动注入。缺失时报告缺失并停止，由用户在环境管理界面处理；不要要求用户把密钥粘贴进聊天、源码或命令行。

会话启动只提供工具入口，不自动运行创建、更新、删除或发布操作。每次都根据用户当前任务决定操作范围；公开发布必须针对具体文章获得新的明确确认。

## 认证和执行权限

- 阅读原项目 `.agents/skills/flare-stack-blog/SKILL.md`，本流程的用户约束优先于其 TOML 凭据配置和错误响应原文输出说明。
- `runtime.py` 导入原 `fsb.py`，仅在该导入模块内替换 `read_config()` 和 transport 入口。请求构造、浏览器 User-Agent、JSON 编码/解析、Markdown 转换均复用原工具；原文件保持不变。
- `FSB_ADMIN_API_KEY` 必须在 Python 进程环境中存在且非空，否则立即停止，不读取 TOML，不回退其他来源。`check` 也不会输出密钥、长度或片段。
- 密钥仅进入固定 HTTPS origin `https://blog.isomeria.net` 的 `x-api-key` 请求头。精确检查主机和允许的 API 路径，禁止重定向，避免将认证头转发到其他域名。
- 保留平台代理和 CA 信任，正常验证 TLS；每次请求 socket 超时 10 秒，总时限 20 秒，无自动重试。同步调用需在 Linux Python 主线程中执行。
- 所有会发起 HTTP 请求的 `exec_command`，必须包含下面的单次网络权限参数。申请不支持、拒绝或需要用户决定时停止/等待，不另找连接方式。Python 文件本身不能代替工具申请权限。

```json
{
  "sandbox_permissions": "with_additional_permissions",
  "additional_permissions": {"network": {"enabled": true}}
}
```

从仓库根目录运行：

```bash
python3 -B tools/isomeria-publisher/runtime.py check
python3 -B tools/isomeria-publisher/probe.py
python3 -B tools/isomeria-publisher/runtime.py list
```

其中 `check` 不访问网络，只确认凭据存在且非空；成功打印 `credential_present_nonempty: true`，失败非零退出。后两条访问网络，须使用上述权限。已有真实文章 ID 后可执行 `python3 -B tools/isomeria-publisher/runtime.py get --id ID`，其中 ID 替换为正整数。CLI 仅打印 HTTP 状态、文章 ID 和结果，不打印正文。

网络权限是 `exec_command` 工具调用的字段，不是 shell 命令、环境变量或 Python 参数。以当前仓库根目录作为工作目录，调用示例为：

```json
{
  "cmd": "python3 -B tools/isomeria-publisher/probe.py",
  "sandbox_permissions": "with_additional_permissions",
  "additional_permissions": {"network": {"enabled": true}}
}
```

保留环境既有域名白名单，不修改网络策略。平台要求用户批准时等待决定；不支持或拒绝时停止，不使用其他连接方式绕过限制。

需要在内存中读取正文时，用下面的 Python 模块入口；从仓库根目录以 `python3 -B` 执行，不要打印完整 payload 或配置。代码块中的 GET 同样需要工具网络权限。

```python
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path("tools/isomeria-publisher").resolve()))
from runtime import Client

client = Client()  # 默认只读
status, listing = client.get("/api/admin/posts", query={"limit": 1, "offset": 0})
# 有真实 ID 时：post = client.read_post(post_id)
```

## 本地 Markdown

```bash
python3 -B .agents/skills/flare-stack-blog/scripts/tiptap.py encode tools/isomeria-publisher/tests/sample.md
python3 -B .agents/skills/flare-stack-blog/scripts/tiptap.py --self-test
python3 -B -m unittest discover -s tools/isomeria-publisher/tests -v
```

本地编码不需要网络，也不需要管理员密钥。支持标题 2–4、段落、列表、引用、代码块、链接、图片、表格、粗体、斜体、删除线及数学公式等项目声明的 Markdown 子集。复杂 Markdown 应先检查转换结果。`sample.md` 是纯本地验证样例，不是待自动上传的文章。

实际草稿与生成 JSON 保存在用户指定的非版本控制位置，例如仓库已有忽略规则覆盖的 `tmp/isomeria-publisher/`，不要把真实草稿或检查结果混入本工具的源码提交。

## 实时接口合同

每次新的管理任务先运行 `probe.py`，重新核对线上 OpenAPI。它仅做 GET，并把公开规范 `openapi.json` 和不含正文/凭据的 `readiness.json` 写入仓库已忽略的 `tmp/isomeria-publisher/`。缓存不替代下一次任务的实时合同检查，也不应提交。当前 server 为 `https://blog.isomeria.net/api`；下表路径已包含 `/api`，拼接时不要重复添加。规范缺失必需操作或返回不同 server 时停止，不自行换目标。

| 功能 | 方法与路径 | 参数 / 请求体 |
| --- | --- | --- |
| 列表 | GET `/api/admin/posts` | `limit`、`offset`；可选 `search`、`sortBy`、`sortDir`、`includeContent`、分类/标签过滤等 |
| 详情 | GET `/api/admin/posts/{id}` | 正整数文章 ID；`contentJson` 是可编辑正文，`publicSnapshotContentJson` 是已发布快照 |
| 创建独立草稿 | POST `/api/admin/posts` | `{"data":{"title":"...","summary":"...","contentJson":{...}}}`；`title` 必填，其余可选 |
| 保存草稿 | PATCH `/api/admin/posts/{id}` | `{"data":{...}}`；本适配仅允许 `title`、`summary`、`contentJson`、`slug`、`categoryId` |
| 分类列表 | GET `/api/admin/categories` | 默认即可；可选 `sortBy`、`sortDir`、`publicOnly` |
| 标签列表 | GET `/api/admin/tags` | 默认即可；可选排序、`withCount`、`publicOnly` |
| 关联分类 | PATCH `/api/admin/posts/{id}` | `{"data":{"categoryId":分类ID}}`；`null` 清除分类 |
| 关联标签 | PUT `/api/admin/posts/{postId}/tags` | `{"tagIds":[标签ID,...]}`，替换全部当前标签；`[]` 清空，不能误当作追加 |
| 文章标签 | GET `/api/admin/posts/{postId}/tags` | 正整数文章 ID |
| 公开发布 | POST `/api/admin/posts/{id}/publish` | ID 在路径中，无必填 JSON 字段；发布会生成/替换公开快照 |

原 `posts new` 发送 `{}`，可能复用已有空草稿。本流程改用携带 `data` 的创建方式，由服务端保证创建新的独立草稿，不修改已有空草稿。这里的 `{postId}` 与详情路由的 `{id}` 是各自 OpenAPI 的参数名称。

当前规范中列表 `status` 参数被生成为不可满足的 `not: {}` schema；准备检查不传此参数，也不依赖该过滤器。需要筛选草稿时可分页读取列表，并逐篇读取详情确认状态。

## 获得草稿写入任务后的流程

1. 明确目标是创建新草稿，还是更新指定 ID。默认保持草稿；此文件不是线上写入授权。
2. 新建使用 `Client(draft_writes=True).create_draft(...)`，不要用空请求体或复用不明 ID。
3. 更新前读取目标，检查标题、正文、状态和 `updatedAt`，核对用户指定的目标与改动；适配器仅允许更新无公开快照的草稿。
4. 根据分类/标签 GET 的返回，在内存里解析已有名称到 ID。缺少目标分类/标签时报告，不擅自创建。新建请求不能携带分类/标签，应在创建后单独保存关联。
5. 保存后自动 GET 详情，验证本次提交的标题、摘要、正文、分类和标签。正文结构严格比较，仅忽略服务端会移除的代码高亮缓存 `highlightedHtml`；不把公开快照误作草稿正文。
6. 每次操作输出 `client.events` 中的状态码和方法、文章 ID、实际成功/失败/核对结果；不要打印完整 response、headers、环境或认证配置。

仅供获得当前任务明确授权后的 Python 用法。先使用上文的模块导入入口；准备好的 `draft.md` 放在忽略目录，样例不自动执行：

```python
from pathlib import Path
from runtime import Client

client = Client(draft_writes=True)  # 仅在用户要求创建/更新草稿后启用
post = client.create_draft(
    title="用户确认的标题",
    summary="用户确认的摘要",
    markdown=Path("tmp/isomeria-publisher/draft.md").read_text(encoding="utf-8"),
)
print({"post_id": post["id"], "requests": client.events, "result": "draft created and read-back verified"})
```

更新指定草稿前，从已核对的详情保存其 `updatedAt`，然后调用：

```python
saved = client.save_draft(
    post_id,
    expected_updated_at=reviewed_updated_at,
    data={"title": intended_title, "summary": intended_summary,
          "contentJson": client.codec.from_markdown(intended_markdown),
          "categoryId": category_id},
    tag_ids=intended_complete_tag_ids,
)
print({"post_id": saved["id"], "requests": client.events, "result": "draft saved and read-back verified"})
```

省略 `tag_ids` 会保持标签不变；指定它则是替换全量标签。保存字段按需传入，省略的字段保持不变。

## 公开发布与结果报告

先把具体文章 ID、最终内容和发布意图提供给用户确认。只有用户针对该 ID 明确确认后，才创建 `Client(publish_authorized=True, publish_post_id=post_id)`，重新读取并审阅最终内容，再调用 `publish(post_id, expected_updated_at=reviewed_updated_at)`。授权绑定该 ID，其他文章的发布请求会被拒绝。不要在启动脚本、默认参数或环境文件中长期开启此开关。

草稿写入开关不允许发布。发布后自动回读并确认公开快照状态。禁止未经授权删除、取消发布或修改站点设置；本适配器不开放这些接口。下例只在获得针对具体 ID 的授权之后执行，并需单次工具网络权限：

```python
from urllib.parse import quote
from runtime import Client, ORIGIN

publisher = Client(publish_authorized=True, publish_post_id=post_id)
published = publisher.publish(post_id, expected_updated_at=reviewed_updated_at)
slug = published.get("publicSlug")
public_url = f"{ORIGIN}/post/{quote(slug, safe='')}" if isinstance(slug, str) and slug else None
print({"requests": publisher.events, "post_id": published["id"],
       "result": "published and read-back verified", "public_url": public_url})
```

每次结束报告实际 HTTP 状态码、文章 ID、执行结果与核对结果。创建草稿时没有公开链接，应报告 `public_url: null`；发布成功后仅根据回读的 `publicSlug` 生成公开链接，不能用草稿 `slug` 猜测。HTTP 状态未知时明确报告未知，不将超时当作写入失败。

捕获 `OperationError` 时只输出其安全错误摘要及 `client.events`，不要输出原始异常对象、响应正文、完整请求头或 `environment_config()` 返回值。错误摘要不应替代对部分已完成步骤的报告。

## 失败和并发处理

- 任何 HTTP、权限、JSON 或回读核对错误都停止当前操作；错误响应正文不输出，避免凭据或敏感内容泄露。
- `expected_updated_at` 仅是写入前检查，不是服务端原子版本条件；实时 API 没有声明 If-Match/ETag 乐观锁。操作期间避免同时编辑同一文章。
- 创建请求超时、连接中断或成功响应无法解析时，结果可能已落库，不能自动重试 POST。先分页 GET 列表，结合搜索、时间范围和详情中的标题、摘要、正文核实；标题相同不等于同一请求。没有服务端幂等键保障，尚不能排除已创建或有多个候选时必须暂停并请用户决定，不能通过再创建来验证。
- 多步保存不是事务，标题/正文可能已成功而标签失败；应报告具体已完成步骤和 ID，先只读回查实际状态，不自动回滚、删除或发布。后续只在用户授权范围内补做缺失步骤，不能盲目重复整套流程。
- 回读失败也不能解释为写入未发生。通过 `client.events` 和已返回 ID 只读核实实际状态。
- 写接口定义和离线模拟通过，不代表线上写权限已经实测。只有实际得到对应请求及回读结果才能报告该操作成功。

## 验证范围

离线测试保留原有保护检查，并验证目录迁移后依赖可定位、发布授权限定文章 ID、重定向不会向其他主机转发认证头，以及 Markdown 编码往返。迁移本身不需要线上请求，不运行构建、部署或任何博客写操作。

旧会话的 HTTP 200 或空文章列表不是新会话的实时结论。新会话只能根据本次实际只读检查报告状态；列表为空时跳过详情 GET，不为补做验证而创建文章。
