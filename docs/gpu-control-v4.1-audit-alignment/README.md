# 动画管家 ↔ GPU Control V4.1 审计增补包

状态：`DRAFT / JOINT ALIGNMENT REQUIRED / RUNTIME UNCHANGED`

生成日期：2026-07-30（Asia/Shanghai）

本目录把 2026-07-30 动画管家全量日志审计转成双方可实施、可回执、可验收的联合合同。它建立在 V4 合同之上，不改变 manifest 1.0、父批次幂等、all-or-nothing、结果十项验证与原子发布等既有语义。

## 唯一推荐阅读顺序

1. [V4.1 性能与稳定性主合同](01_GPU_CONTROL_V4_1_PERFORMANCE_STABILITY_ALIGNMENT.md)
2. [双方行动矩阵](02_BILATERAL_ACTION_MATRIX.md)
3. [联合验收与同素材基准](03_JOINT_ACCEPTANCE_AND_BENCHMARK.md)
4. [GPU Control 回执模板](04_GPU_CONTROL_RECEIPT_TEMPLATE.md)

## 为什么需要 V4.1 增补

V4 已解决或冻结了输入完整性、幂等、失败/取消分离、远端恢复、结果验证等正确性边界，但今天审计确认仍有以下联合缺口：

- 动画管家 watchdog 超时仍会自动调用远端 cancel；
- 动画管家缺完整 cancel intent，且远端 `CANCELLED` 可被直接映射为用户取消；
- workflow/commit/pipeline SHA 已保存但未成为发布硬门禁；
- 当前 UI 把全局 queue depth 当成任务排队，并把集群上传、排队、GPU、回传合并；
- 集群缺稳定的节点级服务时间、帧延迟、重试与拖尾遥测合同；
- 26 个一键动画流程 0 成功，且流程阶段完全不可计时；
- Cherry 实际执行与 38–42 小时空转间隔未分离；
- 125 个 DONE 仅 20 个有持久投递回执；
- 现有集群历史样本中位加速 2.75×，但尚无同素材、同版本、同负载的正式 A/B 冻结证据。

## 冻结前置条件

只有以下条件全部满足，状态才能从 `JOINT ALIGNMENT REQUIRED` 改为 `FROZEN / PRODUCTION ACCEPTED`：

1. 双方逐项填写回执，而不是只回复“已对齐”；
2. 动画管家必改项完成单元测试和安全窗口发布；
3. GPU Control 必改项提供 source commit、镜像 digest 和自动测试证据；
4. 联合故障注入全部通过；
5. 同素材 A/B 达到速度、稳定性与质量门槛；
6. 新任务投递回执、阶段时间和 trace 覆盖率达到合同目标；
7. 生产观察窗连续 7 天无 P0/P1 回归。

## 基线证据

| 基线 | SHA-256 |
|---|---|
| `docs/GPU_CONTROL_MATTING_HANDOFF_V4_ASSETCLAW_ALIGNMENT.md` | `93F638B40B4B009F9D637E3C4E8000F8FAACA20BF36966E465CC696BA768B52A` |
| `docs/animation-log-audit/generated/summary.json` | `31DC3935E4E4F19A21EAA5CA544157E7F9E2D464D194CC04041C4D83071EA70A` |
| 可视化审计 PDF | `B6B0B7FC52E57E10AB3B97C1FE08C9F5E47C94703D47ADCCC6B52FB323111A83` |

## 本增补包文件哈希

| 文件 | SHA-256 |
|---|---|
| `01_GPU_CONTROL_V4_1_PERFORMANCE_STABILITY_ALIGNMENT.md` | `5959F2CBEE82C6D4C24CE868B63E45DF86F8364BB8D81FCCFEEB4B4B7A833A61` |
| `02_BILATERAL_ACTION_MATRIX.md` | `ABEF0241CD392F201555DC09E10C5E459E1834583F7C2A530C18A605A6CC43BE` |
| `03_JOINT_ACCEPTANCE_AND_BENCHMARK.md` | `EE2918259D5EF96D93D59D75AE0606A265D0456EE0A6E3036F9240E1C67B40D0` |
| `04_GPU_CONTROL_RECEIPT_TEMPLATE.md` | `DDCE974EEA00242E1B0ED08684BFF76A3DD46B2FF01F53F4256AE4DB62F6CA30` |

本次只输出对齐文档，没有调用 GPU Control、修改生产配置、重启服务、取消任务或改变活动批次。
