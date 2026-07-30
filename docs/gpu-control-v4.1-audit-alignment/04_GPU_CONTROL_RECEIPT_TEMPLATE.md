# GPU Control V4.1 回执模板

GPU Control 团队请复制本文件，在新文档中逐项填写。不要只回复“已对齐”。未知项写 `UNKNOWN`，未实现写 `false` 并给 owner 和 ETA。

```yaml
gpu_control_v4_1_receipt:
  status: REVIEWED|IMPLEMENTING|ROLLED_OUT|JOINT_ACCEPTANCE_PASSED
  reviewed_documents:
    - path: docs/gpu-control-v4.1-audit-alignment/01_GPU_CONTROL_V4_1_PERFORMANCE_STABILITY_ALIGNMENT.md
      sha256: <sha>
    - path: docs/gpu-control-v4.1-audit-alignment/03_JOINT_ACCEPTANCE_AND_BENCHMARK.md
      sha256: <sha>

  source:
    repository: <url/name>
    git_commit: <full sha>
    api_image_digest: <sha256:...>
    scheduler_image_digest: <sha256:...>
    web_image_digest: <sha256:...>
    node_worker_image_digests:
      control-4090: <digest>
      worker-3090-a: <digest>
      worker-3090-b: <digest>

  api_compatibility:
    manifest_schema_version: "1.0"
    v4_contract_preserved: true|false
    additive_parent_status_only: true|false
    create_batch_is_authoritative_admission: true|false
    capacity_is_advisory_only: true|false
    server_persists_queue_after_accept: true|false

  timing_contract:
    created_at: true|false
    validated_at: true|false
    queued_at: true|false
    started_at_means_first_gpu_execution: true|false
    last_progress_at: true|false
    execution_finished_at: true|false
    assembling_started_at: true|false
    artifact_ready_at: true|false
    finished_at: true|false
    timestamps_monotonic_and_restart_stable: true|false
    implementation_location: <path/module>

  performance_contract:
    parent_returns_input_pixels_total: true|false
    parent_returns_reassignments: true|false
    parent_returns_scheduler_restart_count: true|false
    node_returns_gpu_model_and_worker_version: true|false
    node_returns_frames_assigned_succeeded_failed: true|false
    node_returns_upload_and_prompt_attempts_separately: true|false
    node_returns_gpu_service_ms: true|false
    node_returns_frame_latency_p50_p95: true|false
    node_metrics_reconcile_with_parent_counts: true|false
    child_performance_endpoint: <method/path>

  workflow_identity:
    key: imageclip-rgba
    version: 2026.07.27-721f7d6-r1
    imageclip_commit: 721f7d68635ee36d45f545ce2c82037046147442
    pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
    returned_on_create: true|false
    returned_on_every_parent_get: true|false
    returned_in_artifact_manifest: true|false
    node_claim_fail_closed_on_mismatch: true|false

  upload_integrity:
    overwrite_true: true|false
    readback_size_check: true|false
    readback_sha256_check: true|false
    max_integrity_attempts: 3
    prompt_blocked_until_verified: true|false
    upload_attempt_separate_from_job_attempt: true|false
    zero_byte_fault_test_id: <id|null>

  failure_and_error_codes:
    stable_error_domain_and_code: true|false
    upload_failure_distinct_from_prompt_failure: true|false
    child_failure_keeps_parent_running_until_all_settle: true|false
    permanent_child_failure_makes_parent_failed: true|false
    failed_parent_has_no_result_archive: true|false
    failure_never_sets_cancel_requested: true|false
    implementation_location: <path/module>

  cancellation:
    only_authenticated_user_or_admin_can_cancel: true|false
    required_idempotency_key: <external_batch_id>:cancel
    timeout_never_cancels: true|false
    node_failure_never_cancels: true|false
    cancel_audit_has_actor_source_reason_request_id: true|false
    replay_returns_same_cancel_operation: true|false
    cancelled_without_audit_is_impossible: true|false

  recovery:
    postgres_is_source_of_truth: true|false
    scheduler_restart_reuses_batch_job_attempt_ids: true|false
    node_lease_and_comfy_history_reconciled_before_retry: true|false
    node_offline_reassigns_without_parent_cancel: true|false
    timing_and_performance_survive_restart: true|false

  scheduling_optimization:
    weighted_by_node_pixel_throughput: true|false
    dynamic_work_stealing: true|false
    speculative_retry_for_tail_frames: true|false
    model_or_workflow_kept_warm: true|false
    advisory_estimated_queue_ms_returned: true|false
    straggler_ratio_measurable: true|false

  security:
    production_api_key_enabled: true|false
    tls_verify_required: true|false
    approved_ca_digest: <sha256>
    test_and_production_tenants_isolated: true|false

  tests:
    normal_and_idempotency_report_id: <id|null>
    zero_byte_upload_report_id: <id|null>
    prompt_timeout_report_id: <id|null>
    permanent_frame_failure_report_id: <id|null>
    node_offline_report_id: <id|null>
    scheduler_restart_report_id: <id|null>
    invalid_cancel_state_report_id: <id|null>
    workflow_drift_report_id: <id|null>
    artifact_tamper_report_id: <id|null>
    benchmark_session_id: <id|null>
    machine_report_sha256: <sha|null>

  performance_result:
    fixed_bundle_sha256: <sha|null>
    b97_three_node_runs: <n>
    b97_three_node_gpu_p50_seconds: <value|null>
    b97_three_node_gpu_p90_seconds: <value|null>
    b97_paired_speedup_median: <value|null>
    b97_paired_speedup_p10: <value|null>
    queue_wait_p90_ms: <value|null>
    artifact_return_p95_ms: <value|null>
    straggler_ratio_p95: <value|null>
    gpu_batch_success_rate_7d: <value|null>
    all_performance_gates_passed: true|false

  rollout:
    active_batches_drained: true|false
    comfy_queues_checked: true|false
    rollout_started_at: <UTC|null>
    rollout_finished_at: <UTC|null>
    canary_10_percent_passed: true|false
    canary_50_percent_passed: true|false
    seven_day_observation_passed: true|false
    rollback_version: <version/digest>

  ownership:
    api_owner: <name/id>
    scheduler_owner: <name/id>
    node_worker_owner: <name/id>
    observability_owner: <name/id>
    acceptance_owner: <name/id>

  unresolved_items:
    - id: <P0/P1/...>
      description: <text>
      owner: <name/id>
      target_date: <date>
      blocker: <text>

  declaration:
    no_fastest_sample_cherry_picking: true|false
    no_quality_gate_bypass: true|false
    no_runtime_claim_without_joint_evidence: true|false
    signed_by: <name/id>
    signed_at: <UTC>
```

## 动画管家收到回执后的处理

1. 校验文档 SHA、source commit 和镜像 digest；
2. 把所有 `false/UNKNOWN` 映射回行动矩阵；
3. 只在 P0/P1 全部实现后安排联合故障注入；
4. 正确性通过后才运行速度 A/B；
5. 速度和质量通过后才进入 10%/50% 灰度；
6. 7 天观察通过后双方共同签署生产冻结。
