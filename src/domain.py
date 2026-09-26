"""新兴项目设备场地认证：资料读取、完整性校验与角色视图。

模块只依赖标准库，围绕 ``fixtures/domain.json`` 描述的领域事实工作：
四个项目各自独立的验收清单、按时间窗生效的标准、带采集时间的检查
证据、按设施隔离的工单、证书效力、突发事件向赛程的传播，以及
开赛/延迟/取消决定的可追溯链条。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

DISCIPLINES = ("virtual_taekwondo", "padel", "surfing", "mma")
REQUIRED_TOP_LEVEL = {
    "domain", "version", "sample_id", "as_of", "actors", "facts", "constraints",
    "venues", "people", "standards", "equipment", "facilities", "checklists",
    "inspections", "work_orders", "certificates", "events", "schedule", "decisions",
}


def load_domain(path: Path) -> dict:
    """读取共享资料并执行全部引用与业务规则校验。"""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate(value)
    if errors:
        raise ValueError("共享资料校验失败：" + "；".join(errors))
    return value


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parent_ref(ref: str) -> str:
    """fac-pa-court1#pnl-s -> fac-pa-court1；无部件标记时原样返回。"""
    return ref.split("#", 1)[0]


def _index(items, key="id"):
    return {item[key]: item for item in items}


