/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  dateParts as rawDateParts,
  deviceTimeZone,
  formatDate as rawFormatDate,
  type Fmt,
  type Lang,
} from "@/lib/format";

export type { Lang };

/** 界面语言 + 时区，一处定义、全站生效。
 *
 *  **为什么放在前端而不是数据库**：这两项只改"东西长什么样"，不改同步抓什么、
 *  怎么比对（同步内部一律用带时区的 UTC 比较）。放数据库就得为一个纯显示偏好
 *  拉一次接口、还要在同步跑的时候上锁，代价全花在没有的问题上。
 *
 *  **为什么后端不出文案**：后端本来会直接返回中文（板块状态、设置提示、错误、
 *  提醒摘要）。那些现在一律返回 key，文案全在这份词典里 —— 否则切成英文
 *  界面上会中英混排，而且已经写进库里的历史提醒永远停在写入时的语言。
 */

// ---------------------------------------------------------------- 词典
//
// 中文是基准（开发时先写中文），英文由 TS 强制补齐：`Record<keyof typeof zh>`
// 少一个 key 就编译不过。`{x}` 是插值位。

const zh = {
  // ---- 通用 ----
  "common.cancel": "取消",
  "common.confirm": "确认",
  "common.save": "保存",
  "common.saving": "保存中…",
  "common.loading": "加载中…",
  "common.close": "关闭",
  "common.back": "返回",
  "common.processing": "处理中…",
  "common.details": "详情",
  "common.collapse": "收起",
  "common.gotIt": "知道了",
  "common.on": "开启",
  "common.off": "关闭",

  // ---- 侧边栏 ----
  "nav.updates": "更新提醒",
  "nav.resources": "所有资源",
  "nav.calendar": "日历",
  "nav.tasks": "任务",
  "nav.courses": "课程",
  "nav.manageHint": "课程管理：选择边栏显示哪些课程并排序",
  "nav.noCourses": "边栏里没有课程。去「课程管理」看看是不是都藏起来了，或者去「设置」选择要同步的课程。",

  // ---- 课程通用 ----
  "course.concluded": "已结课",
  "course.resourceCount": "{n} 个资源",
  "course.untitled": "课程",

  // ---- 课程管理 ----
  "manager.title": "课程管理",
  "manager.panel": "边栏课程",
  "manager.visible": "显示 {n} / {total}",
  "manager.empty": "还没有在同步的课程。去「设置 → 选择同步课程」勾选。",
  "manager.drag": "拖动排序",
  "manager.showInSidebar": "在边栏显示",
  "manager.hidden": "已隐藏",
  "manager.sortAlpha": "按字母重排",
  "manager.revert": "撤销更改",
  "manager.saved": "边栏已更新",

  // ---- 更新提醒 ----
  "updates.title": "更新提醒",
  "updates.markAllRead": "全部标记已读",
  "updates.empty": "暂无更新。首次同步只会入库、不产生提醒，之后 Canvas 上有变动就会出现在这里。",
  "updates.unread": "未读 · 点击标记",

  // 事件类型（提醒左边的小标签）
  "event.new_assignment": "新作业",
  "event.updated_assignment": "作业变更",
  "event.new_announcement": "新公告",
  "event.new_message": "新消息",
  "event.updated_page": "页面更新",
  "event.new_file": "新文件",
  "event.updated_file": "文件更新",
  "event.new_link": "新链接",
  "event.updated_link": "链接更新",
  "event.due_soon": "临近截止",
  "event.sync_error": "同步异常",

  // 事件摘要。**由 kind + 课程名 + payload 现拼**，不用后端写死的那句话 ——
  // 那句话是同步当时写进库里的，切语言之后改不了
  "event.summary.new_assignment": "新作业，截止 {due}",
  "event.summary.new_announcement": "新公告",
  "event.summary.new_message": "来自 {author} 的新消息",
  "event.summary.new_page": "页面更新",
  "event.summary.new_file": "新文件",
  "event.summary.updated_file": "文件已更新",
  "event.summary.new_link": "新链接",
  "event.summary.updated_link": "链接已更新",
  "event.summary.updated": "有更新：{fields}",
  "event.field.title": "标题",
  "event.field.due_at": "截止时间",
  "event.field.body_html": "正文",
  "event.field.content": "内容",
  "event.noDue": "无截止时间",
  "event.someone": "教授",

  // ---- 同步 ----
  "sync.preparing": "正在准备…",
  "sync.running": "正在同步…",
  "sync.runningCourses": "正在同步：{courses}",
  "sync.courseProgress": "{done} / {total} 门课",
  "sync.interval.off": "关闭自动同步",
  "sync.interval.minutes": "每 {n} 分钟",
  "sync.interval.hours": "每 {n} 小时",
  "sync.interval.days": "每 {n} 天",
  "sync.next": "下次 {when}",
  "sync.notScheduled": "未安排",
  "sync.startingSoon": "即将开始",
  "sync.inMinutes": "约 {n} 分钟后",
  "sync.underMinute": "不到 1 分钟后",

  // ---- 设置页 ----

  "settings.language": "语言",
  "settings.autoTimezone": "自动时区",
  "settings.autoTimezoneHint": "当前时区：{tz}",
  "settings.timezone": "时区",
  "settings.timezoneSearch": "搜索时区…",
  "settings.timezoneNoMatch": "没有匹配的时区",

  "settings.notConfigured": "未配置",
  "settings.canvasStep1": "在 Canvas 网页版打开 Account → Settings",
  "settings.canvasStep2": "拉到 Approved Integrations，点 + New Access Token",
  "settings.canvasStep3": "把令牌粘贴到上面，然后保存",
  "settings.canvasUrl": "Canvas 地址",
  "settings.canvasToken": "访问令牌",
  "settings.canvasUnchanged": "没有变化",
  "settings.canvasConfirm": "更新 Canvas 连接？",
  "settings.canvasConfirmBody": "将以 {name} 同步所有课程。",
  "settings.canvasSwitchBody": "这是另一个账号（{name}），当前课程数据会被清除。",
  "settings.canvasConfirmAction": "保存并同步",
  "settings.canvasSwitchAction": "清除并同步",

  "settings.sectionSync": "同步",
  "settings.sectionFiles": "文件",
  "settings.syncTasks": "任务",
  "settings.syncEvents": "日历事件",
  "settings.itemCount": "{n} 条",
  "settings.plannerWriteback": "完成状态同步到 Canvas",
  "settings.courseColors": "课程颜色",
  "settings.resetColor": "恢复默认",
  "settings.syncNow": "立即同步所有课程",
  "settings.syncAll": "立即同步",
  "settings.syncing": "同步中…",
  "settings.waitingCourse": "等待课程同步完成…",
  "settings.intervalLabel": "自动同步间隔",
  "settings.intervalOff": "已关闭",
  "settings.pickCourses": "选择同步课程",
  "settings.pickCoursesButton": "选择课程",
  "settings.pickedCount": "已选 {n} / {total} 门",
  "settings.savedShort": "设置已保存",

  "toggle.auto_download_files": "自动下载文件到本地",
  "toggle.sync_inline_images": "同步内联图片",
  "toggle.sync_links": "同步链接",
  "toggle.sync_announcement_links": "同步公告中的链接",
  "toggle.sync_discussions": "同步讨论区",
  "toggle.ask": "{action}{name}？",

  "settings.courseStatus": "课程同步状态",
  "settings.noCourses": "还没有课程，先跑一次同步",
  "settings.manualSync": "手动同步",
  "settings.syncingShort": "同步中",
  "settings.noSyncRecord": "这门课还没有同步记录",
  "settings.unsupportedHint": "这门课的「{boards}」我们还扫不了，里面的资料不会出现在列表里",
  "settings.syncingAllTip": "正在同步所有课程，请等它跑完",
  "settings.courseSyncTip": "只同步这门课，不影响自动同步的时间",

  "status.ok": "同步成功",
  "status.failed": "同步失败",
  "status.syncing": "同步中",
  "status.never": "尚未同步",

  // 板块扫描结果（一门课的 Canvas 导航栏上那一列）
  "board.scanned": "已扫描",
  "board.unavailable": "学校未开放",
  "board.disabled": "已在设置里关闭",
  "board.unsupported": "还不支持",

  // 选择同步课程
  "picker.title": "选择同步课程",
  "picker.pickedCount": "已选 {n} 门",
  "picker.active": "在读",
  "picker.concluded": "已结课",
  "picker.empty": "还没有课程。先跑一次同步，把 Canvas 上的课程列表拉下来。",
  "picker.losing": "取消 {n} 门课会删掉它们的 {files} 条资源记录",

  // 设置改动之后后端报回来的结果
  "note.inline_images_removed": "已删除 {n} 个内联图片",
  "note.links_removed": "已删除 {n} 个外部链接",
  "note.announcement_links_removed": "已删除 {n} 个只出现在公告里的链接",
  "note.discussion_resources_removed": "已删除 {n} 个只出现在讨论区的资源",
  "note.local_copies_removed": "已删除 {n} 个本地文件副本",
  "note.saved": "设置已保存",
  "note.sync_started": "已开始同步所有课程",
  "note.interval_rescheduled": "下次自动同步已按新间隔重新安排",
  "note.courses_enabled": "新增同步 {n} 门课程，首次同步不会产生更新提醒",
  "note.courses_disabled": "已取消 {n} 门课程，删除了 {files} 个文件记录",

  // ---- 筛选 ----
  "filter.title": "筛选",
  "filter.button": "筛选",
  "filter.activeCount": "已选 {n} 条",
  "filter.show": "类别",
  "filter.media.file": "文件",
  "filter.media.link": "链接",
  "filter.dateRange": "日期范围",
  "filter.boards": "板块",
  "filter.containers": "来源",
  "filter.kinds": "类型",
  "filter.courses": "课程",
  "filter.clear": "清空",
  "filter.apply": "应用",
  "filter.emptyResult": "没有资源符合当前的筛选条件",

  // ---- 课程文件页 ----
  "files.refresh": "刷新页面",
  "files.refreshing": "刷新中…",
  "files.refreshTip": "重新整理并拉取这门课的最新数据",
  "files.lastSync": "上次同步 {when}",
  "files.lastSyncFailed": "上次同步失败",
  "files.tab.auto": "自动整理",
  "files.tab.manual": "我的分组",
  "files.dim.default": "默认",
  "files.dim.type": "按资源类型",
  "files.dim.manual": "我的分组",
  "files.allResources": "全部资源",
  "files.emptyTree": "这门课还没扫到资源。去「设置」跑一次同步。",
  "files.selectAll": "全选",
  "files.clearAll": "取消全选",
  "files.searchPlaceholder": "搜索名称、文件夹、页面…",
  // 容错和高级语法从输入框上看不出来，所以写在悬停提示里
  "files.searchHint":
    "大小写、标点、空格都不用对齐（ps1 = PS 1 = Problem_Set-1），拼错一个字母也能搜到。\n" +
    "搜索范围：文件名、文件夹路径、模块名、页面/公告/作业标题、链接地址。\n" +
    '进阶：“加引号”精确匹配，-减号 排除，folder: / page: / module: / name: 只搜某一处。',
  "files.sortByDate": "按日期",
  "files.sortByName": "按名称",
  "files.sortByRelevance": "按相关度",
  // 命中落在文件名以外时行上的那句说明
  "files.matchedIn": "命中",
  "match.folder": "文件夹",
  "match.page": "页面",
  "match.module": "模块",
  "match.announcement": "公告",
  "match.assignment": "作业",
  "match.quiz": "测验",
  "match.syllabus": "课程大纲",
  "match.discussion": "讨论区",
  "match.nav": "导航栏",
  "match.url": "链接地址",
  "files.orderAsc": "↑ 正序",
  "files.orderDesc": "↓ 倒序",
  "files.selectedCount": "选中 {n} 个",
  "files.star": "星标",
  "files.unstar": "取消星标",
  "files.addToGroup": "加入分组",
  "files.removeFromThisGroup": "移出本组",
  "files.download": "下载",
  "files.downloading": "下载中",
  "files.open": "打开",
  "files.newGroup": "+ 新建分组",
  "files.newGroupTitle": "新建分组",
  "files.renameGroupTitle": "重命名分组",
  "files.groupNamePlaceholder": "分组名称",
  "files.groupNameEmpty": "分组名不能为空",
  "files.saveFailed": "保存失败",
  "files.noGroupsTitle": "没有已创建的分组",
  "files.noGroupsHint": "立即创建新分组或添加星标以开始",
  "files.emptyList": "这里还没有资源",
  "files.noGroupsYet": "还没有分组",
  "files.group": "分组",
  "files.edit": "修改",
  "files.rename": "重命名",
  "files.deleteGroup": "删除分组",
  "files.removeFromGroup": "移出",
  "files.removeHint": "再点一次移出这个分组",
  "files.starred": "星标",
  "files.starToggleOn": "加星标",
  "files.morePlaces": "+{n} 处",
  "files.deletedBadge": "已从 Canvas 删除",
  "files.deletedShort": "已删除",
  "files.deletedTip": "这个文件在 Canvas 上已经没有了。因为你整理过它，记录保留下来",
  "files.linkTip": "外部链接，点开会跳出 TCourses",
  "files.fileTip": "文件，可以下载",

  // 确认框
  "confirm.deleteGroup": "删除分组「{name}」？",
  "confirm.deleteGroupBody": "分组内的文件不会被删除。",
  "confirm.deleteGroupAction": "删除分组",
  "confirm.removeFrom": "把{what}移出「{group}」？",
  "confirm.removeAction": "移出",
  "confirm.selectedFiles": "选中的 {n} 个资源",
  "confirm.thisFile": "「{name}」",
  "confirm.unstar": "取消 {n} 个资源的星标？",
  "confirm.unstarAction": "取消星标",
  "confirm.thatGroup": "该分组",

  // 批量下载的结果
  "bundle.done": "已下载 {n} 个文件",
  "bundle.partial": "已下载 {n} 个文件，{notes}（详见包内说明）",
  "bundle.links": "{n} 个链接未下载",
  "bundle.failed": "{n} 个下载失败",

  // ---- 文件详情 ----
  "detail.linkUrl": "链接地址",
  "detail.linkType": "链接类型",
  "detail.fileType": "文件类型",
  "detail.updatedAt": "更新时间",
  "detail.canvasFolder": "Canvas 文件夹",
  "detail.localCopy": "本地副本",
  "detail.downloaded": "已下载",
  "detail.notDownloaded": "未下载",
  "detail.myGroups": "我的分组",
  "detail.noGroups": "未加入任何分组",
  "detail.deletedNote": "这个文件在 Canvas 上已经没有了。因为你星标过或把它加进了分组，这条记录连同下面的来源信息一起保留了下来，但已经无法下载。",
  "detail.locations": "在 Canvas 上的位置",
  "detail.locationCount": "共 {n} 处",
  "detail.openInCanvas": "在 Canvas 打开 ↗",
  "detail.noSources": "没有来源记录",

  // ---- 分页 ----
  // ---- 日历与待办 ----
  "planner.source.all": "全部来源",
  "planner.source.canvas": "Canvas",
  "planner.source.user": "我创建的",
  "planner.state.all": "全部状态",
  "planner.state.incomplete": "未完成",
  "planner.state.completed": "已完成",
  "planner.state.overdue": "逾期",
  "planner.flag.all": "全部标记",
  "planner.flag.important": "重点",
  "planner.flag.hidden": "已隐藏",
  "nav.settings": "设置",
  "settings.title": "设置",
  "settings.general": "通用",
  "settings.sectionTasks": "Planner",
  "settings.colorsTitle": "课程颜色",
  "calendar.day": "日",
  "planner.bucket.past": "已过",
  "planner.courses": "课程",
  "planner.kinds": "类别",
  "planner.nCourses": "{n} 门课程",
  "planner.nKinds": "{n} 个类别",
  "planner.selectAll": "全选",
  "planner.noCourse": "无课程",
  "planner.noDate": "无日期",
  "planner.restore": "恢复",
  "planner.addLocation": "添加地点",
  "planner.addNote": "添加备注",
  "planner.important": "重点",
  "planner.status.deleted": "已从 Canvas 删除",
  "tasks.range.all": "全部时间",
  "tasks.range.today": "今天",
  "tasks.range.week": "本周",
  "tasks.range.next7": "未来 7 天",
  "tasks.range.next30": "未来 30 天",
  "tasks.range.past30": "过去 30 天",
  "tasks.sort.date": "按日期",
  "tasks.sort.course": "按课程",
  "tasks.sort.kind": "按类别",
  "tasks.overdueCount": "{n} 项逾期",
  "calendar.title": "日历",
  "calendar.month": "月",
  "calendar.week": "周",
  "calendar.today": "今天",
  "tasks.title": "任务",
  "tasks.empty": "暂无任务",
  "planner.allCourses": "全部课程",
  "planner.kind.assignment": "作业",
  "planner.kind.quiz": "测验",
  "planner.kind.discussion": "讨论",
  "planner.kind.event": "事件",
  "planner.kind.announcement": "公告",
  "planner.kind.page": "页面",
  "planner.kind.note": "待办",
  "planner.kind.peer_review": "互评",
  "planner.kind.other": "其他",
  "planner.kind.task": "我的任务",
  "planner.lists": "清单",
  "planner.nLists": "{n} 个清单",
  "tasks.sort.list": "按清单",
  "task.new": "新建",
  "task.quickAdd": "添加任务",
  "task.newTitle": "任务名称",
  "task.create": "创建",
  "task.delete": "删除",
  "task.clearDate": "清除日期",
  "task.addStep": "添加步骤",
  "task.noCourse": "不关联课程",
  "task.noList": "不放入清单",
  "task.repeat.none": "不重复",
  "task.repeat.daily": "每天",
  "task.repeat.weekdays": "工作日",
  "task.repeat.weekly": "每周",
  "task.repeat.biweekly": "每两周",
  "task.repeat.monthly": "每月",
  "task.repeat.yearly": "每年",
  "settings.lists": "清单",
  "settings.newList": "新建清单",
  "settings.deleteList": "删除清单「{name}」？",
  "planner.bucket.today": "今天",
  "planner.bucket.tomorrow": "明天",
  "planner.bucket.week": "本周",
  "planner.bucket.later": "以后",
  "planner.bucket.undated": "无日期",
  "planner.status.submitted": "已提交",
  "planner.status.graded": "已评分",
  "planner.status.missing": "缺交",
  "planner.status.late": "迟交",
  "planner.status.excused": "免交",
  "planner.status.feedback": "有反馈",
  "planner.allDay": "全天",
  "planner.dueItem": "作业截止",
  "planner.markDone": "标记完成",
  "planner.done": "已完成",
  "planner.followCanvas": "跟随 Canvas",
  "planner.hide": "隐藏",
  "planner.unhide": "取消隐藏",
  "planner.writebackFailed": "已保存，但没能同步到 Canvas",

  // ---- 全局资源页 ----
  "resources.title": "所有资源",
  "resources.tabHome": "主页",
  "resources.tabFind": "查找资源",
  "resources.latest": "最新",
  // 「更新」= 我还没看过的（按课程水位线判定）；今天 / 本周是时间窗口，
  // 和看过没看过无关 —— 三个口径回答的是三个不同的问题
  "resources.latestUnseen": "更新",
  "resources.latestToday": "今天",
  "resources.latestWeek": "本周",
  "resources.markAllSeen": "全部标记为已看",
  "resources.starred": "我的星标",
  "resources.viewAll": "全部",
  "resources.expand": "展开（还有 {n} 条）",
  "resources.allCourses": "全部课程",
  "resources.noCourses": "没有课程符合当前的筛选条件",
  // 空状态就一句话。三个口径共用同一句 —— 用户要的是"现在没有"，
  // 不是一段解释判定规则的说明
  "resources.emptyUpdates": "暂时没有更新",
  "resources.emptyStarred": "没有星标",
  "resources.emptyFind": "暂时没有资源",

  "page.range": "第 {from}–{to} 个，共 {total} 个",
  "page.sizePrefix": "每页",
  "page.sizeSuffix": "个",
  "page.prev": "上一页",
  "page.next": "下一页",

  // ---- 后端报回来的错误 ----
  "err.file_not_found": "文件不存在",
  "err.file_is_link": "「{name}」是一个外部链接，请直接打开它",
  "err.file_deleted": "「{name}」已从 Canvas 删除，下不到了",
  "err.canvas_not_configured": "Canvas 未配置，无法下载",
  "err.download_failed": "「{name}」下载失败，Canvas 取不到这个文件",
  "err.file_locked": "「{name}」在 Canvas 上还没开放，暂时下不了",
  "err.planner_item_not_found": "找不到这一条",
  "err.task_not_found": "找不到这个任务",
  "err.task_title_empty": "任务名称不能为空",
  "err.task_list_not_found": "清单不存在",
  "err.task_list_name_empty": "清单名称不能为空",
  "err.invalid_date": "日期格式不对",
  "err.invalid_color": "颜色格式不对",
  "err.no_files_selected": "没有选中任何文件",
  "err.bundle_limit": "一次最多打包 {limit} 个文件",
  "err.files_not_in_course": "文件不存在或不属于这门课",
  "err.group_name_empty": "分组名不能为空",
  "err.group_name_taken": "已经有一个叫「{name}」的分组了",
  "err.group_not_found": "分组不存在",
  "err.course_not_found": "课程不存在",
  "err.event_not_found": "事件不存在",
  "err.sync_running": "正在同步所有课程，请等它跑完",
  "err.course_sync_running": "有课程正在单独同步，请等它跑完",
  "err.settings_locked": "同步进行中，请等它跑完再改设置",
  "err.demo_readonly": "演示模式下不可用",
  "app.demo": "演示",
  "err.invalid_canvas_url": "Canvas 地址格式不对",
  "err.canvas_token_required": "请填写访问令牌",
  "err.canvas_auth_failed": "Canvas 拒绝了这个令牌",
  "err.canvas_unreachable": "连不上这个地址，或者它不是 Canvas",
  "err.unknown_courses": "课程不存在：{ids}",
  "err.bundle_all_links": "选中的 {n} 项都是外部链接，没有可下载的文件",
  "err.bundle_all_failed": "选中的文件一个都没下载成功：{detail}",
  "err.download": "下载失败",
  "err.bundle": "打包失败",
  "err.generic": "操作失败",
} as const;

