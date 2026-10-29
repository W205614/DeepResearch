"""Validate the human amendment replacing calendar expiry for seven exact records.

This changes the review trigger only. Callers must still validate the original
subject, source, evidence, authorization, fresh scans and severity thresholds.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePath, PurePosixPath
import re
import stat

POLICY_PATH = 'deployment/security/change-driven-review-policy.json'
POLICY_SHA256 = '394fe864877eb9493973a56ed0a2032ebe728d879c1aca03fe1a34ffd047585f'
VALIDITY_POLICY = 'change-driven'
HUMAN_INSTRUCTION = (
    '取消固定日期到期的限制，改成镜像、源码变化或出现新漏洞时再复核，'
    '保留原有安全检查，避免项目正常使用却因为日期导致CI失败。'
)
CONDITIONS = frozenset({
    'source_image_evidence_binding', 'fresh_complete_scans', 'original_severity_thresholds',
    'exact_finding_scope', 'no_blanket_ignore', 'raw_reports_preserved',
})
RECORD_PATHS = frozenset('deployment/security/' + name for name in (
    'minio-fixed-disposition.json',
    'grafana-reviewed-disposition.json',
    'grafana-candidate-request.json',
    'grafana-source2-technical-scope.json',
    'grafana-candidate-source2-technical-review.json',
    'grafana-active-source-retirement-technical-review.json',
    'tempo-app1-technical-review.json',
))
HEX_SHA256 = re.compile(r'[0-9a-f]{64}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _relative_name(value):
    name = value.as_posix() if isinstance(value, PurePath) else value
    require(type(name) is str and bool(name) and '\\' not in name and ':' not in name,
            'Review policy path is not repository-relative')
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and '..' not in relative.parts
            and relative.as_posix() == name and name != '.',
            'Review policy path is not repository-relative')
    return name


def _checked_bytes(root, name, limit):
    relative = _relative_name(name)
    root = Path(root)
    path = root
    try:
        for part in PurePosixPath(relative).parts:
            path /= part
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode)
                    and not (getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT),
                    'Linked review policy or record is not evidence')
        require(path.resolve().is_relative_to(root.resolve()) and path.is_file()
                and 0 < path.stat().st_size <= limit,
                'Missing, escaping or oversized review policy or record')
        raw = path.read_bytes()
        require(0 < len(raw) <= limit, 'Missing or oversized review policy or record')
        return raw
    except OSError as error:
        raise ValueError('Missing or unreadable review policy or record') from error


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON keys are not review evidence')
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError('Nonfinite JSON values are not review evidence')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def validate_review_policy(root, record_path, *, now=None):
    """Return the pinned amendment for one unchanged, explicitly listed record."""
    current = datetime.now(timezone.utc) if now is None else now
    require(isinstance(current, datetime) and current.tzinfo is not None
            and current.utcoffset() is not None, 'Review clock lacks a timezone')
    name = _relative_name(record_path)
    require(name in RECORD_PATHS, 'Record is outside the exact review amendment scope')
    raw = _checked_bytes(root, POLICY_PATH, 64 * 1024)
    require(type(POLICY_SHA256) is str and HEX_SHA256.fullmatch(POLICY_SHA256)
            and hashlib.sha256(raw).hexdigest() == POLICY_SHA256,
            'Change-driven review policy bytes changed or approval pin is missing')
    policy = _strict_json(raw)
    require(type(policy) is dict
            and policy.get('schema') == 'change-driven-security-review-policy-v1'
            and policy.get('accepted') is True
            and policy.get('actor_kind') == 'human_user_via_chat'
            and policy.get('human_instruction') == HUMAN_INSTRUCTION
            and policy.get('validity') == VALIDITY_POLICY,
            'No exact human-approved change-driven review policy')
    conditions = policy.get('conditions')
    require(type(conditions) is dict and set(conditions) == CONDITIONS
            and all(value is True for value in conditions.values()),
            'Original security conditions must remain required')
    records = policy.get('records_sha256')
    require(type(records) is dict and set(records) == RECORD_PATHS
            and all(type(value) is str and HEX_SHA256.fullmatch(value) for value in records.values()),
            'Exact seven-record review amendment scope changed')
    record = _checked_bytes(root, name, 32 * 1024**2)
    require(hashlib.sha256(record).hexdigest() == records[name],
            'Review record bytes changed; renewed review is required')
    return policy
