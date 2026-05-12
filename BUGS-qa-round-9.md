# BUGS QA Round 9

失败数: 42

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_01_status_badge

```
E   AssertionError: 表格暂无数据，无法验证CompetencyEditor状态徽章（待审/已通过/已驳回/未生成）的显示效果
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_02_jd_collapse

```
E   AssertionError: 页面显示岗位管理列表而非CompetencyEditor，未见JD折叠区和编辑/查看切换按钮
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_03_stats

```
E   AssertionError: 截图显示岗位管理列表，未见 CompetencyEditor 统计卡；缺少硬技能数、软素质数、年经验、学历的统计卡组件
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_04_hard_skills_grid

```
E   AssertionError: 页面显示岗位管理列表无数据，未见CompetencyEditor硬技能网格
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_05_save_draft

```
E   AssertionError: 截图显示岗位管理列表页，未见 CompetencyEditor 界面及期望的'保存草稿'按钮
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_CMP_06_approve_button

```
E   AssertionError: 未见'通过发布'按钮，当前仅显示岗位列表而非CompetencyEditor编辑器
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_PCK_01_autocomplete

```
E   AssertionError: 期望可见元素"合并"未在截图中找到，仅可见"技能"
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_AISP_01_idle_state

```
E   AssertionError: 截图显示岗位管理页面，缺少期望的「候选」和「筛选」关键元素，非AiScreeningPanel界面
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_AISP_02_running_state

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_AISP_03_done_state

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_AISP_04_failed_state

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_ITB_01_items_table

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_AEL_01_resume_ai_eval_list

```
E   AssertionError: verifier 240s timeout
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_AEP_01_interview_eval_panel

```
E   AssertionError: 未找到 AiInterviewEvalPanel 弹窗，当前显示面试安排空列表
    assert False
```

## frontend/test_F_UI_COMPONENTS.py::test_F_UI_GLB_01_axios_401_intercept

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_function: Timeout 10000ms exceeded.
```

## frontend/test_F_UI_HITL.py::test_F_UI_HITL_04_classify_dialog_required

```
E   AssertionError: 这是审核队列列表页而非技能归类弹窗，缺少必选分类下拉和确认按钮
    assert False
```

## frontend/test_F_UI_INT.py::test_F_UI_INT_07_action_buttons

```
E   AssertionError: 表格无数据，操作按钮列存在但没有可操作的记录行，无法验证 Intake 操作按钮实际渲染
    assert False
```

## frontend/test_F_UI_INT.py::test_F_UI_INT_08_expand_slots_panel

```
E   AssertionError: 表格无数据，未展开 Intake 行以显示 SlotsPanel
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_02_card_header

```
E   AssertionError: 页面无候选人卡片，期望的编辑/删除按钮未出现
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_03_candidate_2x2_block

```
E   AssertionError: 截图显示面试安排空页面，未显示候选人信息2x2网格及学校/学历/手机/邮箱元素
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_04_action_group_scheduled

```
E   AssertionError: 页面未显示任何面试卡片，看不到 scheduled 卡片底部操作组及其预期的 5 个操作按钮（腾讯会议、复制邀请、发送通知、AI 面评、取消）
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_05_action_group_completed

```
E   AssertionError: 页面无任何卡片，未见 completed 状态卡片与 AI 面评按钮
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_06_dialog_job_select_filterable

```
E   AssertionError: 未见新建面试弹窗，只显示面试安排列表页面，缺少期望元素：目标岗位和请选择岗位
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_07_candidate_select_by_job

```
E   AssertionError: 新建面试弹窗未打开，期望的候选人和请先选择岗位元素不可见
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_08_interviewer_select_filterable

```
E   AssertionError: 截图显示的是面试安排列表页面，未显示新建面试弹窗，缺少期望的面试官选项和搜索功能
    assert False
```

## frontend/test_F_UI_INV.py::test_F_UI_INV_10_clear_all_confirm

```
E   AssertionError: 未见二次确认对话框，截图仅显示面试安排列表页，缺少清空确认弹窗
    assert False
