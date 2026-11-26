"""Identify concrete poster/video deliverables before choosing an executor."""

import re

from media_brief import poster_plan_issue


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


# These checks compare an explicit output specification with the public brief.
# Shot timings and the dimensions of assets shown inside a shot are not output settings.
_VIDEO_PROSE_SPLIT = re.compile(r"[。；;，,\n!?！？]|\.(?=\s|$)")
_SHOT_DETAIL = re.compile(
    r"分镜|镜头|片头|片尾|开场|结尾|画中画|海报|倒计时|"
    r"\b(?:shot|scene|intro|outro|poster|countdown|overlay)\b", re.I)
_EN_SECONDS = {word: i for i, word in enumerate((
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty").split(), 1)}
_DURATION = re.compile(
    r"(?<![\d.])(?P<value>\d{1,4}(?:\.\d+)?|(?:[一二两三四五六七八九]?十[一二两三四五六七八九]?|[一二两三四五六七八九])|\b(?:" + "|".join(_EN_SECONDS) +
    r"))\s*[- ]?\s*(?:秒钟?|seconds?\b|secs?\b|s\b)", re.I)
_CLIP_AFTER = re.compile(
    r"^\s*(?:的|左右|正式|活动|宣传|竖屏|横屏|\d{1,2}[:：]\d{1,2}|promo(?:tional)?|vertical|short|[- ]){0,8}\s*"
    r"(?:视频|短片|影片|成片|宣传片|video|film|trailer)", re.I)
_DURATION_LABEL = re.compile(
    r"(?:视频|成片|短片|全片|总时长|duration|length|video|film)"
    r"(?:时长|长|为|约|大约|[:：]|\s|of |about |approximately )*$", re.I)
_RATIO = re.compile(r"(?<![\d:：])([1-9]\d?)\s*[:：]\s*([1-9]\d?)(?![\d:：])")
_RATIO_SHAPE = re.compile(
    r"竖屏|竖版|纵向画幅|横屏|横版|横向画幅|方形画幅|"
    r"\b(?:portrait|vertical|landscape|horizontal|square)[ -](?:format|orientation|video|film)\b", re.I)
_COMMON_RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9", "21:9", "2:3", "3:2"}
_APPROVAL = re.compile(
    r"(?:未经|未获).{0,18}(?:确认|批准|审批).{0,10}(?:不得|不应|勿|不能).{0,6}生成"
    r"|(?:取得|获得|等待|征得|须经|需经|必须经).{0,12}(?:生成|视频参数)确认"
    r"|(?:确认|批准|审批)(?:通过)?后(?:方可|才能|才可|才|再)?(?:提交|开始|正式|调用|发起)*生成"
    r"|(?:视频生成|生成视频|正式生成|本次生成)(?:前)?(?:需要|需|须|必须|要)(?:再|另行|单独|先|获得|经过|取得|用户|人工|的|明确)*(?:确认|审批|批准)"
    r"|(?:生成|提交视频)前.{0,28}(?:须|需|必须|等待|取得|完成).{0,18}(?:用户|人工|宿主).{0,12}(?:确认|审批|批准)"
    r"|\b(?:require[sd]?|wait for|obtain|await)\s+(?:explicit |separate |user |human |generation )*"
    r"(?:approval|confirmation)\s+(?:before|to) (?:video |paid )?(?:generation|submission|generate|submit)"
    r"|\b(?:do not|never) (?:generate|submit).{0,25}(?:approval|confirmation)\b", re.I)
_DRAFT_VIDEO = re.compile(
    r"(?:生成前|提交前|生成之前|资料齐全后|信息确认后).{0,16}(?:补齐|补充|更新|替换|填写).{0,14}"
    r"(?:信息|报名方式|参数|提示词|文案)|(?:update|replace|fill in).{0,40}(?:before generating|before generation)", re.I)


def _video_texts(task):
    return (task.get("prompt", ""), task.get("video_prompt", ""), *task.get("acceptance", []))


def video_confirmation_requested(task):
    return any(_APPROVAL.search(text) for text in _video_texts(task) if isinstance(text, str))


def _stated_durations(clause):
    for timing in _DURATION.finditer(clause):
        before, after = clause[:timing.start()], clause[timing.end():]
        if (_CLIP_AFTER.match(after) or _DURATION_LABEL.search(before)
                or (not _SHOT_DETAIL.search(clause) and re.fullmatch(r"\s*(?:约|大约|约莫|大概|~|≈)?\s*", before))):
            value = timing["value"].lower()
            if value.isascii():
                yield _EN_SECONDS[value] if value in _EN_SECONDS else float(value)
            else:
                # Only the handful of Chinese duration words accepted above enter here.
                digits = "零一二三四五六七八九"
                head, separator, tail = value.replace("两", "二").partition("十")
                if separator:
                    yield (digits.index(head) if head else 1) * 10 + (digits.index(tail) if tail else 0)
                else:
                    yield digits.index(head)


def video_plan_issue(task):
    settings = task.get("video_settings", {})
    if "video_settings" in task:
        if not task.get("video_prompt"):
            return "video_settings 只能用于独立的 video_prompt 生成任务"
        if not isinstance(settings, dict) or not settings or settings.keys() - {"duration", "ratio", "resolution"}:
            return "video_settings 需要非空对象，仅支持 duration、ratio、resolution"
        if "duration" in settings and (type(settings["duration"]) is not int or settings["duration"] <= 0):
            return "video_settings.duration 需要正整数秒"
        if "ratio" in settings and not re.fullmatch(r"[1-9]\d?:[1-9]\d?", str(settings["ratio"])):
            return "video_settings.ratio 需要宽:高，例如 9:16"
        if "resolution" in settings and (not isinstance(settings["resolution"], str)
                                          or not re.fullmatch(r"[A-Za-z0-9_.-]{1,24}", settings["resolution"])):
            return "video_settings.resolution 需要有效的分辨率，例如 768p"
    if not task.get("video_prompt"):
        return None

    issues = []
    if _DRAFT_VIDEO.search(task["video_prompt"]):
        issues.append("将 video_prompt 中待补齐的内容替换为已确认的最终创作提示")
    ratio = settings.get("ratio")
    for text in _video_texts(task):
        for clause in _VIDEO_PROSE_SPLIT.split(text):
            shot = _SHOT_DETAIL.search(clause)
            for seconds in _stated_durations(clause):
                if settings.get("duration") != seconds:
                    issues.append(f"在 video_settings.duration 登记成片时长 {seconds:g} 秒")
            if shot and not re.search(r"成片画幅|视频画幅|成片比例|output aspect ratio", clause, re.I):
                continue
            for match in _RATIO.finditer(clause):
                # A schedule or scoreboard can also contain numbers separated by a colon.
                expected = ":".join(match.groups())
                if (expected not in _COMMON_RATIOS and not _RATIO_SHAPE.search(clause)
                        and not re.search(r"画幅|宽高比|比例|aspect|ratio|format", clause, re.I)):
                    continue
                if ratio != expected:
                    issues.append(f"在 video_settings.ratio 登记画幅 {expected}")
            for shape in _RATIO_SHAPE.finditer(clause):
                word = shape.group().lower()
                vertical = word.startswith(("竖", "纵", "portrait", "vertical"))
                square = word.startswith(("方", "square"))
                width, height = (map(int, ratio.split(":")) if ratio else (0, 0))
                if not ratio or (width == height) != square or (width < height) != vertical:
                    issues.append(f"在 video_settings.ratio 登记与 {shape.group()} 一致的画幅")
    if video_confirmation_requested(task) and task.get("impact") != "costly_change":
        issues.append("本次生成须单独确认，请登记 impact=costly_change")
    return "；".join(dict.fromkeys(issues)) or None


def media_plan_issue(task):
    """Describe the correction for the LD, leaving unrelated planning rules alone."""
    task_id = task.get("id", "?")
    if issue := poster_plan_issue(task):
        return issue
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
    routes = {"video": "video_prompt", "image": "media.type=image 和 media.prompt"}
    if len(identified) > 1:
        return (f"{task_id}: 请 LD 将海报与视频生成拆成独立任务，分别填写 {routes['image']}、"
                "video_prompt，两项任务的 tools 和 write_paths 均留空。")
    if issue := video_plan_issue(task):
        return f"{task_id}: 请 LD 重新规划，{issue}。"
    if not identified:
        return None
    expected = next(iter(identified))
    media = task.get("media") or {}
    registered = "video" if task.get("video_prompt") else media.get("type")
    if registered != expected:
        return (f"{task_id}: 请 LD 重新规划，将{'视频' if expected == 'video' else '海报'}生成登记为 "
                f"{routes[expected]}，tools 和 write_paths 留空。")
    return None
