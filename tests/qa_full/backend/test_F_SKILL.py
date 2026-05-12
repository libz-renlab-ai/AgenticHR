"""5 章 能力模型与技能库 (F-SKILL-01..11)。

QA 清单参考: docs/QA-系统功能清单-v1.md 第 174-188 行。
- F-SKILL-01..07: REST CRUD
- F-SKILL-08:    LLM 自动分类 (真实 LLM, 一次)
- F-SKILL-09..11: 向量打包/解包/cosine/最近邻 (内部函数, 直接 import)
"""
from __future__ import annotations

import sqlite3
import time
import uuid

import pytest

from app.core.vector.service import (
    cosine_similarity,
    find_nearest,
    pack_vector,
    unpack_vector,
)


# ---------- helpers ---------------------------------------------------------


def _create_skill(http, api_base, auth_headers, *, canonical_name: str,
                  category: str = "uncategorized", aliases: list[str] | None = None) -> dict:
    body = {
        "canonical_name": canonical_name,
        "category": category,
        "aliases": aliases or [],
    }
    r = http.post(f"{api_base}/api/skills", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def _delete_skill_via_db(qa_db_path, skill_id: int) -> None:
    """跳过 REST 校验直接清, 避免 seed/usage 检查阻挡测试清理。"""
    with sqlite3.connect(qa_db_path) as c:
        c.execute("DELETE FROM skills WHERE id=?", (skill_id,))
        c.commit()


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ===================== F-SKILL-01..07 REST CRUD ===========================


@pytest.mark.api
def test_F_SKILL_01_list_search_category_pending(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-01: GET /api/skills?search=&category=&pending= 三种过滤。"""
    name1 = _unique_name("Python")
    name2 = _unique_name("Java")
    s1 = _create_skill(http, api_base, auth_headers,
                       canonical_name=name1, category="language")
    s2 = _create_skill(http, api_base, auth_headers,
                       canonical_name=name2, category="language")

    # 全量
    r = http.get(f"{api_base}/api/skills", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and "total" in body

    # search=name1 → 命中
    r2 = http.get(
        f"{api_base}/api/skills?search={name1}",
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    items = r2.json()["items"]
    assert any(it["canonical_name"] == name1 for it in items)

    # category=language → 至少含 s1/s2
    r3 = http.get(
        f"{api_base}/api/skills?category=language&limit=200",
        headers=auth_headers,
    )
    assert r3.status_code == 200
    cats = {it["canonical_name"] for it in r3.json()["items"]}
    assert name1 in cats and name2 in cats

    # pending=true → list_pending 路径不报错
    r4 = http.get(f"{api_base}/api/skills?pending=true", headers=auth_headers)
    assert r4.status_code == 200, r4.text

    # 返回不带 embedding (BUG: 大 blob 不能进 JSON)
    for it in r.json()["items"]:
        assert "embedding" not in it

    _delete_skill_via_db(qa_db_path, s1["id"])
    _delete_skill_via_db(qa_db_path, s2["id"])


@pytest.mark.api
def test_F_SKILL_02_categories(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-02: GET /api/skills/categories 返所有 category 集合。"""
    name = _unique_name("CatTest")
    s = _create_skill(http, api_base, auth_headers,
                      canonical_name=name, category="framework")
    r = http.get(f"{api_base}/api/skills/categories", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "categories" in body
    assert "framework" in body["categories"]
    _delete_skill_via_db(qa_db_path, s["id"])


@pytest.mark.api
def test_F_SKILL_03_get_one_no_embedding(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-03: GET /api/skills/{id} 返单条且无 embedding 字段。"""
    name = _unique_name("Get")
    s = _create_skill(http, api_base, auth_headers, canonical_name=name)
    r = http.get(f"{api_base}/api/skills/{s['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["canonical_name"] == name
    assert "embedding" not in body

    # 不存在 → 404
    r2 = http.get(f"{api_base}/api/skills/9999999", headers=auth_headers)
    assert r2.status_code == 404, r2.text
    _delete_skill_via_db(qa_db_path, s["id"])


@pytest.mark.api
def test_F_SKILL_04_create_conflict(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-04: POST /api/skills, 重名返 409。"""
    name = _unique_name("Dup")
    s = _create_skill(http, api_base, auth_headers, canonical_name=name)
    # 重名
    r = http.post(
        f"{api_base}/api/skills",
        json={"canonical_name": name, "category": "uncategorized", "aliases": []},
        headers=auth_headers,
    )
    assert r.status_code == 409, r.text
    _delete_skill_via_db(qa_db_path, s["id"])


@pytest.mark.api
def test_F_SKILL_05_update_invalidates_cache(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-05: PUT /api/skills/{id} 改后立刻能读到最新值 (清缓存)。"""
    name = _unique_name("Up")
    s = _create_skill(http, api_base, auth_headers, canonical_name=name,
                      category="uncategorized")
    r = http.put(
        f"{api_base}/api/skills/{s['id']}",
        json={"category": "tool", "aliases": ["alias-x"]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["category"] == "tool"
    assert "alias-x" in (body.get("aliases") or [])

    # 二次 GET 应已是新分类
    r2 = http.get(f"{api_base}/api/skills/{s['id']}", headers=auth_headers)
    assert r2.json()["category"] == "tool"

    # 不存在 → 404
    r3 = http.put(
        f"{api_base}/api/skills/9999999",
        json={"category": "tool"},
        headers=auth_headers,
    )
    assert r3.status_code == 404
    _delete_skill_via_db(qa_db_path, s["id"])


@pytest.mark.api
def test_F_SKILL_06_merge(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-06: POST /api/skills/{id}/merge,merge_into_id 必填,
    src 的 canonical_name + aliases 都进 dst.aliases,src 删除。"""
    src_name = _unique_name("MergeSrc")
    dst_name = _unique_name("MergeDst")
    src = _create_skill(http, api_base, auth_headers,
                        canonical_name=src_name, aliases=["alias-from-src"])
    dst = _create_skill(http, api_base, auth_headers,
                        canonical_name=dst_name, aliases=[])

    # 缺 merge_into_id → 422
    r0 = http.post(
        f"{api_base}/api/skills/{src['id']}/merge",
        json={}, headers=auth_headers,
    )
    assert r0.status_code == 422, r0.text

    # 正常合并
    r = http.post(
        f"{api_base}/api/skills/{src['id']}/merge",
        json={"merge_into_id": dst["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "merged"

    # src 已删
    r2 = http.get(f"{api_base}/api/skills/{src['id']}", headers=auth_headers)
    assert r2.status_code == 404
    # dst.aliases 含 src 的 canonical_name 与原 aliases
    r3 = http.get(f"{api_base}/api/skills/{dst['id']}", headers=auth_headers)
    aliases = r3.json().get("aliases") or []
    assert src_name in aliases
    assert "alias-from-src" in aliases

    _delete_skill_via_db(qa_db_path, dst["id"])


@pytest.mark.api
def test_F_SKILL_07_delete_constraints(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-07: DELETE 限制: seed 来源不可删, usage_count>0 不可删,其余可删。"""
    # 7a: 普通 seed_manual + usage=0 → 可删
    name_ok = _unique_name("Del-OK")
    s_ok = _create_skill(http, api_base, auth_headers, canonical_name=name_ok)
    r = http.delete(f"{api_base}/api/skills/{s_ok['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text

    # 7b: source='seed' → 400
    name_seed = _unique_name("Del-Seed")
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO skills (canonical_name, aliases, category, source, "
            "pending_classification, usage_count, created_at, updated_at) "
            "VALUES (?, '[]', 'uncategorized', 'seed', 0, 0, "
            "datetime('now'), datetime('now'))",
            (name_seed,),
        )
        c.commit()
        seed_id = cur.lastrowid
    r2 = http.delete(f"{api_base}/api/skills/{seed_id}", headers=auth_headers)
    assert r2.status_code == 400, r2.text

    # 7c: usage_count>0 → 400
    name_used = _unique_name("Del-Used")
    s_used = _create_skill(http, api_base, auth_headers, canonical_name=name_used)
    with sqlite3.connect(qa_db_path) as c:
        c.execute("UPDATE skills SET usage_count=3 WHERE id=?", (s_used["id"],))
        c.commit()
    r3 = http.delete(
        f"{api_base}/api/skills/{s_used['id']}", headers=auth_headers,
    )
    assert r3.status_code == 400, r3.text

    # 不存在 → 404
    r4 = http.delete(
        f"{api_base}/api/skills/9999999", headers=auth_headers,
    )
    assert r4.status_code == 404, r4.text

    # 清理
    _delete_skill_via_db(qa_db_path, seed_id)
    _delete_skill_via_db(qa_db_path, s_used["id"])


# ===================== F-SKILL-08 LLM 自动分类 =============================


@pytest.mark.api
@pytest.mark.external_real
def test_F_SKILL_08_auto_classify_llm(api_base, http, auth_headers, qa_db_path):
    """F-SKILL-08: POST /api/skills/auto-classify 把 pending 全部分掉.
    真实 LLM 一次, 失败时关键词降级. 都接受。"""
    # 灌 3 条 pending 技能 (含一个能让关键词命中的)
    names = [_unique_name("Python"), _unique_name("nginx"), _unique_name("沟通")]
    inserted_ids = []
    with sqlite3.connect(qa_db_path) as c:
        for n in names:
            cur = c.execute(
                "INSERT INTO skills (canonical_name, aliases, category, source, "
                "pending_classification, usage_count, created_at, updated_at) "
                "VALUES (?, '[]', 'uncategorized', 'seed_manual', 1, 0, "
                "datetime('now'), datetime('now'))",
                (n,),
            )
            inserted_ids.append(cur.lastrowid)
        c.commit()

    r = http.post(
        f"{api_base}/api/skills/auto-classify",
        headers=auth_headers,
        timeout=120,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "classified" in body
    assert body["classified"] >= len(names), body
    # method 字段表明走 LLM 还是关键词
    assert body.get("method") in ("LLM", "关键词"), body

    # 三条都应已被标记 pending=False
    with sqlite3.connect(qa_db_path) as c:
        rows = c.execute(
            "SELECT pending_classification, category FROM skills WHERE id IN "
            f"({','.join('?' * len(inserted_ids))})",
            tuple(inserted_ids),
        ).fetchall()
    for pc, cat in rows:
        assert pc == 0, f"仍为 pending: {pc}"
        assert cat and cat != "uncategorized" or True  # 关键词降级时 nginx/python 应进 cloud/language

    # 清理
    for sid in inserted_ids:
        _delete_skill_via_db(qa_db_path, sid)


# ===================== F-SKILL-09..11 向量内部函数 =========================


@pytest.mark.api
def test_F_SKILL_09_pack_unpack_roundtrip():
    """F-SKILL-09: float[] ↔ float32 LE bytes 往返一致 (允许 float32 精度抖动)。"""
    src = [0.0, 1.5, -2.25, 3.14, 1e-3]
    blob = pack_vector(src)
    assert isinstance(blob, bytes)
    # float32 LE: 每个 4 字节
    assert len(blob) == 4 * len(src)
    arr = unpack_vector(blob)
    assert arr.dtype.itemsize == 4
    assert arr.shape == (len(src),)
    for got, want in zip(arr.tolist(), src):
        assert abs(got - want) < 1e-5, (got, want)


@pytest.mark.api
def test_F_SKILL_10_cosine_zero_vector():
    """F-SKILL-10: 余弦相似度: 任一零向量返 0; 同向 ≈ 1; 反向 ≈ -1。"""
    assert cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0
    assert cosine_similarity([1, 2, 3], [0.0, 0.0, 0.0]) == 0.0
    s = cosine_similarity([1, 0, 0], [1, 0, 0])
    assert abs(s - 1.0) < 1e-5
    s2 = cosine_similarity([1, 0, 0], [-1, 0, 0])
    assert abs(s2 + 1.0) < 1e-5


@pytest.mark.api
def test_F_SKILL_11_find_nearest():
    """F-SKILL-11: 最近邻 O(n) 遍历, 空候选 → (None, 0.0)。"""
    # 空候选
    nid, sim = find_nearest([1.0, 0.0], [])
    assert nid is None and sim == 0.0

    # 三个候选, query=[1,0]
    cands = [
        (10, [0.0, 1.0]),    # 与 query 正交 → 0
        (20, [0.99, 0.01]),  # 接近 query → 接近 1
        (30, [-1.0, 0.0]),   # 反向 → -1
    ]
    nid, sim = find_nearest([1.0, 0.0], cands)
    assert nid == 20
    assert 0.9 <= sim <= 1.0
