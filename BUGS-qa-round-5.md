# BUGS QA Round 5

失败数: 6

## backend/test_F_IE.py::test_F_IE_17b_retention_purges_expired_row

```
E   sqlite3.IntegrityError: FOREIGN KEY constraint failed

The above exception was the direct cause of the following exception:
E   sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed
    [SQL: DELETE FROM jobs WHERE id=?]
    [parameters: (89701,)]
    (Background on this error at: https://sqlalche.me/e/20/gkpj)
```

## backend/test_F_IE.py::test_F_IE_19_spawn_failure_marks_failed

```
E   sqlite3.IntegrityError: FOREIGN KEY constraint failed

The above exception was the direct cause of the following exception:
E   sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed
    [SQL: DELETE FROM jobs WHERE id=?]
    [parameters: (89901,)]
    (Background on this error at: https://sqlalche.me/e/20/gkpj)
```

## backend/test_F_IE.py::test_F_IE_20_audit_failure_no_rollback

```
E   sqlite3.IntegrityError: FOREIGN KEY constraint failed

The above exception was the direct cause of the following exception:
E   sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed
    [SQL: DELETE FROM jobs WHERE id=?]
    [parameters: (90001,)]
    (Background on this error at: https://sqlalche.me/e/20/gkpj)
```

## backend/test_F_INT.py::test_F_INT_05_collect_chat_llm_extract

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_SCH.py::test_F_SCH_01_create_interviewer_lookup_open_id

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

## backend/test_F_SCH.py::test_F_SCH_08_freebusy_real_feishu

```
E   httpcore.ReadTimeout: timed out

The above exception was the direct cause of the following exception:
E   httpx.ReadTimeout: timed out
```

