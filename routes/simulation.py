"""بيئة محاكاة الهجمات: سيناريوهات تعليمية بمقاطع من المولدات الخوارزمية فقط."""
import io
import logging
from functools import lru_cache

from flask import Blueprint, abort, g, jsonify, render_template, request, send_file

import database as db
import security
from engine import AudioError, ModelUnavailable, run_analysis
from engine.audio_io import encode_wav, preprocess, read_wav
from engine.spectrogram import compute_spectrogram
from engine.synth import generate

bp = Blueprint("simulation", __name__)
logger = logging.getLogger("voxshield")

SAMPLE_DURATION = 4.0
SAMPLE_SEEDS = {"live": 910000, "replay": 920000, "synthetic": 930000}
SAMPLE_TITLES = {
    "live": "عينة صوت حي",
    "replay": "عينة معاد تشغيلها",
    "synthetic": "عينة مولدة اصطناعياً",
}

SCENARIOS = {
    "replay-attack": {
        "index": 1,
        "title": "هجوم إعادة التشغيل",
        "summary": "يعرض هذا السيناريو كيف يُستخدم تسجيل حقيقي لصوت الضحية عبر إعادة تشغيله أمام نظام يعتمد على الصوت، وما الأثر الذي يتركه المكبر والغرفة على الإشارة.",
        "samples": ["live", "replay"],
        "steps": [
            {"title": "الحصول على تسجيل للضحية", "body": "يحصل المهاجم على تسجيل صوتي حقيقي للضحية من مكالمة سابقة أو من مقطع منشور على الإنترنت.", "notes": ["التسجيل في هذه المرحلة صوت بشري حقيقي بكامل خصائصه.", "كلما كان التسجيل أنقى زادت فرصة نجاح الهجوم."]},
            {"title": "التشغيل أمام الميكروفون", "body": "يشغّل المهاجم التسجيل عبر مكبر صوت قرب ميكروفون الجهة المستهدفة، كهاتف أو نظام تحقق صوتي.", "notes": ["المكبرات الصغيرة لا تنقل الترددات المنخفضة والعالية جداً بكفاءة.", "الإشارة تمر الآن بسلسلة ثانية من الأجهزة لم تكن في التسجيل الأصلي."]},
            {"title": "تغير خصائص الإشارة", "body": "تتغير خصائص الإشارة بعد مرورها بالمكبر والغرفة، فيضيق نطاقها الترددي ويظهر صدى الغرفة وأرضية ضجيج إضافية.", "notes": ["قارن المخطط الطيفي للعينتين: النطاق فوق 5500 هرتز يكاد يختفي في العينة المعاد تشغيلها.", "يظهر ذيل الطاقة بعد نهاية المقاطع الصوتية بسبب الصدى."]},
            {"title": "استقبال نظام التحقق للصوت", "body": "يستقبل نظام التحقق الصوت ويقارنه ببصمة المستخدم المسجلة، وقد يقبله إذا اعتمد على تشابه البصمة وحده.", "notes": ["البصمة الصوتية للمتحدث ما زالت قريبة من الأصل لأن المصدر صوت حقيقي.", "الفارق الحقيقي يكمن في قناة النقل وليس في هوية المتحدث."]},
            {"title": "الكشف بواسطة VoxShield", "body": "يرصد VoxShield فقدان الترددات الطرفية وذيل الصدى وارتفاع أرضية الضجيج، ويصنف المقطع على أنه معاد تشغيله.", "notes": ["استخدم زر فحص هذا المقطع لكل عينة وقارن النتيجتين.", "مؤشرات إعادة التشغيل وتوزيع الطاقة على النطاقات من أبرز العوامل في هذا النوع."]},
        ],
    },
    "synthetic-voice": {
        "index": 2,
        "title": "التوليد الصوتي بالذكاء الاصطناعي",
        "summary": "يشرح هذا السيناريو بشكل عام كيف تُنتج نماذج التوليد صوتاً يشبه شخصاً معيناً، وما الأنماط الاصطناعية التي تبقى في الإشارة الناتجة.",
        "samples": ["live", "synthetic"],
        "steps": [
            {"title": "جمع عينة من صوت الشخص المستهدف", "body": "يجمع المهاجم عينة قصيرة من صوت الشخص المستهدف من مصادر متاحة مثل المقابلات والمقاطع المنشورة.", "notes": ["تكفي أحياناً عينة قصيرة لإنتاج تقليد مقنع للسامع.", "تقليل المحتوى الصوتي المنشور للمسؤولين يحد من هذه المرحلة."]},
            {"title": "تكييف نموذج التوليد", "body": "يُكيَّف نموذج توليد صوتي على الخصائص العامة لهذه العينة مثل طبقة الصوت ولون النبرة، وهذا شرح عام لآلية العمل فقط.", "notes": ["النموذج يتعلم متوسط الخصائص ولا يعيد إنتاج التفاصيل الدقيقة العشوائية للصوت البشري."]},
            {"title": "تحويل النص إلى كلام", "body": "يُدخل نص مكتوب ليُحوَّل إلى كلام بصوت يشبه الشخص المستهدف.", "notes": ["الكلام الناتج لم يُنطق فعلياً من حنجرة بشرية.", "الإيقاع والتنغيم يكونان أكثر انتظاماً من الكلام الطبيعي."]},
            {"title": "خروج صوت شديد الانتظام", "body": "يخرج صوت شديد الانتظام في طبقته وطيفه، مع غلاف طيفي أملس وأرضية ضجيج شبه معدومة.", "notes": ["لاحظ في المخطط الطيفي غياب ضجيج التنفس بين المقاطع.", "قد تظهر فجوات فوق 7000 هرتز أو خطوط دورية ثابتة مرتبطة بخطوة الإطار."]},
            {"title": "الكشف بواسطة VoxShield", "body": "يرصد VoxShield الانتظام المفرط في طبقة الصوت وغياب ضجيج التنفس والفجوات الطيفية، ويصنف المقطع على أنه مولد اصطناعياً.", "notes": ["افحص العينتين وقارن احتمال فئة التوليد الاصطناعي في كل منهما.", "خصائص طبقة الصوت وانتظام الترددات العليا من أهم العوامل هنا."]},
        ],
    },
    "executive-impersonation": {
        "index": 3,
        "title": "انتحال صوت مسؤول تنفيذي لتمرير تحويل مالي",
        "summary": "سيناريو احتيال مالي يعتمد على صوت مولد يشبه صوت مسؤول تنفيذي، مع عرض الإجراءات الدفاعية التي تمنع تنفيذ التحويل.",
        "samples": ["synthetic"],
        "steps": [
            {"title": "جمع المعلومات", "body": "يجمع المهاجم معلومات عن المسؤول التنفيذي وعن الموظف المالي الذي ينفذ التحويلات، مثل الأسماء والمسميات وأوقات السفر.", "notes": ["المعلومات المنشورة عن الهيكل الإداري تسهّل صياغة طلب مقنع."]},
            {"title": "الاتصال بصوت مولد", "body": "يتصل المهاجم بالموظف المالي بصوت مولد يشبه صوت المسؤول ويطلب تحويلاً عاجلاً إلى حساب جديد.", "notes": ["الطلب يأتي عادة عبر قناة غير معتادة أو من رقم غير مسجل.", "استمع إلى العينة ولاحظ انتظام الإيقاع والنبرة."]},
            {"title": "ضغط الوقت والسرية", "body": "يمارس المهاجم ضغط الوقت ويؤكد على سرية العملية لدفع الموظف إلى تجاوز الإجراءات المعتادة.", "notes": ["الاستعجال وطلب السرية من أبرز مؤشرات الاحتيال الاجتماعي."]},
            {"title": "تنفيذ التحويل", "body": "يُنفَّذ التحويل قبل أن يكتشف أحد الخلل، وغالباً ما يصعب استرداد المبلغ بعد ذلك.", "notes": ["الخسارة تقع عندما يعتمد القرار على الصوت وحده."]},
            {"title": "الدفاع", "body": "يكون الدفاع بالتحقق من الطلب عبر قناة ثانية معتمدة مثل الاتصال برقم مسجل مسبقاً، وبفحص المقطع في VoxShield قبل التنفيذ.", "notes": ["افحص العينة المعروضة ولاحظ مستوى الخطورة الناتج.", "اعتماد سياسة التحقق المزدوج لكل تحويل غير معتاد يحد من هذا الهجوم."]},
        ],
    },
    "verification-bypass": {
        "index": 4,
        "title": "تجاوز نظام التحقق الصوتي",
        "summary": "يوضح هذا السيناريو الفرق بين نظام يقارن البصمة الصوتية وحدها ونظام يضيف طبقة كشف الحياة الصوتية.",
        "samples": ["replay", "synthetic"],
        "steps": [
            {"title": "نظام تحقق بعبارة صوتية", "body": "تعتمد الجهة على نظام يطلب من المستخدم نطق عبارة صوتية للتحقق من هويته.", "notes": ["يُستخدم هذا الأسلوب في مراكز الاتصال والخدمات المصرفية الهاتفية."]},
            {"title": "تقديم عينة غير حية", "body": "يقدم المهاجم عينة معاد تشغيلها أو مولدة بدلاً من صوت المستخدم الحي.", "notes": ["العينتان المعروضتان تمثلان هذين النوعين من الهجوم."]},
            {"title": "قبول النظام التقليدي للعينة", "body": "يقارن النظام التقليدي البصمة الصوتية وحدها فيقبل العينة لأنها تشبه صوت المستخدم.", "notes": ["مطابقة هوية المتحدث لا تثبت أن الصوت صادر عن شخص حي في تلك اللحظة."]},
            {"title": "إضافة طبقة كشف الحياة", "body": "تُظهر طبقة كشف الحياة أن العينة ليست صوتاً حياً فتُرفض قبل الوصول إلى مرحلة مطابقة الهوية.", "notes": ["افحص كل عينة على حدة ولاحظ نوع الهجوم الذي يحدده النظام."]},
            {"title": "توصيات للجهات المالية والمصرفية", "body": "يُوصى بدمج كشف الحياة الصوتية مع التحقق متعدد العوامل، ومراجعة المقاطع ذات الثقة المنخفضة بشرياً، وتسجيل التنبيهات ومتابعتها في مركز العمليات الأمنية.", "notes": ["لا يُعتمد على الصوت وحده في العمليات عالية القيمة.", "تُحدَّث نماذج الكشف دورياً ببيانات حقيقية حديثة."]},
        ],
    },
}


