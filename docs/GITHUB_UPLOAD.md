# GitHub 上架指南

把本项目上传到 GitHub 的完整步骤（本项目已完成打包与首次提交，你只需建远程仓库并 push）。

## 0. 上架前自查（重要）

- [ ] 已确认无任何 API Key 泄露：`backend/data/llm_config.json`、`backend/.env` 已被 `.gitignore` 忽略，不会被提交。
- [ ] 已确认无内网 IP / 个人代理地址泄露（代码中已清理）。
- [ ] `backend/data/parquet/` 下的大文件已拆分为 `<50MB` 分片（`daily.parquet.part*`、`valuation.parquet.part*`），完整文件与 `market.duckdb` 不进入仓库。
- [ ] 已确认 `LICENSE`（默认 MIT）符合你的预期。

可用下面的命令再做一次“密钥扫描”兜底：

```bash
grep -rniE "sk-[A-Za-z0-9]{16,}|10\.144\.|api[_-]?key[[:space:]]*=" \
  --include="*.py" --include="*.sh" --include="*.ps1" --include="*.md" \
  --include="*.json" --include="*.ts" --include="*.tsx" . \
  | grep -v node_modules | grep -v ".venv" | grep -v "/.run/"
```

> 预期只会命中 `backend/.env.example` 与 `.gitignore` 中的**占位说明**，不应出现真实密钥或代理 IP。

## 1. 在 GitHub 新建空仓库

1. 登录 [github.com](https://github.com) → 右上角 `+` → **New repository**。
2. 仓库名建议：`ashare-simulator`、`stock` 或 `ashare-backtest`。
3. 可见性：选择 **Public**（开源）。
4. **不要**勾选 “Add a README / .gitignore / license”（避免与本地内容冲突，保持空仓库）。
5. 点击 **Create repository**。

## 2. 关联远程仓库并推送

在项目根目录执行（把 `YOUR_NAME/YOUR_REPO` 换成你的 GitHub 用户名与仓库名）：

```bash
cd /path/to/Stock

# 若尚未初始化 git（本项目通常已初始化并完成首次提交）：
#   git init -b main
#   git add .
#   git commit -m "chore: open-source initial commit"

git remote add origin https://github.com/YOUR_NAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

> 首次推送含约 220MB 的数据分片，会稍慢；属正常现象（分片均已 <50MB，不会触发 GitHub 单文件限制）。

## 3. 验证

- 浏览器打开 `https://github.com/YOUR_NAME/YOUR_REPO`，确认 README、目录结构、数据分片都已展示。
- 确认 `backend/data/llm_config.json`、`.env`、`backend/data/duckdb/`、`backend/.venv/`、`frontend/node_modules/` **没有**出现在仓库里。

## 4. 后续更新

数据更新后需重新分片再提交：

```bash
backend/.venv/bin/python scripts/split_data.py   # 重新拆分大 parquet
git add .
git commit -m "chore: update data"
git push
```

## 常见问题

- **push 被拒（大文件）**：说明有超过 100MB 的文件被加入，检查是否误提交了完整的 `daily.parquet` / `valuation.parquet` / `market.duckdb`（应在 `.gitignore` 中）。
- **403 / 认证失败**：GitHub 已不支持密码推送，请使用 Personal Access Token（PAT）或 SSH。生成 PAT：GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)，勾选 `repo` 权限；推送时用户名填 GitHub 用户名、密码填该 Token。
- **想修改协议**：直接替换根目录 `LICENSE` 文件内容，并同步 README 中的 License 章节即可。