def validate(data: dict) -> list[str]:
    """返回全部校验错误；空列表表示资料可用于放行判断。"""
    errors: list[str] = []

    missing = REQUIRED_TOP_LEVEL - data.keys()
    if missing:
        return [f"共享资料缺少必要字段：{sorted(missing)}"]

    as_of = _dt(data["as_of"])

    venues = _index(data["venues"])
    people = _index(data["people"])
    standards = _index(data["standards"])
    equipment = _index(data["equipment"])
    facilities = _index(data["facilities"])
    checklists = _index(data["checklists"])
    inspections = _index(data["inspections"])
    work_orders = _index(data["work_orders"])
    certificates = _index(data["certificates"])
    events = _index(data["events"])
    sessions = _index(data["schedule"])
    decisions = _index(data["decisions"])

    for name, table in (
        ("venues", venues), ("people", people), ("standards", standards),
        ("equipment", equipment), ("facilities", facilities),
        ("checklists", checklists), ("inspections", inspections),
        ("work_orders", work_orders), ("certificates", certificates),
        ("events", events), ("schedule", sessions), ("decisions", decisions),
    ):
        if len(table) != len(data[name]):
            errors.append(f"{name} 存在重复 id")

    # 每个项目在快照时刻恰有一个生效标准，旧标准必须已经闭合。
    effective_std: dict[str, str] = {}
    for std in data["standards"]:
        if std["discipline"] not in DISCIPLINES:
            errors.append(f"标准 {std['id']} 的项目取值非法")
        if std["effective_to"] is not None and _dt(std["effective_to"]) < _dt(std["effective_from"]):
            errors.append(f"标准 {std['id']} 生效时间窗倒置")
        active_now = (
            _dt(std["effective_from"]) <= as_of
            and (std["effective_to"] is None or _dt(std["effective_to"]) >= as_of)
        )
        if active_now:
            if std["discipline"] in effective_std:
                errors.append(f"项目 {std['discipline']} 在快照时刻存在多个生效标准")
            effective_std[std["discipline"]] = std["id"]
    for disc in DISCIPLINES:
        if disc not in effective_std:
            errors.append(f"项目 {disc} 在快照时刻缺少生效标准")

    # 设施与可独立换件的子部件（如逐块玻璃面板）。
    component_owners: dict[str, str] = {}
    for fac in data["facilities"]:
        if fac["venue_id"] not in venues:
            errors.append(f"设施 {fac['id']} 引用了不存在的场馆 {fac['venue_id']}")
        for comp in fac["components"]:
            if _parent_ref(comp) != fac["id"]:
                errors.append(f"设施 {fac['id']} 声明的部件 {comp} 前缀不一致")
            component_owners[comp] = fac["id"]

    def _ref_kind(ref: str) -> str:
        if ref == "@discipline":
            return "discipline"
        if "#" in ref:
            return "component" if ref in component_owners else "unknown"
        if ref in equipment:
            return "equipment"
        if ref in facilities:
            return "facility"
        if ref in people:
            return "person"
        return "unknown"

    for eq in data["equipment"]:
        if eq["venue_id"] not in venues:
            errors.append(f"设备 {eq['id']} 引用了不存在的场馆 {eq['venue_id']}")

    for person in data["people"]:
        for vid in person["venue_scopes"]:
            if vid not in venues:
                errors.append(f"人员 {person['id']} 的场馆作用域 {vid} 不存在")

    # 验收清单按项目独立，引用资源必须存在，且挂在当时有效的标准上。
    for cl in data["checklists"]:
        if cl["standard_id"] not in standards:
            errors.append(f"清单 {cl['id']} 引用了不存在的标准 {cl['standard_id']}")
        elif standards[cl["standard_id"]]["id"] != effective_std.get(cl["discipline"]):
            errors.append(f"清单 {cl['id']} 未挂接项目 {cl['discipline']} 当前生效标准")
        for target in cl["targets"]:
            if _ref_kind(target) == "unknown":
                errors.append(f"清单 {cl['id']} 引用了不存在的资源 {target}")

    # 检查数据：结论与读数一致，照片带采集时间且与检查同一时间窗。
    for ins in data["inspections"]:
        cl = checklists.get(ins["item_id"])
        if cl is None:
            errors.append(f"检查 {ins['id']} 引用了不存在的清单 {ins['item_id']}")
            continue
        if ins["target_ref"] != "@discipline" and ins["target_ref"] not in cl["targets"]:
            errors.append(f"检查 {ins['id']} 的对象 {ins['target_ref']} 不在清单 {cl['id']} 范围内")
        if ins["measured_by"] not in people:
            errors.append(f"检查 {ins['id']} 的检查人 {ins['measured_by']} 不存在")
        if _dt(ins["measured_at"]) > as_of:
            errors.append(f"检查 {ins['id']} 采集时间晚于资料快照")
        all_pass = all(r["pass"] for r in ins["readings"]) and bool(ins["readings"])
        if ins["verdict"] == "pass" and not all_pass:
            errors.append(f"检查 {ins['id']} 判定合格但存在不合格读数")
        if ins["verdict"] == "fail" and all_pass:
            errors.append(f"检查 {ins['id']} 判定不合格但读数全部合格")
        for photo in ins["photos"]:
            delta = _dt(photo["captured_at"]) - _dt(ins["measured_at"])
            if delta < timedelta(0) or delta > timedelta(hours=1):
                errors.append(
                    f"检查 {ins['id']} 的照片 {photo['id']} 采集时间与检查时间不一致"
                )

    # 工单：只作用于单一设施/设备；处理人须在对应场馆作用域内；
    # 关闭必须凭针对同一 scope 的复检证据，且只有永久修复才能恢复放行。
    def _venue_of(ref: str) -> str | None:
        if ref in equipment:
            return equipment[ref]["venue_id"]
        owner = component_owners.get(ref)
        if owner:
            return facilities[owner]["venue_id"]
        if ref in facilities:
            return facilities[ref]["venue_id"]
        return None

    for wo in data["work_orders"]:
        assignee = people.get(wo["assignee"])
        if assignee is None:
            errors.append(f"工单 {wo['id']} 处理人不存在")
        elif assignee["role"] != "venue_staff":
            errors.append(f"工单 {wo['id']} 必须由场馆技术人员处理")
        scope_venue = _venue_of(wo["scope_ref"])
        if scope_venue is None:
            errors.append(f"工单 {wo['id']} 的作用对象 {wo['scope_ref']} 不存在")
        elif assignee and scope_venue not in assignee["venue_scopes"]:
            errors.append(f"工单 {wo['id']} 超出处理人 {assignee['id']} 的场馆作用域")
        if wo["status"] == "closed":
            res = wo["resolution"]
            if res is None:
                errors.append(f"工单 {wo['id']} 已关闭但缺少处置记录")
                continue
            re_ins = inspections.get(res["inspection_id"])
            if re_ins is None:
                errors.append(f"工单 {wo['id']} 引用了不存在的复检 {res['inspection_id']}")
            elif re_ins["target_ref"] != wo["scope_ref"]:
                errors.append(f"工单 {wo['id']} 的复检证据不属于其作用设施，换件影响被越界传播")
            if not res["permanent"]:
                errors.append(f"工单 {wo['id']} 仅为临时修复，不得关闭并恢复放行")

    # 证书链：旧证书被新证书取代，暂停必须带原因与时间。
    for cert in data["certificates"]:
        if cert["standard_id"] not in standards:
            errors.append(f"证书 {cert['id']} 引用了不存在的标准")
        if cert["issued_by"] not in people or people[cert["issued_by"]]["role"] != "certifier":
            errors.append(f"证书 {cert['id']} 签发人缺失或不是安全认证官")
        if cert["status"] == "superseded":
            successor = certificates.get(cert["superseded_by"]) if cert["superseded_by"] else None
            if successor is None:
                errors.append(f"旧证书 {cert['id']} 未指明或未找到取代它的新证书")
            elif successor["scope_discipline"] != cert["scope_discipline"]:
                errors.append(f"证书 {cert['id']} 被不同项目的证书取代")
            elif _dt(successor["issued_at"]) < _dt(cert["issued_at"]):
                errors.append(f"证书 {cert['id']} 的取代证书签发时间早于原证书")
        if cert["status"] == "suspended" and not (cert["suspended_at"] and cert["reason"]):
            errors.append(f"证书 {cert['id']} 暂停缺少时间或原因")
        for ref in cert["scope_refs"]:
            if _ref_kind(ref) == "unknown":
                errors.append(f"证书 {cert['id']} 引用了不存在的资源 {ref}")

    # 突发事件传播：开放事件必须覆盖所有依赖被阻断资源的赛程。
    blocked_by_session: dict[str, list[str]] = {}
    for evt in data["events"]:
        if evt["source_inspection"] not in inspections:
            errors.append(f"事件 {evt['id']} 缺少来源检查")
        blocked = set(evt["blocks_refs"])
        for ref in blocked:
            if _ref_kind(ref) == "unknown":
                errors.append(f"事件 {evt['id']} 阻断了不存在的资源 {ref}")
        if evt["status"] == "closed":
            res = evt["resolution"]
            if res is None:
                errors.append(f"事件 {evt['id']} 已关闭但缺少处置记录")
            elif not res["permanent"]:
                errors.append(f"事件 {evt['id']} 仅凭临时修复关闭，不得据此恢复赛程")
            continue
        for sess in data["schedule"]:
            if blocked.intersection(sess["requires"]):
                blocked_by_session.setdefault(sess["id"], []).append(evt["id"])
                if sess["id"] not in evt["affected_sessions"]:
                    errors.append(
                        f"事件 {evt['id']} 未传播到受影响赛程 {sess['id']}"
                    )

    # 赛程引用。
    for sess in data["schedule"]:
        if sess["venue_id"] not in venues:
            errors.append(f"赛程 {sess['id']} 引用了不存在的场馆")
        for ref in sess["requires"]:
            if _ref_kind(ref) == "unknown":
                errors.append(f"赛程 {sess['id']} 依赖不存在的资源 {ref}")
        if sess["decision_id"] is not None and sess["decision_id"] not in decisions:
            errors.append(f"赛程 {sess['id']} 引用了不存在的决定")

    # 决定：锚定当时有效标准、证据与签署人；只有“开赛”需要满足全部放行条件。
    for dec in data["decisions"]:
        sess = sessions.get(dec["session_id"])
        if sess is None:
            errors.append(f"决定 {dec['id']} 引用了不存在的赛程")
            continue
        decided_at = _dt(dec["decided_at"])
        signer = people.get(dec["signed_by"])
        if signer is None or signer["role"] != "technical_delegate":
            errors.append(f"决定 {dec['id']} 签署人缺失或不是技术官员")
        std = standards.get(dec["standard_id"])
        if std is None:
            errors.append(f"决定 {dec['id']} 引用了不存在的标准")
        elif not (_dt(std["effective_from"]) <= decided_at and
                  (std["effective_to"] is None or _dt(std["effective_to"]) >= decided_at)):
            errors.append(f"决定 {dec['id']} 引用了签署时刻尚未生效的标准版本")
        for eid in dec["event_ids"]:
            if eid not in events:
                errors.append(f"决定 {dec['id']} 引用了不存在的事件 {eid}")
        for iid in dec["evidence_inspection_ids"]:
            ins = inspections.get(iid)
            if ins is None:
                errors.append(f"决定 {dec['id']} 引用了不存在的检查证据 {iid}")
            elif _dt(ins["measured_at"]) > decided_at:
                errors.append(f"决定 {dec['id']} 引用了晚于签署时间的证据 {iid}")
        if dec["certificate_id"] is not None:
            cert = certificates.get(dec["certificate_id"])
            if cert is None:
                errors.append(f"决定 {dec['id']} 引用了不存在的证书")
            elif dec["outcome"] == "go" and not _cert_allows(cert, sess, decided_at):
                errors.append(f"开赛决定 {dec['id']} 使用了当时无效或不覆盖该场的证书")
        elif dec["outcome"] == "go":
            errors.append(f"开赛决定 {dec['id']} 缺少有效证书")

        if dec["outcome"] == "go":
            blockers = blocked_by_session.get(sess["id"], [])
            if blockers:
                errors.append(f"开赛决定 {dec['id']} 仍有未解除事件：{blockers}")
            failing = [
                g for g in _fresh_gaps(data, sess, decided_at)
                if g["status"] != "pass"
            ]
            if failing:
                errors.append(
                    f"开赛决定 {dec['id']} 存在未闭合缺口："
                    + ",".join(g["checklist_id"] + "/" + str(g["target_ref"]) for g in failing)
                )

    return errors


