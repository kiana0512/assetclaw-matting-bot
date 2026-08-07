# GPU Control 1.5.5 候选回执审阅与联合测试输入交付

状态：`GPU CODE ALIGNED_NOT_TESTED / ASSETCLAW INPUTS FROZEN / PRODUCTION HOLD / JOINT ACCEPTANCE PENDING`

日期：2026-07-30（Asia/Shanghai）

本文件是动画管家收到 `65_2026-07-30_ASSETCLAW_POST_OPTIMIZATION_ALIGNMENT_RECEIPT.md` 后的第三轮对齐回复。它确认代码合同方向、交付固定 benchmark 输入，并列出进入联合测试前 GPU Control 必须关闭的证据门禁。本文件不授权升级、迁移、重启或故障注入生产集群。

## 1. 输入回执核验

| 项目 | 核验值 | 结论 |
|---|---|---|
| GPU 回执 SHA-256 | `40938b4884ba788f509fd0a4942ee10630962825a861bcad744bb85bbe383047` | 已读、完整 |
| GPU 回执声明的输入 SHA-256 | `9b04040655a7ad7d3090e848dd86551547fbee8e45dd9074fa0374e951ca9e26` | 与动画管家 `06_POST_OPTIMIZATION_GPU_CONTROL_ALIGNMENT.md` 实测一致 |
| GPU 候选版本 | `1.5.5` | 仅工作树候选 |
| GPU 生产版本 | `1.5.4` | 运行时未改变 |
| GPU 候选源码 commit | `PENDING_BUILD` | 未提交工作树不能充当 source commit |
| G-P0-01～07 | `CODE_PRESENT` / 测试入口存在 | 方向接受，证据未关闭 |
| 自测原始报告 | `PENDING_TEST_RUN` | 阻断构建和联合测试 |
| 四镜像 digest / SBOM / provenance | `PENDING_BUILD` | 阻断部署和联合测试 |
| 隔离 tenant/API key/时间窗 | `PENDING_SECURE_EXCHANGE` | 阻断真实联测 |
| 生产迁移 | 未执行，生产 DB 仍 `20260729_0010` | 正确，继续保持 |

本轮决定：**接受 GPU Control 1.5.5 的代码对齐方向，但不接受为已测试、已构建、已发布或可生产灰度。** 当前生产继续保持 `1.5.4`。

## 2. 合同对账结论

GPU 回执对以下关键合同已给出正确实现方向：

1. batch-owned child cancel 返回 `409 BATCH_CHILD_CANCEL_FORBIDDEN`；
2. 父状态非 `SUCCEEDED` 时父/子 artifact 返回 `409 ARTIFACT_NOT_READY`；
3. 公共父 cancel 认证、持久、幂等，并区分 POST `CANCEL_REQUESTED` 与 GET `CANCELLING`；
4. 父批次固化 workflow/pipeline/output-node 身份快照；
5. 混合版本节点调度跳过不兼容节点而不阻塞后续兼容节点；
6. prompt 提交崩溃窗口只通过 Comfy queue/history 对账收养，不二次 POST；
7. 候选版本统一为 `1.5.5`，但仍需干净 source commit、不可变镜像与 SBOM 证明。

双方字段确认：

- 公共 cancel POST：`status=CANCEL_REQUESTED`；
- 普通父 GET 在途取消：`status=CANCELLING`；
- 合法终态取消：`status=CANCELLED` 且关联有效 operation/audit；
- 装配阶段时间的正式字段：`assembling_at`；动画管家同时兼容旧别名 `assembling_started_at`；
- 新任务完整身份必须包含 `output_node=SaveImage #25`；
- `performance` 中无法权威测量的值允许为 `null`，但不得伪造；正式联合性能验收所需的逐节点 service、帧数、P50/P95、像素和 attempt 必须有真实值。

## 3. 动画管家本轮已完成

### 3.1 客户端合同兼容

动画管家已完成但尚未因本回执单独重载生产 Gateway 的源码调整：

- 将 `output_node` 纳入批准身份：`SaveImage #25`；
- 服务端返回非空且不匹配时 fail closed；
- 现有生产 `1.5.4` 暂缺 `output_node` 时保持 `PARTIAL_MATCH`，不破坏当前任务；
- 同时接收正式 `assembling_at` 和旧别名 `assembling_started_at`，内部保留兼容字段；
- 新增身份漂移、旧生产兼容和阶段字段测试。

