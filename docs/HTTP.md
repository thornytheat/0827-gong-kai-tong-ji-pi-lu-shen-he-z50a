# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。

签发 `APPROVED` 时会把本批与同一 `period`、`base` 下已签发公报刊载的表合并做线性反推判定：可通过追加遮蔽消除唯一解时返回 `APPROVED` 并在 `issuance.plan` 给出最终遮蔽方案；即使遮蔽全部可舍单元格仍泄露时，本批落为 `HELD`，已签发批次不变。响应中的 `issuance.conclusion` 与 `issuance.reason_code` 给出结论和理由。相同签发意见的原报文重试会返回同一签发结果；也支持 `Idempotency-Key` 请求头。`GET /api/publications`（别名 `/api/bulletins`）返回已生效公报。

`GET /api/runs/<id>/publication` 返回复核结论；`POST /api/publication/check` 可对 JSON 形态的 `current` 与 `history/published` 直接做跨批判定。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据和复核信息的 JSON。