def _cert_allows(cert: dict, session: dict, at: datetime) -> bool:
    """证书在指定时刻能否覆盖该场：active、在有效期内、范围覆盖该场所用设施。"""
    if cert["status"] != "active" or cert.get("suspended_at"):
        return False
    if not (_dt(cert["valid_from"]) <= at <= _dt(cert["valid_to"])):
        return False
    if cert["scope_discipline"] != session["discipline"]:
        return False
    if not cert["scope_refs"]:
        return True
    scope = set(cert["scope_refs"])
    for ref in session["requires"]:
        if ref.startswith("fac-") and _parent_ref(ref) not in scope:
            return False
    return True


def _latest_by_target(inspections: list[dict], item_id: str) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for ins in inspections:
        if ins["item_id"] != item_id:
            continue
        cur = latest.get(ins["target_ref"])
        if cur is None or _dt(ins["measured_at"]) > _dt(cur["measured_at"]):
            latest[ins["target_ref"]] = ins
    return latest


def _fresh_gaps(data, session, at) -> list[dict]:
    """评估单场在指定时刻逐项缺口（按清单周期判定过期/缺失/不合格）。"""
    gaps = []
    for cl in data["checklists"]:
        same_discipline = cl["discipline"] == session["discipline"]
        # 跨项目借用的设备（如板式网球演示借用跆拳道头显）按资源命中，
        # 但其项目级环境条目不适用于本场。
        if not same_discipline and not set(cl["targets"]).intersection(session["requires"]):
            continue
        latest = _latest_by_target(data["inspections"], cl["id"])
        window = timedelta(hours=cl["cadence_hours"])
        for target in cl["targets"] or ["@discipline"]:
            if target == "@discipline":
                if not same_discipline:
                    continue
            elif target not in session["requires"]:
                continue
            ins = latest.get(target)
            if ins is None:
                status = "missing"
            elif _dt(ins["measured_at"]) < at - window:
                status = "stale"
            else:
                status = "pass" if ins["verdict"] == "pass" else "fail"
            if status != "pass":
                gaps.append({
                    "checklist_id": cl["id"],
                    "kind": cl["kind"],
                    "target_ref": target,
                    "status": status,
                    "requirement": cl["requirement"],
                    "latest_inspection": ins["id"] if ins else None,
                    "alternatives": list(cl["alternatives"]),
                })
    return gaps


