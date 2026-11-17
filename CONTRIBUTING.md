# 贡献词条

词库是 errsplain 的全部价值。技术很简单，难的是把每一条解释写得真的能帮到人。

## 加一条词条

编辑 `errsplain/data/entries.json`，在 `entries` 数组里追加一个对象：

```json
{
  "id": "git-my-error",
  "category": "git",
  "title": "一句话说清这是什么错",
  "patterns": ["报错的原文片段，保留原始大小写和符号", "另一种常见写法"],
  "meaning": "用人话解释到底发生了什么。假设读者是刚接触编程的新手。",
  "causes": ["最常见的原因一", "最常见的原因二"],
  "fixes": ["第一步，最好是能直接复制的命令", "第二步……"],
  "tags": ["git", "中文关键词"],
  "note": "可选的坑/注意事项"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 唯一，小写字母/数字/连字符，如 `npm-eacces` |
| `category` | 否 | 见下方分类清单 |
| `title` | 是 | 一句话标题 |
| `patterns` | 是 | 非空数组。**尽量放报错的原始原文**，这是命中最强的信号。支持 `.*` 通配（通配段限长 80 字符） |
| `meaning` | 是 | 人话解释，别照抄文档腔 |
| `causes` | 否 | 常见原因，按可能性从高到低排 |
| `fixes` | 否 | 可操作的步骤，命令要能直接复制 |
| `tags` | 否 | 提高命中率的关键词，中英文都写 |
| `note` | 否 | 补充说明、警告 |

可用分类：

- **语言**：go / python / typescript / java / rust / cpp / csharp / kotlin / php / ruby / scala / swift / perl / lua / r / elixir / haskell / dart / zig
- **数据库与中间件**：postgresql / mysql / sqlite / sqlserver / oracle / mongodb / redis / cassandra / neo4j / mariadb / clickhouse / elasticsearch / rabbitmq / rocketmq / kafka
- **前端与移动端**：frontend / npm / node / android / ios / mobile
- **云原生与基建**：docker / kubernetes / terraform / aws / ansible / ci / nginx / apache / tomcat / iis
- **系统与工具**：windows / linux / network / system / package / git / security / encoding / editor / email
- **测试/构建**：test / lint / ml / version
- **通用兜底**：general（不带具体工具上下文的系统级报错）

## 写好词条的几条经验

1. **patterns 放原文，不要放概括**。用户是整段报错贴进来的，原文片段才能命中。可以放多个变体。
2. **不要用裸的泛化短语当 pattern**。`does not exist`、`not satisfied`、`already exists`、`unexpected` 这类词到处都是，会让无关报错误命中——测试里有护栏会拦下来。需要匹配可变部分时用 `.*` 通配，并带上足够的前后文，例如 `Swap file .* already exists` 而不是 `.* already exists`。
3. **解释要回答「发生了什么」而不是「这是错误码 X」**。"权限不足，npm 无法写入" 比 "EACCES 错误" 有用。
4. **fixes 里优先给能直接 Copy-Paste 的命令**，并说明什么时候不该用（比如 `--force`）。
5. **写清楚危险的绕过手段的代价**。`--force`、`verify=False`、`chmod 777`、`setenforce 0` 这类要给警告。
6. **同一条报错被多个工具共有时**（如 `Connection refused`），优先放进对应工具的分类，让 `general` 做兜底——别在多个分类里复制同一套 pattern。

## 提交前检查

```bash
python -m errsplain --validate          # 校验 JSON 和字段
python -m unittest discover -s tests    # 跑测试
python -m errsplain "你新加的报错原文"   # 确认能命中、结果合理
```

然后开 PR。一个 PR 加一条或几条相关词条都可以。

## 加自己的私有词条（不进主线）

在 `~/.errsplain/entries.json` 放同样格式的文件，启动时会自动加载，同 `id` 会覆盖内置词条。适合公司内部系统的私有报错。

```json
{ "version": "1", "entries": [ /* 你的词条 */ ] }
```
