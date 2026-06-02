# 用例: fizzbuzz（种子用例）

小型可验证编码任务，用于验证 benchmark 全链路（跑→判→分享）。

## 任务

agent 在隔离工作目录中补全 `solution.py` 的 `fizzbuzz(n)`，使 `test_solution.py` 通过。

## 判分

- **check**（确定性）：`check.sh` 跑 `python3 test_solution.py`，退出 0 = 通过。
- **judge**（advisory）：按 `prompts/rubric.md` 给 correctness / code_quality 打分。

## 运行

```bash
# 默认 runner（codex）+ 对照（claude），每格跑 2 次
./run.sh -c 2026-06-02-001-fizzbuzz -r codex,claude --repeat 2
```

输出落在 `output/<runner标签>/`（已 gitignore），计分卡生成在仓库根 `scorecards/`。

## 已知验证缺口

- 跨引擎不可移植场景（`requires_engine` 的 skill/slash 用例）本期未覆盖，留作后续用例补充。
- 本用例为 check 型；judge-only 的开放型任务（写作/方案）建议另建用例验证。
