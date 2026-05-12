# BUGS QA Round 2

失败数: 11

## backend/test_F_FB.py::test_F_FB_04_command_handler_user_isolation

```
E   AssertionError: BUG-039 回归: dashboard 应只返 uid=1 数据, 实际:
      📊 招聘概览
      
      总简历数：32
      待筛选：0
      已通过：32
      待面试：22
    assert '总简历数：1' in '📊 招聘概览\n\n总简历数：32\n待筛选：0\n已通过：32\n待面试：22'
```

## backend/test_F_IE.py::test_F_IE_03_by_interview_returns_latest

```
E   assert 3 == 4
```

## backend/test_F_IE.py::test_F_IE_17b_retention_purges_expired_row

```
E   AssertionError: purge 应处理 >=1 行, got 0
    assert 0 >= 1
```

## backend/test_F_IE.py::test_F_IE_19_spawn_failure_marks_failed

```
E   AssertionError: assert 404 == 500
     +  where 404 = ServiceError(code=404, message='面试 81901 不存在').code
     +    where ServiceError(code=404, message='面试 81901 不存在') = <ExceptionInfo ServiceError(code=404, message='面试 81901 不存在') tblen=2>.value
```

## backend/test_F_IE.py::test_F_IE_20_audit_failure_no_rollback

```
E   assert 0 >= 1
```

## backend/test_F_JOB.py::test_F_JOB_03_list_jobs_user_isolation

```
E   AssertionError: Internal Server Error
    assert 500 == 200
     +  where 500 = <Response [500 Internal Server Error]>.status_code
```

## backend/test_F_MATCH.py::test_F_MATCH_01_score_pair

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_MATCH.py::test_F_MATCH_02_results_list_filters_dead

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_MATCH.py::test_F_MATCH_08_hash_stale_detection

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_MATCH.py::test_F_MATCH_09_cascade_purge_after_recompute

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_SCH.py::test_F_SCH_02_list_interviewers_owner_only

```
E   AssertionError: Internal Server Error
    assert 500 == 200
     +  where 500 = <Response [500 Internal Server Error]>.status_code
```

