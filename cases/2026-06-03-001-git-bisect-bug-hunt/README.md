# 用例: git-bisect-bug-hunt（textual v0.48.0 stdin hang）

**class**: `tool-using` — 模型必须用 git CLI（log/show/blame/diff）调查。

## 来源

- **Bug**: textual v0.48.0 起的 subprocess stdin hang
- **引入 commit**: [`374478a`](https://github.com/Textualize/textual/commit/374478a0b12640df4753c041770e824a2c4259f0) — PR #4064 "Move the main work on suspending with Ctrl+Z into the Linux driver"
- **修复 commit**: [`8c2b5d2`](https://github.com/Textualize/textual/pull/4105) — PR #4105 "Only perform the SIGTOU check in the Linux driver when hooked up to a tty"
- **Issue**: [#4104](https://github.com/Textualize/textual/issues/4104) — pablogsal 自己 `git bisect` 锁定了 374478a
- **影响版本**: 0.48.0、0.48.1；0.48.2 起已修

## 任务

input/repo 包含 textual @ 374478a（已 checkout，git 全历史可用）。
模型要用 git 工具找出**哪个 commit 引入了 stdin hang regression**，
格式 `ANSWER: <hash>` + `REASON: <...>`。

## 评判

- **check.sh**（确定性）: 抽 `ANSWER:` 行与 expected.commit 比对，hash 前 7 位匹配 = pass
- **judge**（advisory，3 维 × 5 分 = 15）: commit 准确性 / 根因解释 / 调查过程

## 为什么这个 case 特别适合 ai-eval

1. **真 regression**（0.47.x 一切正常，0.48.0 必坏）
2. **headless 验证**（subprocess stdin 即可，不需要 GUI）
3. **启动器差异会被暴露**：
   - codex 自带 bash 工具，git 操作天然顺手
   - claude -p 默认用 `--dangerously-skip-permissions` + Bash tool
   - `c N` 路由下行为可能不同
4. **既测 tool-use 又测 reasoning**：要用 git CLI 还要读 diff 找根因
