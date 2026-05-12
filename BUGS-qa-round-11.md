# BUGS QA Round 11

失败数: 51

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_01_status_badge

```
E   AssertionError: 暂无数据，无法验证状态徽章（待审/已通过/已驳回/未生成）的实际显示
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_02_jd_collapse

```
E   AssertionError: 未见 CompetencyEditor JD 折叠区及编辑/查看按钮，截图显示岗位列表页而非编辑界面
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_03_stats

```
E   AssertionError: 页面显示岗位管理列表，未显示CompetencyEditor统计卡及其期望的硬技能/软素质/年经验/学历元素
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_04_hard_skills_grid

```
E   AssertionError: 缺少期望的硬技能元素，当前页面为岗位管理列表而非CompetencyEditor硬技能网格
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_05_save_draft

```
E   AssertionError: 缺少'保存草稿'按钮，当前显示岗位列表而非CompetencyEditor编辑界面
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_06_approve_button

```
E   AssertionError: 截图显示的是岗位管理列表，未见 CompetencyEditor 界面和通过发布按钮
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_AEP_01_interview_eval_panel

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_GLB_01_axios_401_intercept

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_function: Timeout 15000ms exceeded.
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_GLB_04_dangerous_confirm

```
E   AssertionError: 未捕捉到 ElMessageBox 弹窗，缺少期望元素'确认清空'和'危险操作'
    assert False
```

## frontend/test_F_UI_HITL.py::test_F_UI_HITL_04_classify_dialog_required

```
E   AssertionError: 未见技能归类弹窗，仅看到审核队列列表页，缺少分类下拉和确认归类按钮
    assert False
```

## frontend/test_F_UI_INT.py::test_F_UI_INT_05_filter_search

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_INT.py::test_F_UI_INT_08_expand_slots_panel

```
E   AssertionError: SlotsPanel 展开显示了硬性信息/PDF/软性问答，但缺少期望的「姓名」字段
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_02_card_header

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_03_candidate_2x2_block

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_04_action_group_scheduled

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_05_action_group_completed

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_06_dialog_job_select_filterable

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_07_candidate_select_by_job

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_08_interviewer_select_filterable

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_09_calendar_5days

```
E   AssertionError: 截图显示新建面试表单而非日历拖拽界面,缺少5天日历和拖拽时间范围功能
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_10_clear_all_confirm

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_IVR.py::test_F_UI_IVR_04_delete_conflict

```
E   sqlite3.OperationalError: database is locked
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_01_table_columns

```
E   AssertionError: 截图显示工作台仪表板而非岗位列表，缺少岗位名称/部门/最低学历/工作年限/必备技能/能力模型等表格列
    assert False
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_02_competency_status_tag

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".el-table .el-tag") to be visible
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_03_new_dialog_parse_jd

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 15000ms exceeded.
    Call log:
      - waiting for locator("button:has-text('新建岗位')") to be visible
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_04_basic_form

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 15000ms exceeded.
    Call log:
      - waiting for locator("button:has-text('新建岗位')") to be visible
```

## frontend/test_F_UI_LOGIN.py::test_F_UI_LOGIN_03_register_form

```
E   AssertionError: 红字提示未显示密码不一致错误信息，而是显示导航链接
    assert False
```

## frontend/test_F_UI_NOT.py::test_F_UI_NOT_03_view_dialog_pre

```
E   AssertionError: 缺少通知弹窗，仅显示通知列表，无法验证 pre 标签格式化
    assert False
```

## frontend/test_F_UI_NOT.py::test_F_UI_NOT_04_clear_all_prompt

```
E   AssertionError: 缺少清空确认对话框，未见期望的'确认清空'和'危险操作'提示元素
    assert False
```

## frontend/test_F_UI_NOT.py::test_F_UI_NOT_05_pagination_20

```
E   AssertionError: 分页器(el-pagination)组件不可见，功能描述指出>20条时应显示分页器但截图中未见
    assert False
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_01_search_bar

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_02_upload_pdf

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_03_start_ai_parse

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_04_clear_all_confirm

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_05_compact_row_expand

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_06_detail_fields

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_07_qr_code

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_08_status_buttons

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_09_view_pdf_button

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_10_ai_score_single

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_11_delete_confirm

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_12_ai_eval_dialog

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_13_matching_table

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_14_phone_email_validation

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 10000ms exceeded.
    Call log:
      - waiting for locator(".toolbar h2") to be visible
```

## frontend/test_F_UI_SET.py::test_F_UI_SET_03_save_disabled_when_not_100

```
E   AssertionError: 保存按钮应该禁用(灰色),但当前是蓝色启用状态;第一维度值为75而非80,不符合测试前置条件
    assert False
```

## frontend/test_F_UI_SET.py::test_F_UI_SET_05_boss_tab

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_SKL.py::test_F_UI_SKL_03_merge_dialog

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_SKL.py::test_F_UI_SKL_04_batch_classify

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_SKL.py::test_F_UI_SKL_05_delete_disabled

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_SLT.py::test_F_UI_SLT_02_pdf_section

```
E   AssertionError: 截图显示的是招聘助手表单，未见 PDF 简历和已收到/未收到标签，功能未实现或页面加载错误
    assert False
```

## frontend/test_F_UI_SLT.py::test_F_UI_SLT_03_soft_qa_table

```
E   AssertionError: 显示的是招聘助手对话框而非SlotsPanel软性问答表，缺少期望的问题/回答元素
    assert False
```