def session_readiness(data: dict, session_id: str, at: datetime | None = None) -> dict:
    """汇总单场开赛条件：证书、逐项缺口、阻断事件与可执行替代方案。"""
    at = at or _dt(data["as_of"])
    sessions = _index(data["schedule"])
    certificates = _index(data["certificates"])
    standards = _index(data["standards"])
    session = sessions[session_id]

    gaps = _fresh_gaps(data, session, at)
    blockers = [
        evt["id"] for evt in data["events"]
        if evt["status"] == "open" and set(evt["blocks_refs"]).intersection(session["requires"])
    ]

    cert_rows = []
    for cert in data["certificates"]:
        if cert["scope_discipline"] != session["discipline"]:
            continue
        cert_rows.append({
            "certificate_id": cert["id"],
            "usable": _cert_allows(cert, session, at),
            "status": cert["status"],
            "reason": cert["reason"],
        })

    std = next(
        (s for s in data["standards"]
         if s["discipline"] == session["discipline"]
         and _dt(s["effective_from"]) <= at
         and (s["effective_to"] is None or _dt(s["effective_to"]) >= at)),
        None,
    )
    return {
        "session_id": session_id,
        "label": session["label"],
        "at": at.isoformat(),
        "standard_id": std["id"] if std else None,
        "certificates": cert_rows,
        "blocking_events": blockers,
        "gaps": gaps,
        "ready": not blockers and not gaps
        and any(c["usable"] for c in cert_rows),
    }


