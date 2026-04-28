# 下一步（由你执行）

所有本地构建与验证已完成。以下命令由你亲自运行，agent 不代劳（保留凭据与最终确认权）。

## 1. 创建 GitHub 仓库并 push

```powershell
cd $env:REPO_ROOT

# 如未登录
gh auth login

# 创建远程仓库并 push main
gh repo create AllenS0104/mindkeep --public --source=. --push

# push tag
git push origin v0.1.0
```

## 2. 创建 Release 并上传产物

```powershell
cd $env:REPO_ROOT

gh release create v0.1.0 `
  dist/mindkeep-0.1.0-py3-none-any.whl `
  dist/mindkeep-0.1.0.tar.gz `
  dist/SHA256SUMS `
  --title "v0.1.0" `
  --notes-file RELEASE-NOTES.md
```

## 3. 验证 share 链接（仓库 public 之后）

```powershell
# Windows
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash
```

安装完成后：
```bash
mindkeep --version    # -> mindkeep 0.1.0
mindkeep doctor       # -> All checks passed 🎉
```

## 4. 分享

把 `SHARE.md` 内容直接粘贴到 IM 发给朋友即可。

---

## 产物校验码 (SHA256)

```
494ca7f9b497aa288c86d86c869c08c71ac5cb84d652acb747771e5b4513deb5  mindkeep-0.1.0-py3-none-any.whl
43c1f95e86dd3c1cce66ba70d7104fd5099f91218e0ec8f76382a816fb888590  mindkeep-0.1.0.tar.gz
```

（与 `dist/SHA256SUMS` 一致）