type Key = keyof typeof zh;

const en: Record<Key, string> = {
  "common.cancel": "Cancel",
  "common.confirm": "Confirm",
  "common.save": "Save",
  "common.saving": "Saving…",
  "common.loading": "Loading…",
  "common.close": "Close",
  "common.back": "Back",
  "common.processing": "Working…",
  "common.details": "Details",
  "common.collapse": "Collapse",
  "common.gotIt": "Got it",
  "common.on": "Turn on ",
  "common.off": "Turn off ",

  "nav.updates": "Updates",
  "nav.resources": "My Resources",
  "nav.calendar": "Calendar",
  "nav.tasks": "Tasks",
  "nav.courses": "Courses",
  "nav.manageHint": "Course Manager: choose which courses show in the sidebar, and their order",
  "nav.noCourses":
    "No courses in the sidebar. Check Course Manager in case they are all hidden, or pick courses to sync in Settings.",

  "course.concluded": "Concluded",
  "course.resourceCount": "{n} resources",
  "course.untitled": "Course",

  "manager.title": "Course Manager",
  "manager.panel": "Sidebar Courses",
  "manager.visible": "{n} / {total} shown",
  "manager.empty": "No courses are being synced. Pick some in Settings → Choose courses.",
  "manager.drag": "Drag to reorder",
  "manager.showInSidebar": "Show in sidebar",
  "manager.hidden": "Hidden",
  "manager.sortAlpha": "Sort A–Z",
  "manager.revert": "Discard changes",
  "manager.saved": "Sidebar updated",

  "updates.title": "Updates",
  "updates.markAllRead": "Mark all read",
  "updates.empty":
    "Nothing yet. The first sync only fills the library; after that, changes on Canvas show up here.",
  "updates.unread": "Unread · click to mark",

  "event.new_assignment": "New assignment",
  "event.updated_assignment": "Assignment changed",
  "event.new_announcement": "New announcement",
  "event.new_message": "New message",
  "event.updated_page": "Page updated",
  "event.new_file": "New file",
  "event.updated_file": "File updated",
  "event.new_link": "New link",
  "event.updated_link": "Link changed",
  "event.due_soon": "Due soon",
  "event.sync_error": "Sync problem",

  "event.summary.new_assignment": "New assignment, due {due}",
  "event.summary.new_announcement": "New announcement",
  "event.summary.new_message": "New message from {author}",
  "event.summary.new_page": "Page updated",
  "event.summary.new_file": "New file",
  "event.summary.updated_file": "File updated",
  "event.summary.new_link": "New link",
  "event.summary.updated_link": "Link changed",
  "event.summary.updated": "Changed: {fields}",
  "event.field.title": "title",
  "event.field.due_at": "due date",
  "event.field.body_html": "body",
  "event.field.content": "content",
  "event.noDue": "no due date",
  "event.someone": "your instructor",

  "sync.preparing": "Getting ready…",
  "sync.running": "Syncing…",
  "sync.runningCourses": "Syncing: {courses}",
  "sync.courseProgress": "{done} / {total} courses",
  "sync.interval.off": "Auto sync off",
  "sync.interval.minutes": "Every {n} min",
  "sync.interval.hours": "Every {n} h",
  "sync.interval.days": "Every {n} d",
  "sync.next": "Next {when}",
  "sync.notScheduled": "Not scheduled",
  "sync.startingSoon": "Starting now",
  "sync.inMinutes": "in ~{n} min",
  "sync.underMinute": "in under a minute",


  "settings.language": "Language",
  "settings.autoTimezone": "Automatic time zone",
  "settings.autoTimezoneHint": "Current time zone: {tz}",
  "settings.timezone": "Time zone",
  "settings.timezoneSearch": "Search time zones…",
  "settings.timezoneNoMatch": "No matching time zone",

  "settings.notConfigured": "Not configured",
  "settings.canvasStep1": "In Canvas on the web, open Account → Settings",
  "settings.canvasStep2": "Scroll to Approved Integrations, click + New Access Token",
  "settings.canvasStep3": "Paste the token above and save",
  "settings.canvasUrl": "Canvas URL",
  "settings.canvasToken": "Access token",
  "settings.canvasUnchanged": "No changes",
  "settings.canvasConfirm": "Update Canvas connection?",
  "settings.canvasConfirmBody": "Syncs all courses as {name}.",
  "settings.canvasSwitchBody": "Different account ({name}). Current course data will be cleared.",
  "settings.canvasConfirmAction": "Save and sync",
  "settings.canvasSwitchAction": "Clear and sync",

  "settings.sectionSync": "Sync",
  "settings.sectionFiles": "Files",
  "settings.syncTasks": "Tasks",
  "settings.syncEvents": "Calendar events",
  "settings.itemCount": "{n} items",
  "settings.plannerWriteback": "Sync completion to Canvas",
  "settings.courseColors": "Course colors",
  "settings.resetColor": "Reset",
  "settings.syncNow": "Sync all courses now",
  "settings.syncAll": "Sync now",
  "settings.syncing": "Syncing…",
  "settings.waitingCourse": "Waiting for a course sync to finish…",
  "settings.intervalLabel": "Auto sync interval",
  "settings.intervalOff": "Off",
  "settings.pickCourses": "Courses to sync",
  "settings.pickCoursesButton": "Choose courses",
  "settings.pickedCount": "{n} / {total} selected",
  "settings.savedShort": "Settings saved",

  "toggle.auto_download_files": "Download files to this computer",
  "toggle.sync_inline_images": "Sync inline images",
  "toggle.sync_links": "Sync links",
  "toggle.sync_announcement_links": "Sync links in announcements",
  "toggle.sync_discussions": "Sync discussions",
  "toggle.ask": "{action}{name}?",

  "settings.courseStatus": "Course Sync Status",
  "settings.noCourses": "No courses yet — run a sync first",
  "settings.manualSync": "Sync now",
  "settings.syncingShort": "Syncing",
  "settings.noSyncRecord": "This course has not been synced yet",
  "settings.unsupportedHint":
    "We cannot scan this course's {boards} yet, so anything in there will not appear in the list",
  "settings.syncingAllTip": "All courses are syncing — wait for it to finish",
  "settings.courseSyncTip": "Syncs only this course; the automatic schedule is untouched",

  "status.ok": "Synced",
  "status.failed": "Sync failed",
  "status.syncing": "Syncing",
  "status.never": "Never synced",

  "board.scanned": "Scanned",
  "board.unavailable": "Not offered by the school",
  "board.disabled": "Turned off in settings",
  "board.unsupported": "Not supported yet",

  "picker.title": "Courses to sync",
  "picker.pickedCount": "{n} selected",
  "picker.active": "Active",
  "picker.concluded": "Concluded",
  "picker.empty": "No courses yet. Run a sync first to pull your course list from Canvas.",
  "picker.losing": "Unchecking {n} courses will delete their {files} resource records",

  "note.inline_images_removed": "Removed {n} inline images",
  "note.links_removed": "Removed {n} external links",
  "note.announcement_links_removed": "Removed {n} links that only appeared in announcements",
  "note.discussion_resources_removed": "Removed {n} resources that only appeared in discussions",
  "note.local_copies_removed": "Removed {n} local file copies",
  "note.saved": "Settings saved",
  "note.sync_started": "Started syncing all courses",
  "note.interval_rescheduled": "The next automatic sync was rescheduled",
  "note.courses_enabled": "Added {n} courses; their first sync will not raise update notices",
  "note.courses_disabled": "Removed {n} courses and deleted {files} file records",

  "filter.title": "Filter",
  "filter.button": "Filter",
  "filter.activeCount": "{n} active",
  "filter.show": "Category",
  "filter.media.file": "Files",
  "filter.media.link": "Links",
  "filter.dateRange": "Date range",
  "filter.boards": "Boards",
  "filter.containers": "Locations",
  "filter.kinds": "Types",
  "filter.courses": "Courses",
  "filter.clear": "Clear all",
  "filter.apply": "Apply",
  "filter.emptyResult": "No resources match the current filter",

  "files.refresh": "Refresh",
  "files.refreshing": "Refreshing…",
  "files.refreshTip": "Re-organize and pull the latest data for this course",
  "files.lastSync": "Last synced {when}",
  "files.lastSyncFailed": "Last sync failed",
  "files.tab.auto": "Organized",
  "files.tab.manual": "My groups",
  "files.dim.default": "Default",
  "files.dim.type": "By type",
  "files.dim.manual": "My groups",
  "files.allResources": "All resources",
  "files.emptyTree": "Nothing found in this course yet. Run a sync from Settings.",
  "files.selectAll": "Select all",
  "files.clearAll": "Clear",
  "files.searchPlaceholder": "Search names, folders, pages…",
  "files.searchHint":
    "Case, punctuation and spacing don't have to match (ps1 = PS 1 = Problem_Set-1), and one " +
    "typo is fine.\nSearches file names, folder paths, module names, page / announcement / " +
    "assignment titles and link URLs.\n" +
    'Advanced: "quotes" for exact, -minus to exclude, folder: / page: / module: / name: to ' +
    "search one place only.",
  "files.sortByDate": "By date",
  "files.sortByName": "By name",
  "files.sortByRelevance": "By relevance",
  "files.matchedIn": "matched in",
  "match.folder": "folder",
  "match.page": "page",
  "match.module": "module",
  "match.announcement": "announcement",
  "match.assignment": "assignment",
  "match.quiz": "quiz",
  "match.syllabus": "syllabus",
  "match.discussion": "discussion",
  "match.nav": "nav item",
  "match.url": "link URL",
  "files.orderAsc": "↑ Oldest",
  "files.orderDesc": "↓ Newest",
  "files.selectedCount": "{n} selected",
  "files.star": "Star",
  "files.unstar": "Unstar",
  "files.addToGroup": "Add to group",
  "files.removeFromThisGroup": "Remove from group",
  "files.download": "Download",
  "files.downloading": "Downloading",
  "files.open": "Open",
  "files.newGroup": "+ New group",
  "files.newGroupTitle": "New group",
  "files.renameGroupTitle": "Rename group",
  "files.groupNamePlaceholder": "Group name",
  "files.groupNameEmpty": "Group name cannot be empty",
  "files.saveFailed": "Could not save",
  "files.noGroupsTitle": "No groups yet",
  "files.noGroupsHint": "Create a group or star something to get started",
  "files.emptyList": "Nothing here yet",
  "files.noGroupsYet": "No groups yet",
  "files.group": "Group",
  "files.edit": "Edit",
  "files.rename": "Rename",
  "files.deleteGroup": "Delete group",
  "files.removeFromGroup": "Remove",
  "files.removeHint": "Click again to remove it from this group",
  "files.starred": "Starred",
  "files.starToggleOn": "Star",
  "files.morePlaces": "+{n} more",
  "files.deletedBadge": "Deleted on Canvas",
  "files.deletedShort": "Deleted",
  "files.deletedTip": "This file is gone from Canvas. The record stays because you organized it",
  "files.linkTip": "External link — opens outside TCourses",
  "files.fileTip": "File — can be downloaded",

  "confirm.deleteGroup": "Delete group “{name}”?",
  "confirm.deleteGroupBody": "The files inside are not deleted.",
  "confirm.deleteGroupAction": "Delete group",
  "confirm.removeFrom": "Remove {what} from “{group}”?",
  "confirm.removeAction": "Remove",
  "confirm.selectedFiles": "the {n} selected resources",
  "confirm.thisFile": "“{name}”",
  "confirm.unstar": "Unstar {n} resources?",
  "confirm.unstarAction": "Unstar",
  "confirm.thatGroup": "this group",

  "bundle.done": "Downloaded {n} files",
  "bundle.partial": "Downloaded {n} files, {notes} (see the note inside the zip)",
  "bundle.links": "{n} links not downloaded",
  "bundle.failed": "{n} failed",

  "detail.linkUrl": "Link",
  "detail.linkType": "Link type",
  "detail.fileType": "File type",
  "detail.updatedAt": "Updated",
  "detail.canvasFolder": "Canvas folder",
  "detail.localCopy": "Local copy",
  "detail.downloaded": "Downloaded",
  "detail.notDownloaded": "Not downloaded",
  "detail.myGroups": "My groups",
  "detail.noGroups": "Not in any group",
  "detail.deletedNote":
    "This file is gone from Canvas. Because you starred it or put it in a group, the record and its locations are kept — but it can no longer be downloaded.",
  "detail.locations": "Where it lives on Canvas",
  "detail.locationCount": "{n} places",
  "detail.openInCanvas": "Open in Canvas ↗",
  "detail.noSources": "No source records",

  // ---- Calendar & tasks ----
  "planner.source.all": "All sources",
  "planner.source.canvas": "Canvas",
  "planner.source.user": "Created by me",
  "planner.state.all": "All statuses",
  "planner.state.incomplete": "Incomplete",
  "planner.state.completed": "Completed",
  "planner.state.overdue": "Overdue",
  "planner.flag.all": "All flags",
  "planner.flag.important": "Important",
  "planner.flag.hidden": "Hidden",
  "nav.settings": "Settings",
  "settings.title": "Settings",
  "settings.general": "General",
  "settings.sectionTasks": "Planner",
  "settings.colorsTitle": "Course colors",
  "calendar.day": "Day",
  "planner.bucket.past": "Past",
  "planner.courses": "Courses",
  "planner.kinds": "Category",
  "planner.nCourses": "{n} courses",
  "planner.nKinds": "{n} categories",
  "planner.selectAll": "All",
  "planner.noCourse": "No course",
  "planner.noDate": "No date",
  "planner.restore": "Restore",
  "planner.addLocation": "Add location",
  "planner.addNote": "Add note",
  "planner.important": "Important",
  "planner.status.deleted": "Deleted on Canvas",
  "tasks.range.all": "Any time",
  "tasks.range.today": "Today",
  "tasks.range.week": "This week",
  "tasks.range.next7": "Next 7 days",
  "tasks.range.next30": "Next 30 days",
  "tasks.range.past30": "Past 30 days",
  "tasks.sort.date": "By date",
  "tasks.sort.course": "By course",
  "tasks.sort.kind": "By category",
  "tasks.overdueCount": "{n} overdue",
  "calendar.title": "Calendar",
  "calendar.month": "Month",
  "calendar.week": "Week",
  "calendar.today": "Today",
  "tasks.title": "Tasks",
  "tasks.empty": "No tasks",
  "planner.allCourses": "All courses",
  "planner.kind.assignment": "Assignment",
  "planner.kind.quiz": "Quiz",
  "planner.kind.discussion": "Discussion",
  "planner.kind.event": "Event",
  "planner.kind.announcement": "Announcement",
  "planner.kind.page": "Page",
  "planner.kind.note": "To Do",
  "planner.kind.peer_review": "Peer review",
  "planner.kind.other": "Other",
  "planner.kind.task": "My task",
  "planner.lists": "Lists",
  "planner.nLists": "{n} lists",
  "tasks.sort.list": "By list",
  "task.new": "New",
  "task.quickAdd": "Add a task",
  "task.newTitle": "Task name",
  "task.create": "Create",
  "task.delete": "Delete",
  "task.clearDate": "Clear date",
  "task.addStep": "Add step",
  "task.noCourse": "No course",
  "task.noList": "No list",
  "task.repeat.none": "Doesn't repeat",
  "task.repeat.daily": "Daily",
  "task.repeat.weekdays": "Weekdays",
  "task.repeat.weekly": "Weekly",
  "task.repeat.biweekly": "Every 2 weeks",
  "task.repeat.monthly": "Monthly",
  "task.repeat.yearly": "Yearly",
  "settings.lists": "Lists",
  "settings.newList": "New list",
  "settings.deleteList": "Delete list “{name}”?",
  "planner.bucket.today": "Today",
  "planner.bucket.tomorrow": "Tomorrow",
  "planner.bucket.week": "This week",
  "planner.bucket.later": "Later",
  "planner.bucket.undated": "No date",
  "planner.status.submitted": "Submitted",
  "planner.status.graded": "Graded",
  "planner.status.missing": "Missing",
  "planner.status.late": "Late",
  "planner.status.excused": "Excused",
  "planner.status.feedback": "Feedback",
  "planner.allDay": "All day",
  "planner.dueItem": "Assignment due",
  "planner.markDone": "Mark done",
  "planner.done": "Done",
  "planner.followCanvas": "Follow Canvas",
  "planner.hide": "Hide",
  "planner.unhide": "Unhide",
  "planner.writebackFailed": "Saved, but couldn't update Canvas",

  // ---- Global resources ----
  "resources.title": "My Resources",
  "resources.tabHome": "Home",
  "resources.tabFind": "Find",
  "resources.latest": "Latest",
  "resources.latestUnseen": "Updates",
  "resources.latestToday": "Today",
  "resources.latestWeek": "This week",
  "resources.markAllSeen": "Mark all as seen",
  "resources.starred": "Starred",
  "resources.viewAll": "All",
  "resources.expand": "Show all ({n} more)",
  "resources.allCourses": "All courses",
  "resources.noCourses": "No course matches the current filter",
  "resources.emptyUpdates": "No updates",
  "resources.emptyStarred": "No starred items",
  "resources.emptyFind": "No resources yet",

  "page.range": "{from}–{to} of {total}",
  "page.sizePrefix": "Per page",
  "page.sizeSuffix": "",
  "page.prev": "Previous",
  "page.next": "Next",


  "err.file_not_found": "File not found",
  "err.file_is_link": "“{name}” is an external link — open it directly",
  "err.file_deleted": "“{name}” was deleted on Canvas and can no longer be downloaded",
  "err.canvas_not_configured": "Canvas is not configured, so nothing can be downloaded",
  "err.download_failed": "“{name}” failed to download — Canvas would not serve the file",
  "err.file_locked": "“{name}” is still locked on Canvas and cannot be downloaded yet",
  "err.planner_item_not_found": "Item not found",
  "err.task_not_found": "Task not found",
  "err.task_title_empty": "Task name can't be empty",
  "err.task_list_not_found": "List not found",
  "err.task_list_name_empty": "List name can't be empty",
  "err.invalid_date": "Invalid date",
  "err.invalid_color": "Invalid color",
  "err.no_files_selected": "Nothing selected",
  "err.bundle_limit": "At most {limit} files per zip",
  "err.files_not_in_course": "Some files do not exist or belong to another course",
  "err.group_name_empty": "Group name cannot be empty",
  "err.group_name_taken": "There is already a group called “{name}”",
  "err.group_not_found": "Group not found",
  "err.course_not_found": "Course not found",
  "err.event_not_found": "Notification not found",
  "err.sync_running": "All courses are syncing — wait for it to finish",
  "err.course_sync_running": "A course is syncing on its own — wait for it to finish",
  "err.settings_locked": "A sync is running — wait for it to finish before changing settings",
  "err.demo_readonly": "Not available in demo",
  "app.demo": "Demo",
  "err.invalid_canvas_url": "That doesn't look like a Canvas URL",
  "err.canvas_token_required": "Enter an access token",
  "err.canvas_auth_failed": "Canvas rejected this token",
  "err.canvas_unreachable": "Can't reach this address, or it isn't Canvas",
  "err.unknown_courses": "Course not found: {ids}",
  "err.bundle_all_links": "All {n} selected items are external links — there is nothing to download",
  "err.bundle_all_failed": "None of the selected files could be downloaded: {detail}",
  "err.download": "Download failed",
  "err.bundle": "Could not build the zip",
  "err.generic": "Something went wrong",
};

