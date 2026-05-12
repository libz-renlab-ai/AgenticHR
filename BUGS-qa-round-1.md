# BUGS QA Round 1

失败数: 56

## backend/test_F_FB.py::test_F_FB_03_status_anonymous

```
E   AssertionError: {"detail":"未登录，请先登录"}
    assert 401 == 200
     +  where 401 = <Response [401 Unauthorized]>.status_code
```

## backend/test_F_FB.py::test_F_FB_04_command_handler_user_isolation

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_HITL.py::test_F_HITL_01_list_basic

```
E   AssertionError: {"detail":"未登录，请先登录"}
    assert 401 == 200
     +  where 401 = <Response [401 Unauthorized]>.status_code
```

## backend/test_F_HITL.py::test_F_HITL_01_list_filter_by_stage_status

```
E   AssertionError: {"detail":"未登录，请先登录"}
    assert 401 == 200
     +  where 401 = <Response [401 Unauthorized]>.status_code
```

## backend/test_F_HITL.py::test_F_HITL_02_get_includes_payload

```
E   AssertionError: {"detail":"未登录，请先登录"}
    assert 401 == 200
     +  where 401 = <Response [401 Unauthorized]>.status_code
```

## backend/test_F_HITL.py::test_F_HITL_02_get_404

```
E   AssertionError: {"detail":"未登录，请先登录"}
    assert 401 == 404
     +  where 401 = <Response [401 Unauthorized]>.status_code
```

## backend/test_F_HITL.py::test_F_HITL_07_competency_approve_hook_updates_job

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_01b_start_competency_not_approved

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_01c_start_no_meeting_id

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_01d_start_account_not_in_pool

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_01e_start_active_job_exists

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_02_get_job_detail

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_03_by_interview_returns_latest

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_03b_by_interview_no_job

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_04_by_resume_returns_scorecards

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_05_get_scorecard_with_files

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_06_get_transcript

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_06b_get_transcript_missing

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_07_get_recording

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_07b_get_recording_missing

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_08_cancel_pending_job

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_08b_cancel_terminal_job_rejected

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_09_status_machine_check_constraint

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_12_startup_zombie_recovery_imports

```
E   sqlalchemy.exc.NoReferencedTableError: Foreign key associated with column 'interview_eval_jobs.interview_id' could not find table 'interviews' with which to generate a foreign key to target column 'id'
```

## backend/test_F_IE.py::test_F_IE_12b_sweep_finds_stale_pending

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_16_recording_path_priority

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_17b_retention_purges_expired_row

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_19_spawn_failure_marks_failed

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_IE.py::test_F_IE_20_audit_failure_no_rollback

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: jobs.greet_threshold
```

## backend/test_F_INT.py::test_F_INT_06_pdf_path_validation[\u7b80\u5386.pdf]

```
E   AssertionError: {"candidate_id":6,"intake_status":"collecting","next_action":{"type":"send_hard","text":"您好PDF测试~\n想跟您先确认几个信息：\n1. 您好~请问您最快什么时候可以到岗呢？\n2. 方便告知您接下来五天哪些时段可以面试吗？\n3. 请问您实习能持续多久呢？","slot_keys":["arrival_date","free_slots","intern_duration"]}}
    assert 200 == 422
     +  where 200 = <Response [200 OK]>.status_code
```

## backend/test_F_INT.py::test_F_INT_21_autoscan_rank

```
E   AssertionError: new candidate 21 not in rank items {1, 2, 4, 6, 11, 12, 13, 14, 19, 20}
    assert 21 in {1, 2, 4, 6, 11, 12, ...}
```

## backend/test_F_INT.py::test_F_INT_23_start_conversation_url_encode_inject_defense

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: intake_candidates.phone
```

## backend/test_F_MATCH.py::test_F_MATCH_01_score_pair

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_02_results_list_filters_dead

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_04_recompute_background

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_05_recompute_status

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_06_legacy_set_action

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_08_hash_stale_detection

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_09_cascade_purge_after_recompute

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_10_evidence_llm_degrade

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MATCH.py::test_F_MATCH_11_derive_tags

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.boss_id
```

## backend/test_F_MEET.py::test_F_MEET_01_auto_create_real

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_MEET.py::test_F_MEET_02_all_busy_returns_409

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_MEET.py::test_F_MEET_03_exclude_self_when_rebuilding

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_NOTI.py::test_F_NOTI_06_template_generated_even_when_external_off

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_NOTI.py::test_F_NOTI_07_list_logs_by_interview_desc

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_NOTI.py::test_F_NOTI_08_clear_all_only_current_user

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_REC_SET.py::test_F_REC_02_record_greet_success

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_REC_SET.py::test_F_REC_02_record_greet_failure

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: resumes.seniority
```

## backend/test_F_REC_SET.py::test_F_SET_01_get_scoring_weights

```
E   AssertionError: {"detail":"未登录，请先登录"}
    assert 401 == 200
     +  where 401 = <Response [401 Unauthorized]>.status_code
```

## backend/test_F_REC_SET.py::test_F_AIE_03_status

```
E   AssertionError: {"detail":"未登录，请先登录"}
    assert 401 == 200
     +  where 401 = <Response [401 Unauthorized]>.status_code
```

## backend/test_F_SCH.py::test_F_SCH_04_delete_interviewer_blocked_when_pending

```
E   sqlite3.IntegrityError: NOT NULL constraint failed: notification_logs.recipient_type
```

## backend/test_F_SCH.py::test_F_SCH_12b_patch_reschedule_pipeline

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_SCH.py::test_F_SCH_15_clear_all_immediate_db_clean

```
E   AssertionError: 他人面试不应被清
    assert 2 == 1
```

## backend/test_F_SCH.py::test_F_SCH_18_reschedule_guardrail_db_untouched

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_SCH.py::test_F_SCH_19_feishu_calendar_sync_on_delete

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

