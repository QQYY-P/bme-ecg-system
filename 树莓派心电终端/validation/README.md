# 验证资料与运行输出

[终端导航](../README.md) · [工具索引](../tools/README.md) · [公开评估报告](../../docs/algorithm-validation/README.md)

这里提供验证资料入口；临时进程状态与演示消息不再作为已跟踪的验证结果保存。

| 需要查阅 | 入口 / 保存规则 |
| --- | --- |
| QRS/RR、HRV 公开评估 | [报告目录](../../docs/algorithm-validation/README.md)，含配置、代码摘要和公开数据范围 |
| 当前模型数值一致性 | [cardioedge 测试向量及来源](../cardioedge/README.md) |
| 历史 CNN、LSTM 原理图 | [算法参考图](../docs/archive/algorithm-illustrations/README.md)，不作为验证证据 |
| 局域网演示消息 | `local_link/`、`local_link_smoke/` 为本机运行输出，已忽略；旧状态可在 Git 历史中追溯 |
| 实体采集和个人数据 | 按项目约定保留在本机授权位置，不上传到 GitHub |

新增可公开验证资料时，使用能说明内容的文件名，并登记：运行日期、对应代码提交、数据来源/许可、运行命令、设备与环境、结果、未覆盖项。日期或来源未知就明确写未知，不从文件名猜测。

只保存经核对的可公开报告与必要图示；不提交原始心电、账号数据库、凭据、日志包或生成安装包。模拟、公开数据回放、软件测试和实物采集分别标注。