```

## frontend/test_F_UI_IVR.py::test_F_UI_IVR_04_delete_conflict

```
E   AssertionError: 表格显示暂无数据，无法验证删除操作及409友好提示，测试未能完整执行
    assert False
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_01_table_columns

```
E   AssertionError: 截图显示的是工作台首页而非岗位列表表格,缺少岗位名称/部门/最低学历/工作年限/必备技能/能力模型等期望列
    assert False
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_02_competency_status_tag

```
E   playwright._impl._errors.TimeoutError: Page.wait_for_selector: Timeout 8000ms exceeded.
    Call log:
      - waiting for locator(".el-table .el-tag") to be visible
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_03_new_dialog_parse_jd

```
E   playwright._impl._errors.TimeoutError: Page.click: Timeout 30000ms exceeded.
    Call log:
      - waiting for locator("button:has-text('新建岗位')")
```

## frontend/test_F_UI_JOB.py::test_F_UI_JOB_04_basic_form

```
E   playwright._impl._errors.TimeoutError: Page.click: Timeout 30000ms exceeded.
    Call log:
      - waiting for locator("button:has-text('新建岗位')")
```

## frontend/test_F_UI_LOGIN.py::test_F_UI_LOGIN_04_enter_submits

```
E   AssertionError: 密码框显示有输入(***),但错误信息为'请输入密码'而非'密码过短',不符合短密码校验的预期
    assert False
```

## frontend/test_F_UI_NOT.py::test_F_UI_NOT_02_status_tag_colors

```
E   AssertionError: 表格无数据，无法验证通知状态tag颜色规则
    assert False
```

## frontend/test_F_UI_NOT.py::test_F_UI_NOT_05_pagination_20

```
E   AssertionError: 表格暂无数据，无法验证分页器在>20条记录时的显示
    assert False
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_01_search_bar

```
E   playwright._impl._errors.TimeoutError: Locator.fill: Timeout 30000ms exceeded.
    Call log:
      - waiting for locator("input[placeholder*='搜索姓名']")
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_02_upload_pdf

```
E   AssertionError: 缺上传按钮
    assert 0 >= 1
     +  where 0 = count()
     +    where count = <Locator frame=<Frame name= url='http://localhost:5174/#/resumes'> selector="button:has-text('上传PDF简历')">.count
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_03_start_ai_parse

```
E   AssertionError: 缺手动启动内容解析按钮
    assert 0 >= 1
     +  where 0 = count()
     +    where count = <Locator frame=<Frame name= url='http://localhost:5174/#/resumes'> selector="button:has-text('手动启动内容解析'), button:has-text('后台内容解析中')">.count
```

## frontend/test_F_UI_RES.py::test_F_UI_RES_04_clear_all_confirm

```
E   playwright._impl._errors.TimeoutError: Page.click: Timeout 30000ms exceeded.
    Call log:
      - waiting for locator("button:has-text('清空全部')")
```

## frontend/test_F_UI_SET.py::test_F_UI_SET_03_save_disabled_when_not_100

```
E   AssertionError: 保存权重配置按钮应置灰disabled，但实际显示为蓝色启用状态
    assert False
```

## frontend/test_F_UI_SLT.py::test_F_UI_SLT_01_hard_table

```
E   AssertionError: 截图显示的是候选人列表页面，非 SlotsPanel 硬性信息表，缺少预期的字段/原话/来源等列
    assert False
```

## frontend/test_F_UI_SLT.py::test_F_UI_SLT_02_pdf_section

```
E   AssertionError: 缺少SlotsPanel PDF简历区及'已收到/未收到'标签，表格暂无数据
    assert False
```

## frontend/test_F_UI_SLT.py::test_F_UI_SLT_03_soft_qa_table

```
E   AssertionError: 截图显示的是候选人列表页面，未见软性问答面板，缺少期望的「问题」、「回答」等关键元素
    assert False
```