const DICTS: Record<Lang, Record<Key, string>> = { zh, en };

export type Params = Record<string, string | number>;

function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole,
  );
}

// ---------------------------------------------------------------- 偏好

export interface LocalePrefs {
  lang: Lang;
  /** 时区跟随设备。关掉才用下面那个 */
  autoTimezone: boolean;
  timezone: string;
}

const STORAGE_KEY = "tcourses:locale";

function loadPrefs(): LocalePrefs {
  const fallback: LocalePrefs = { lang: "en", autoTimezone: true, timezone: deviceTimeZone() };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<LocalePrefs>;
    return {
      lang: saved.lang === "zh" ? "zh" : "en",
      autoTimezone: saved.autoTimezone !== false,
      timezone: saved.timezone || fallback.timezone,
    };
  } catch {
    // 隐私模式、存储被禁用，或者存进去的东西被改坏了
    return fallback;
  }
}

function savePrefs(prefs: LocalePrefs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // 存不下就算了，只是下次回到默认值
  }
}

// ---------------------------------------------------------------- 上下文

export interface I18n extends LocalePrefs {
  /** 实际生效的时区：自动就是设备的，手动就是选中的那个 */
  zone: string;
  setLang: (lang: Lang) => void;
  setAutoTimezone: (auto: boolean) => void;
  setTimezone: (tz: string) => void;
  t: (key: Key, params?: Params) => string;
  /** 词典里没有就返回原样 —— 后端传回来的老数据（写死的中文）不至于变成空白 */
  tOr: (key: string, fallback: string, params?: Params) => string;
  formatDate: (value: string | null | undefined) => string;
  dateParts: (value: string | null | undefined) => { date: string; time: string };
  /** 0 / 分钟 / 小时 / 天 —— 说人话的同步间隔 */
  formatInterval: (minutes: number) => string;
  /** 未来时间：近的说"约 x 分钟后"，远的直接给时刻 */
  formatRelativeFuture: (value: string | null | undefined) => string;
}