def _get_scenario(key):
    scenario = SCENARIOS.get(key)
    if scenario is None:
        abort(404)
    return scenario


def _validate_sample(key, sample):
    scenario = _get_scenario(key)
    if sample not in scenario["samples"]:
        abort(404)
    return scenario


@lru_cache(maxsize=16)
def _sample_wav(key, sample):
    seed = SAMPLE_SEEDS[sample] + SCENARIOS[key]["index"]
    return encode_wav(generate(sample, seed, SAMPLE_DURATION))


@lru_cache(maxsize=16)
def _sample_spectrogram(key, sample):
    signal, sample_rate, _duration = read_wav(_sample_wav(key, sample), 600)
    processed, _stats = preprocess(signal, sample_rate)
    return compute_spectrogram(processed)


def _progress_map():
    rows = db.query_all(
        "SELECT scenario_key, last_step, completed FROM simulation_progress WHERE user_id = ?", (g.user["id"],)
    )
    return {row["scenario_key"]: row for row in rows}


@bp.get("/simulation")
@security.login_required
def simulation_index():
    return render_template("simulation.html", scenarios=SCENARIOS, progress=_progress_map())


@bp.get("/simulation/<key>")
@security.login_required
def scenario(key):
    data = _get_scenario(key)
    progress = _progress_map().get(key)
    start_step = min(progress["last_step"], len(data["steps"]) - 1) if progress else 0
    samples = [{"key": s, "title": SAMPLE_TITLES[s]} for s in data["samples"]]
    return render_template(
        "simulation_scenario.html", key=key, scenario=data, samples=samples, start_step=start_step,
        page_data={"key": key, "steps": len(data["steps"]), "start": start_step, "samples": data["samples"]},
    )