本地回归：`456 passed, 8 warnings`。GPU Control 专项：`23 passed, 3 warnings`。

### 3.2 冻结联合基准输入

冻结会话：`v4_1-20260730-r1`

机器事实源：`storage/gpu_control_v4_1_acceptance/frozen_inputs/v4_1-20260730-r1/bundle_index.json`

- 文件 SHA-256：`44e908d53eba884caaeeefa97f88115354739f444508755767b1ad05320d21987`（比较时不区分十六进制大小写）
- 内部 canonical index SHA-256：`b4cac1999b69ad80bb05803e1cb1a88715f482b9ae662a7691f9c88a1cbe2741`
- 生成器：`scripts/prepare_gpu_control_v4_1_benchmarks.py`
- 生成器 SHA-256：`f3f453571c9203c99eb1c782d952f39f4bf0a141a564f65a902f680097a227f5`
- 网络调用：无；生产任务创建：无；源图片修改：无。

B1～B97 都是现场任务 `VID_717F2D6BC22C / 2惊讶.mp4` 的有序前缀；B97 与本次 50 分 10 秒现场审计使用完全相同的 97 张 1080×1440 PNG。B300 在 B97 后按固定顺序补充两个已完成真实任务的帧。

| Bundle | 帧数 | 总像素 | 输入 ZIP bytes | 输入 ZIP SHA-256 | canonical manifest SHA-256 |
|---|---:|---:|---:|---|---|
| B1 | 1 | 1,555,200 | 506,070 | `b2fb238fd5b4d0464a4e9740a16ac4503714aa09becee8de5baf4a5c0e1dbd3a` | `6aceb448453f8f3fa1fbe5e453161a274ef6a0ac7a168bf1df68653a246a47bb` |
| B6 | 6 | 9,331,200 | 3,128,251 | `30a460b5b2b83a0e58bc63e2e1b15c4ee7bf8e9cb3ae618009a439f2db579337` | `013e8ef74f52864b056b022360281099169d71777e56b274d2dd32416b5ae49d` |
| B30 | 30 | 46,656,000 | 14,439,039 | `25940a3552789df396c0995622778aaf2812d496830ac1c956c0796110eae38e` | `379b5031a8160e588424b2da4c4894cf328b1da8b3ee601f9376365f098bf525` |
| B64 | 64 | 99,532,800 | 31,764,974 | `4c77b995801416b904855eb60c2990d7cbd3ed2cf24215dd80fe365a55f9628b` | `0b73247de67cd31bf52ecdbffb9b30a617ad5bf089fb42dab6d8d8a2d968d14d` |
| B97 | 97 | 150,854,400 | 49,712,651 | `63c8fdc106d8e3d3e9574885639b2c6cc4f29fb84b18c02ce21d259722033276` | `1e54c97b72d8ec6f30695bf86d93b88fd54fc6b545b3975a18bbb86f0f3301b5` |
| B300 | 300 | 438,912,000 | 163,160,206 | `1776a65aee8432a365aa9b61ab9b9c9844108afd49bee586bc4c7e00b66b3763` | `6eeda613161f32bea664a3bb7812aafe65e0e03e4b71b67d5b96e11d39e826e2` |

每个 Bundle 目录包含 `input.zip`、`input_manifest.json` 和 `benchmark_metadata.json`。双方交换后必须先核对 ZIP SHA、canonical manifest SHA 和逐帧 size/SHA/width/height/pixels，任一不一致不得进入速度统计。

### 3.3 当前只读健康事实

核验时：

- 动画管家全部本地活动任务为 0；
- Gateway `/health` HTTP 200；
- GPU Control readiness：database/redis 均 `ok`；
- GPU Control：3/3 eligible nodes、3 available slots、queue 0、running 0；
- 未创建、取消、迁移、下载或改派任何 GPU 批次。

## 4. GPU Control 下一步必须执行

以下步骤必须顺序完成，任一步失败即停止：