const Ctx = createContext<I18n | null>(null);

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [prefs, setPrefs] = useState<LocalePrefs>(loadPrefs);

  useEffect(() => {
    document.documentElement.lang = prefs.lang === "zh" ? "zh-CN" : "en";
  }, [prefs.lang]);

  const update = useCallback((patch: Partial<LocalePrefs>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch };
      savePrefs(next);
      return next;
    });
  }, []);

  const value = useMemo<I18n>(() => {
    // 自动模式下每次都现问设备，而不是用存下来的那个值 —— 用户可能换了地方
    const zone = prefs.autoTimezone ? deviceTimeZone() : prefs.timezone;
    const fmt: Fmt = { lang: prefs.lang, timeZone: zone };
    const dict = DICTS[prefs.lang];
    const t = (key: Key, params?: Params) => interpolate(dict[key], params);

    return {
      ...prefs,
      zone,
      setLang: (lang) => update({ lang }),
      setAutoTimezone: (autoTimezone) =>
        update(autoTimezone ? { autoTimezone, timezone: deviceTimeZone() } : { autoTimezone }),
      setTimezone: (timezone) => update({ timezone }),
      t,
      tOr: (key, fallback, params) =>
        key in dict ? interpolate(dict[key as Key], params) : fallback,
      formatDate: (value) => rawFormatDate(value, fmt),
      dateParts: (value) => rawDateParts(value, fmt),
      formatInterval: (minutes) => {
        if (minutes === 0) return t("sync.interval.off");
        if (minutes < 60) return t("sync.interval.minutes", { n: minutes });
        if (minutes < 1440) return t("sync.interval.hours", { n: minutes / 60 });
        return t("sync.interval.days", { n: minutes / 1440 });
      },
      formatRelativeFuture: (value) => {
        if (!value) return t("sync.notScheduled");
        const diffMs = new Date(value).getTime() - Date.now();
        if (diffMs <= 0) return t("sync.startingSoon");
        const minutes = Math.round(diffMs / 60_000);
        if (minutes < 1) return t("sync.underMinute");
        if (minutes < 60) return t("sync.inMinutes", { n: minutes });
        return rawFormatDate(value, fmt);
      },
    };
  }, [prefs, update]);

  // key 挂在这里：换语言或换时区时整棵树重挂一次。
  // 光靠 context 更新是不够的 —— 只有订阅了 context 的组件才会重渲染，
  // 而"所有时间立刻跟着变"要求的是**全部**都变。
  return (
    <Ctx.Provider value={value}>
      <div key={`${value.lang}-${value.zone}`} className="contents">
        {children}
      </div>
    </Ctx.Provider>
  );
}

export function useI18n(): I18n {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n 必须在 LocaleProvider 里用");
  return ctx;
}