def gap_report(data: dict, discipline: str | None = None, at: datetime | None = None) -> list[dict]:
    """技术官员视图：逐项目缺口与替代方案（覆盖全部赛程）。"""
    at = at or _dt(data["as_of"])
    report = []
    for session in sorted(data["schedule"], key=lambda s: s["starts_at"]):
        if discipline and session["discipline"] != discipline:
            continue
        report.append(session_readiness(data, session["id"], at))
    return report


def official_view(data: dict, person_id: str) -> dict:
    """技术官员赛前视图：按其项目/场馆作用域过滤的逐项缺口。"""
    person = _index(data["people"])[person_id]
    if person["role"] != "technical_delegate":
        raise PermissionError("仅技术官员可查看逐项缺口视图")
    sessions = _index(data["schedule"])
    rows = gap_report(data)
    if person["venue_scopes"]:
        rows = [
            row for row in rows
            if sessions[row["session_id"]]["venue_id"] in person["venue_scopes"]
        ]
    return {"official": person_id, "sessions": rows}


def staff_work_orders(data: dict, person_id: str) -> list[dict]:
    """场馆人员视图：只能看到分配给自己的工单，工单互不串扰。"""
    person = _index(data["people"])[person_id]
    if person["role"] != "venue_staff":
        raise PermissionError("场馆工单视图仅对场馆技术人员开放")
    return [wo for wo in data["work_orders"] if wo["assignee"] == person_id]


def decision_trace(data: dict, decision_id: str) -> dict:
    """还原一个开赛/延迟/取消决定当时依据的标准、证书、证据与签署人。"""
    decisions = _index(data["decisions"])
    people = _index(data["people"])
    standards = _index(data["standards"])
    certificates = _index(data["certificates"])
    inspections = _index(data["inspections"])
    events = _index(data["events"])
    sessions = _index(data["schedule"])

    dec = decisions[decision_id]
    cert = certificates.get(dec["certificate_id"]) if dec["certificate_id"] else None
    return {
        "decision_id": dec["id"],
        "session_id": dec["session_id"],
        "session_label": sessions[dec["session_id"]]["label"],
        "outcome": dec["outcome"],
        "decided_at": dec["decided_at"],
        "signed_by": {"id": dec["signed_by"], "name": people[dec["signed_by"]]["name"]},
        "standard": standards[dec["standard_id"]],
        "certificate": cert,
        "certificate_was_usable": (
            _cert_allows(cert, sessions[dec["session_id"]], _dt(dec["decided_at"]))
            if cert else False
        ),
        "evidence": [inspections[i] for i in dec["evidence_inspection_ids"]],
        "events": [events[e] for e in dec["event_ids"]],
        "alternative": dec["alternative"],
        "note": dec["note"],
    }