1. 在隔离数据库、Fake ComfyUI 或候选容器中执行回执 §6.1 的完整自测，生成原始 report 和 SHA；
2. 完成 PostgreSQL `20260729_0010 → 20260730_0011 → 20260729_0010 → 20260730_0011` 隔离迁移测试；
3. 将候选源码提交并推送为一个干净、可远端解析的 40 位 source commit；
4. 只从该 source commit 构建 API/Scheduler/Asset API/Web 四个 `1.5.5` 镜像；
5. 推送 registry，返回四个不可变 manifest digest、OCI labels、SBOM 和 provenance；
6. 生成不可变 `1.5.4` rollback image digest、回滚命令和实测恢复时间；
7. 运行 `make verify-release-identity`，证明 source commit、四镜像 revision 和四份 SBOM 一致；
8. 使用上述冻结包在 GPU 侧隔离环境先做 prepare/import 校验，不执行生产任务；
9. 通过安全渠道提供隔离 tenant/API key、CA、测试调度约束和安全时间窗；
10. 返回第三轮最终证据回执，动画管家审核通过后才启动双方联合故障注入与速度 A/B。

## 5. GPU Control 下一份回执必须填满

```yaml
gpu_control_candidate:
  status: SELF_TESTED_BUILT_NOT_DEPLOYED
  version: "1.5.5"
  source_commit: <40-hex-pushed-commit>
  source_tree_clean: true
  self_test_session_id: <id>
  self_test_report_path: <path>
  self_test_report_sha256: <sha256>
  backend_passed: <int>
  backend_failed: 0
  frontend_passed: <int>
  frontend_failed: 0
  migration_report_sha256: <sha256>
  production_database_used_for_test: false
  images:
    api: {manifest_digest: "sha256:...", sbom_sha256: "...", revision: "<source_commit>"}
    scheduler: {manifest_digest: "sha256:...", sbom_sha256: "...", revision: "<source_commit>"}
    asset_api: {manifest_digest: "sha256:...", sbom_sha256: "...", revision: "<source_commit>"}
    web: {manifest_digest: "sha256:...", sbom_sha256: "...", revision: "<source_commit>"}
  rollback:
    production_version: "1.5.4"
    manifest_digest: "sha256:..."
    command: <exact-command>
    measured_recovery_seconds: <number>
  frozen_inputs:
    acceptance_session_id: "v4_1-20260730-r1"
    bundle_index_file_sha256: "44e908d53eba884caaeeefa97f88115354739f444508755767b1ad05320d21987"
    all_bundle_archive_sha256_match: true
    all_manifest_and_frame_hashes_match: true
  secure_joint_test:
    tenant_ready: true
    api_key_shared_out_of_band: true
    ca_shared_out_of_band: true
    production_isolation_proof: <reference>
    safe_window_utc: <range>
```

同时必须附带：G-P0-01～07 每项 test ID、退出码、原始 report 路径/SHA，create/GET/cancel/artifact/capacity 脱敏 JSON，以及至少一个隔离 B6 的真实 `performance.nodes[]` 父子对账样例。

## 6. 联合测试启动条件

只有以下条件全部为真，动画管家才会真正提交 B1/B6/B30/B64/B97/B300 和 `3×B97`：

- GPU 端自测和迁移测试全部通过；
- source commit 已推送且工作树干净；
- 四镜像 digest、revision、SBOM 一致；
- rollback 镜像和命令可用；
- 冻结输入 SHA 全部一致；
- 隔离 tenant/key/CA/调度约束已通过安全渠道交换；
- 联测时生产活动任务为 0，或隔离性足以证明不会影响生产；
- 双方明确批准当次测试窗口。

当前这些条件尚未全部满足，所以本轮不执行真实 GPU 批次、不执行节点离线、不重启 Scheduler、不迁移生产数据库。

## 7. 本轮上线决策

```yaml
decision:
  assetclaw_source_contract_update: PASS
  assetclaw_regression: PASS_456
  benchmark_inputs_frozen: PASS
  gpu_candidate_code_direction: ACCEPTED_FOR_SELF_TEST
  gpu_candidate_self_test: PENDING
  gpu_candidate_build: PENDING
  joint_fault_injection: PENDING
  joint_benchmark: PENDING
  production_rollout: HOLD
```

动画管家稳定回滚基线继续有效：

`storage/rollback_snapshots/20260730-115717_stable-pre-v4_1_1f84ceba1276`

在 GPU Control 返回完整不可变候选证据前，双方生产事实保持不变。
