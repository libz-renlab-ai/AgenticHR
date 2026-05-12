# BUGS QA Round 4

失败数: 3

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