@bp.get("/api/simulation/<key>/<sample>/spectrogram")
@security.login_required
def sample_spectrogram(key, sample):
    _validate_sample(key, sample)
    try:
        return jsonify(_sample_spectrogram(key, sample))
    except Exception:
        logger.exception("فشل حساب المخطط الطيفي لعينة المحاكاة")
        return jsonify(error="تعذر حساب المخطط الطيفي."), 500


@bp.get("/api/simulation/<key>/<sample>/audio")
@security.login_required
def sample_audio(key, sample):
    _validate_sample(key, sample)
    return send_file(io.BytesIO(_sample_wav(key, sample)), mimetype="audio/wav", download_name="{}-{}.wav".format(key, sample))


@bp.post("/api/simulation/analyze")
@security.login_required
def simulation_analyze():
    if not security.simulation_limiter.hit(g.user["id"]):
        return jsonify(error="تجاوزت الحد المسموح لفحوصات المحاكاة، يرجى المحاولة لاحقاً."), 429
    payload = request.get_json(silent=True) or {}
    key = payload.get("scenario")
    sample = payload.get("sample")
    if not isinstance(key, str) or not isinstance(sample, str) or key not in SCENARIOS or sample not in SCENARIOS[key]["samples"]:
        return jsonify(error="العينة المطلوبة غير معروفة."), 400
    try:
        result = run_analysis(_sample_wav(key, sample), 600, include_visuals=False)
    except ModelUnavailable:
        return jsonify(error="النموذج غير جاهز حالياً."), 503
    except AudioError as error:
        return jsonify(error=error.message), 400
    except Exception:
        logger.exception("فشل فحص عينة المحاكاة")
        return jsonify(error="تعذر فحص العينة."), 500
    return jsonify(
        verdict=result["verdict"], confidence=result["confidence"], attack_type=result["attack_type"],
        risk_level=result["risk_level"], probabilities=result["probabilities"], inconclusive=result["inconclusive"],
    )


@bp.post("/api/simulation/progress")
@security.login_required
def simulation_progress():
    payload = request.get_json(silent=True) or {}
    key = payload.get("scenario")
    if not isinstance(key, str) or key not in SCENARIOS:
        return jsonify(error="السيناريو غير معروف."), 400
    last_index = len(SCENARIOS[key]["steps"]) - 1
    step = security.parse_int(payload.get("step"), 0, 0, last_index)
    try:
        db.execute(
            "INSERT INTO simulation_progress (user_id, scenario_key, last_step, completed, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, scenario_key) DO UPDATE SET last_step = excluded.last_step, "
            "completed = MAX(simulation_progress.completed, excluded.completed), updated_at = excluded.updated_at",
            (g.user["id"], key, step, 1 if step == last_index else 0, db.utc_iso()),
        )
    except Exception:
        logger.exception("فشل حفظ تقدم المحاكاة")
        return jsonify(error="تعذر حفظ التقدم."), 500
    return jsonify(ok=True, step=step, completed=step == last_index)