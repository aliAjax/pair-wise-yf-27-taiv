# 博物馆藏品来源与返还审查

标准库实现、SQLite 持久化的独立项目。它管理藏品、历史流转事件、来源引用、证据、权利主张和审查阶段，并提供面向公众、主张人、审查员和工作人员的分层视图。

## 运行

```bash
python3 app.py --init --seed
python3 app.py
```

访问 <http://127.0.0.1:8103>。数据库默认是 `provenance.db`。测试命令：

```bash
python3 -m unittest -v
```

演示身份通过 `X-User-Id` 传入：`staff`、`reviewer1`、`claimant1`、`public`。

## 主要接口

- `POST /api/objects`、`GET /api/objects`、`GET /api/objects/{id}`：藏品登记与分层查看。
- `POST /api/objects/{id}/update`：更新藏品并创建完整快照。
- `POST /api/sources`、`POST /api/objects/{id}/events`：来源与流转事件。
- `POST /api/objects/{id}/events/{event_id}/source`：工作人员为既有流转补录来源引用。
- `POST /api/objects/{id}/evidence`：上传证据，服务端计算 SHA-256。
- `POST /api/objects/{id}/claims`：提交权利主张。
- `POST /api/claims/{id}/transition`：按 `submitted → under_review → negotiating → resolved_return/rejected` 流转。进入 `negotiating` 或 `resolved_return` 前必须通过来源链核验。
- `GET /api/objects/{id}/history` 与 `/history/{version}`：版本历史及历史快照。

公众和主张人只能看到 `provenance_verified` 是否通过；工作人员和审查员可查看 `provenance_check.missing_items` 中列出的缺项。每段流转都必须有关联来源，藏品至少要有一份证据；缺项时主张保持原阶段，补齐后审查员可重新办理。返还确认后，藏品资料、历史流转、来源链、证据和已完成主张均进入锁定状态，不能再修改或追加。
