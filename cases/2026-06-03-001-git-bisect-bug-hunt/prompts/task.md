# 任务：找出引入 stdin hang regression 的 commit

## 背景

`textual` 项目（Python TUI 库）出现了一个回归 bug：

- 在 v0.47.x 时，下面的脚本可以正常退出（returncode 0，~1s 内结束）：
  ```python
  from textual.app import App
  class A(App): pass
  A().run()
  ```
  当 stdin 是 PIPE 时（典型场景：被 `subprocess.run(..., input=...)` 调起来）：
  ```bash
  python app.py < /dev/null   # 期望退出
  echo "q" | python app.py    # 期望消费输入后退出
  ```
- 在 v0.48.0 起，这个脚本会**永远挂起**，stack 卡在 `_run_once → _selector.select(timeout)`，永远等不到事件。

工作目录 `repo/` 是这个仓库在 **v0.48.0 引入 bug 的 commit** 上 checkout 出来的。
你已经被放在这个 commit 上。

## 任务

1. 用 `git log` / `git show` / `git blame` / `git diff` 等命令
2. **找出引入这个 bug 的 commit hash（短 7 位或长 40 位都行）**
3. **简要说明**为什么是这个 commit 引入的（1-2 句话）

把你的回答**写在 stdout 的最末尾**，格式：

```
ANSWER: <commit-hash>
REASON: <一句话原因>
```

## 提示（不要直接看答案）

- 关键文件：`src/textual/drivers/linux_driver.py`（新加的 `start_application_mode()`）
- 关键词：`tcgetattr`、`PIPE`、`Inappropriate ioctl for device`
- 修复 commit 在 374478a 之后（PR #4105 加了 `os.isatty` 守卫）
