# 发布到 GitHub 的步骤（首次）

> **状态（v0.1.0, release-v0.1.0 todo 完成后）**
> - ✅ 154 / 154 tests green
> - ✅ `mindkeep --version` = 0.1.0
> - ✅ `mindkeep doctor` = All checks passed 🎉
> - ✅ `dist/mindkeep-0.1.0-py3-none-any.whl` 已构建
> - ✅ `dist/mindkeep-0.1.0.tar.gz` 已构建
> - ✅ `dist/SHA256SUMS` 已生成（见下）
> - ✅ 所有 hotfix 已 commit（install.ps1 pipx、security P1、upgrade regression、README、CI）
> - ✅ Tag `v0.1.0` 已打在本地
> - ⏭️ 下一步见 [`NEXT-STEPS.md`](./NEXT-STEPS.md) — 由你执行 `gh repo create` / `gh release create`
>
> **实际 SHA256（来自 `dist/SHA256SUMS`）**
> ```
> 494ca7f9b497aa288c86d86c869c08c71ac5cb84d652acb747771e5b4513deb5  mindkeep-0.1.0-py3-none-any.whl
> 43c1f95e86dd3c1cce66ba70d7104fd5099f91218e0ec8f76382a816fb888590  mindkeep-0.1.0.tar.gz
> ```

本文档由 `github-repo-bootstrap` todo 生成。仓库已在本地 `git init` 并完成首次 commit；
以下步骤由**你本人**执行，agent 不代劳（保留凭据与最终确认权）。

## 前置条件

- 已安装 [GitHub CLI](https://cli.github.com/): `gh --version`
- 在仓库根目录（`<repo-root>`）下

## 1. 登录 GitHub（如未登录）

```powershell
gh auth status
# 如未登录：
gh auth login
```

## 2. 创建远端仓库并 push

```powershell
cd $env:REPO_ROOT
gh repo create AllenS0104/mindkeep --public --source=. --push
```

这会：
- 在 `github.com/AllenS0104/mindkeep` 创建公开仓库
- 将当前目录设为 source
- 把本地 `main` 分支 push 上去

## 3. 构建发布产物并创建 Release

```powershell
# 构建 wheel 和 sdist
python -m pip install --upgrade build
python -m build

# 创建 v0.1.0 release 并上传产物
gh release create v0.1.0 dist/*.whl dist/*.tar.gz `
  --title "v0.1.0" `
  --notes "First public release of mindkeep. Stdlib-only, SQLite/WAL cross-project memory store for AI agents."
```

## 3.1 Release 产物哈希（SHA256SUMS）

> **为什么**：一键安装（`curl | bash` / `iwr | iex`）本质是执行远端代码。发布哈希让用户可以核对下载内容未被篡改，是供应链安全的基础防线。

对每次 Release，构建完产物后生成 `SHA256SUMS`，随 wheel / sdist / 安装脚本一并上传；并把哈希粘到 Release notes。

### Windows (PowerShell)

```powershell
cd $env:REPO_ROOT
# 对发布产物 + 安装脚本一起计算
$files = @(
  "dist\mindkeep-0.1.0-py3-none-any.whl",
  "dist\mindkeep-0.1.0.tar.gz",
  "install.ps1",
  "install.sh"
)
$files | ForEach-Object {
  $h = (Get-FileHash -Algorithm SHA256 $_).Hash.ToLower()
  "$h  $(Split-Path -Leaf $_)"
} | Set-Content -Encoding ascii SHA256SUMS

Get-Content SHA256SUMS
```

### macOS / Linux (bash)

```bash
cd mindkeep
sha256sum dist/*.whl dist/*.tar.gz install.ps1 install.sh > SHA256SUMS
cat SHA256SUMS
```

### 上传并写入 Release notes

```powershell
# 把 SHA256SUMS 追加到现有 release
gh release upload v0.1.0 SHA256SUMS

# 或在 create 时一并带上
gh release create v0.1.0 dist/*.whl dist/*.tar.gz install.ps1 install.sh SHA256SUMS `
  --title "v0.1.0" `
  --notes-file RELEASE_NOTES.md
```

在 Release notes 里加一节，示例：

````markdown
## 🔐 SHA256 checksums

```
<粘贴 SHA256SUMS 内容>
```

Verify before running:

```powershell
# Windows
iwr https://github.com/AllenS0104/mindkeep/releases/download/v0.1.0/install.ps1 -OutFile install.ps1
Get-FileHash -Algorithm SHA256 install.ps1
```

```bash
# macOS / Linux
curl -fsSL -o install.sh https://github.com/AllenS0104/mindkeep/releases/download/v0.1.0/install.sh
curl -fsSL -o SHA256SUMS https://github.com/AllenS0104/mindkeep/releases/download/v0.1.0/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```
````

## 4. 验证一键安装链接

share/install 脚本（由 `install-ps1` todo 产生）发布后应可通过 raw URL 访问：

```powershell
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex
```

确认：
- 链接返回 200
- 脚本可成功安装 `mindkeep` CLI
- `mindkeep --version` 输出 `0.1.0`

## 回滚 / 重做

```powershell
# 删除远端仓库（谨慎！）
gh repo delete AllenS0104/mindkeep --yes

# 删除某个 release
gh release delete v0.1.0 --yes
```
