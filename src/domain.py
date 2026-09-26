"""读取并检查项目共享资料。"""

import json
from pathlib import Path

SPORT_FACETS = {
    "equipment",
    "venue",
    "safety_standards",
    "environmental_thresholds",
    "medical_resources",
}

def load_domain(path: Path) -> dict:
    """读取字段完整且带版本的业务资料。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "domain", "version", "sample_id", "actors", "facts", "constraints",
        "sports", "inspections", "work_orders", "incidents",
        "certifications", "decisions",
    }
    if not required.issubset(value):
        raise ValueError("共享资料缺少必要字段")
    if value["version"] < 1 or len(value["actors"]) < 2 or len(value["facts"]) < 2:
        raise ValueError("共享资料内容不完整")
    _check_sports(value["sports"])
    _check_references(value)
    return value

def _check_sports(sports: list) -> None:
    """每个项目都必须具备独立的设备、场地、安全、环境与医疗档案。"""
    ids = [sport.get("sport_id") for sport in sports]
    if len(ids) != len(set(ids)):
        raise ValueError("项目标识重复")
    for sport in sports:
        if not SPORT_FACETS.issubset(sport):
            raise ValueError(f"项目 {sport.get('sport_id')} 缺少认证要素")

def _check_references(value: dict) -> None:
    """跨记录引用必须闭合，决定与事件不得悬空。"""
    sport_ids = {sport["sport_id"] for sport in value["sports"]}
    for section in ("inspections", "work_orders", "incidents", "certifications"):
        for record in value[section]:
            if record.get("sport_id") not in sport_ids:
                raise ValueError(f"{section} 记录引用了未知项目")
    inspection_ids = {item["inspection_id"] for item in value["inspections"]}
    for decision in value["decisions"]:
        if not decision.get("evidence"):
            raise ValueError("决定缺少检查证据")
        unknown = set(decision["evidence"]) - inspection_ids
        if unknown:
            raise ValueError(f"决定 {decision.get('decision_id')} 引用了未知检查记录")
        if not decision.get("signer") or not decision.get("standard_version"):
            raise ValueError("决定缺少签署人或标准版本")
    for incident in value["incidents"]:
        if not incident.get("affects_schedules"):
            raise ValueError("事件未标明受影响赛程")
