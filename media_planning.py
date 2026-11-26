"""Identify concrete poster/video deliverables before choosing an executor."""

import re


# Match an action and its object within one instruction. Mentions of an asset in
# a path, dependency, provider brief, or UI specification do not declare output.
_ACTION = re.compile(
    r"生成(?!器|工具|功能)|制作|绘制|设计|做出|做|创作|拍摄|产出|输出|交付|提供|创建|"
    r"\b(?:generate|create|make|produce|design|draw|deliver|output|provide)\b", re.I)
_ASSET = re.compile(
    r"(?P<video>宣传片|预告片|短片|成片|视频|\b(?:videos?|films?|trailers?|mp4)\b)|"
    r"(?P<image>海报|宣传图|招贴画|\b(?:posters?|flyers?)\b)", re.I)
_OTHER_OBJECT = re.compile(
    r"""["'”’」]?\s*的?\s*(?:播放|生成器|生成工具|生成流程|处理|系统|平台|网站|组件|接口|功能|按钮|页面|库|插件|应用|"""
    r"预览|下载接口|上传|导出工具|编辑器|验收|审核|测试|脚本|分镜|大纲|文案|提示词|字幕|素材|规范|模板|"
    r"需求|方案|教程|清单|封面|风格|参考)|"
    r"\s+(?:player|generation|generator|editor|editing|preview|upload|exporter|system|platform|service|tool|code|app|component|"
    r"API|button|page|plugin|script|storyboard|brief|copy|prompt|captions?|subtitles?|assets?|"
    r"specification|template|checklist|style|reference)\b", re.I)
_INDIRECT = re.compile(
    r"脚本|分镜|文案|提示词|模板|工具|组件|接口|功能|系统|框架|平台|页面|播放器|视频|海报|参考|用于|"
    r"\b(?:script|storyboard|copy|prompt|template|tool|system|framework|platform|website|"
    r"component|API|player|poster|video|reference|for|using|based on)\b", re.I)
_NON_ACTION = re.compile(
    r"(?:不(?:得|要|用|会|需(?:要)?)?|禁止|勿|无需|避免|不负责|不涉及|"
    r"支持|用于|用来|用以|能否|是否|能(?:够)?|检查|验证|测试)(?:直接|正确|实际|真正|自动|单独|另行)*\s*$|"
    r"\b(?:do not|don't|must not|never|without|can|allows? (?:users? )?to|feature to|ability to|"
    r"tool to|how to)(?:\s+\w+ly){0,2}\s*$", re.I)
_USER_ACTION = re.compile(r"(?:用户|用户端|按钮|系统|接口).{0,14}(?:点击|触发|自动|支持).{0,5}$")
_JOIN = re.compile(r"^\s*(?:和|及|与|以及|并|、|and\b|&|\+)\s*", re.I)
_REFERENCE = re.compile(r"(?:风格)?[与和].{0,24}?(?:海报|视频)(?:的?风格|视觉)?(?:保持)?一致的?")
_CONTEXT_TASK = re.compile(
    r"开发|实现|修复|重构|编写|测试|验收|核验|核对|检查|审核|评审|归档|转码|"
    r"\b(?:implement|develop|fix|refactor|test|verify|validate|review|inspect|archive|transcode)\b", re.I)
_FINAL_ASSET = re.compile(
    r"(?:实际|最终|成品|可直接|可下载|可播放|可打印)(?:的|可直接|可播放|可下载|竖版|宣传|活动|短|成品)*\s*$|"
    r"\b(?:finished|final|downloadable|playable|print-ready)\s+(?:promotional\s+)?$", re.I)
_CLAUSE = re.compile(r"[，,。；;：:\n.!?！？]")


def _requested_assets(clause):
    """Recognise direct creative outputs (including lists like 'a poster and a video')."""
    clause = _REFERENCE.sub("", clause)
    actions = list(_ACTION.finditer(clause))
    for index, action in enumerate(actions):
        prefix = clause[max(0, action.start() - 100):action.start()]
        if _NON_ACTION.search(prefix) or _USER_ACTION.search(prefix):
            continue
        end = actions[index + 1].start() if index + 1 < len(actions) else len(clause)
        phrase = clause[action.end():end]
        kinds = []
        previous = 0
        for asset in _ASSET.finditer(phrase):
            modifier = phrase[previous:asset.start()]
            if kinds:
                joined = _JOIN.match(modifier)
                if not joined:
                    break
                modifier = modifier[joined.end():]
            if len(modifier) > 60 or _INDIRECT.search(modifier):
                break
            if _OTHER_OBJECT.match(clause, action.end() + asset.end()):
                # In "produce poster and video templates" the shared head is templates.
                kinds.clear()
                break
            kinds.append(asset.lastgroup)
            previous = asset.end()
        yield from kinds


def required_media_types(task):
    """Return declared/inferred deliverables independently of their registered route."""
    declared = task.get("deliverable_type")
    kinds = {declared} if declared in ("video", "image") else set()
    prompt = task.get("prompt", "")
    for clause in _CLAUSE.split(prompt):
        kinds.update(_requested_assets(clause))
    # QA and software specifications often name the finished asset under test.
    # An explicit production instruction in their own prompt still counts above.
    if not task.get("review") and not _CONTEXT_TASK.search(prompt):
        for criterion in task.get("acceptance", []):
            for clause in _CLAUSE.split(criterion):
                kinds.update(_requested_assets(clause))
                for asset in _ASSET.finditer(clause):
                    if (_FINAL_ASSET.search(clause[max(0, asset.start() - 60):asset.start()])
                            and not _OTHER_OBJECT.match(clause, asset.end())):
                        kinds.add(asset.lastgroup)
    return kinds


def media_plan_issue(task):
    """Describe the correction for the LD, leaving unrelated planning rules alone."""
    task_id = task.get("id", "?")
    if (task.get("media") or {}).get("type") == "video":
        return f"{task_id}: 请 LD 重新规划，视频使用 video_prompt，移除 media 并清空 tools 和 write_paths。"
    if task.get("deliverable_type") not in (None, "video", "image"):
        return f"{task_id}: 请 LD 重新规划，deliverable_type 仅接受 video 或 image。"
    has_other_work = (task.get("tools") or task.get("write_paths") or task.get("review") or task.get("effect")
                      or task.get("impact") in ("architecture", "feature_design"))
    if task.get("media") is not None and (task.get("video_prompt") is not None or has_other_work):
        return (f"{task_id}: 请 LD 为 media 安排独立生成任务 (dedicated task)，海报用 media.type=image，"
                "视频单列 video_prompt。tools 和 write_paths 留空，验收、设计方案和外部操作另派。")
    if task.get("video_prompt") is not None and has_other_work:
        return (f"{task_id}: 请 LD 单列 video_prompt 生成任务 (task without tools)，tools 和 write_paths "
                "留空，验收、设计方案和外部操作另派。")
    identified = required_media_types(task)
    if not identified:
        return None
    routes = {"video": "video_prompt", "image": "media.type=image 和 media.prompt"}
    if len(identified) > 1:
        return (f"{task_id}: 请 LD 将海报与视频生成拆成独立任务，分别填写 {routes['image']}、"
                "video_prompt，两项任务的 tools 和 write_paths 均留空。")
    expected = next(iter(identified))
    media = task.get("media") or {}
    registered = "video" if task.get("video_prompt") else media.get("type")
    if registered != expected:
        return (f"{task_id}: 请 LD 重新规划，将{'视频' if expected == 'video' else '海报'}生成登记为 "
                f"{routes[expected]}，tools 和 write_paths 留空。")
    return None
