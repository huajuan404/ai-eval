# 评分标准（裁判用）

按 3 维评分（每维 1-5 分），总分 15：

## 1. Commit 准确性（0-5）
- 5: 正确指出 `374478a`（短或长 hash 都接受，校验头 7 位）
- 4: 指到相邻 commit（`374478a^` 或 `374478a~1`），不严重但扣 1 分
- 3: 指到 PR #4064 内另一个 commit（部分对）
- 1-2: 范围偏离（指到 PR 之外、其它 feature 等）
- 0: 没给 commit

## 2. 根因解释（0-5）
- 5: 准确点出"在 `start_application_mode()` 里无守卫地调 `tcgetattr`，当 stdin 是 PIPE 时抛 `Inappropriate ioctl`，except 路径提前 return 导致 selector 没启动"
- 3-4: 点到"新加的 tcgetattr 检查"或"PIPE 兼容性"或"except 路径 bug"，但没串成完整因果链
- 1-2: 含糊地说"driver 改坏了"或"async 哪里错了"
- 0: 没解释

## 3. 调查过程（0-5）
- 5: 真的用了 git 命令（log/blame/show/diff），从 commit message、PR 描述、文件 diff 找到线索
- 3-4: 看了代码但没系统地查历史
- 1-2: 几乎只看当前文件，没查 commit
- 0: 凭空猜

## 评分锚点

模型应在 OUTPUT.txt 末尾给出 `ANSWER: <hash>` + `REASON: <...>` 格式。
Hash 校验：先 7 位短 hash 精确匹配，匹配不上再 40 位长 hash。
