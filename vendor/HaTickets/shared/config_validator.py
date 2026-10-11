# -*- coding: UTF-8 -*-
"""
Shared config validation utilities for web and mobile modules.
"""


def validate_url(url, field_name):
    """验证 URL 必须以 http:// 或 https:// 开头"""
    if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"{field_name} 必须是以 http:// 或 https:// 开头的有效 URL，实际值: {url!r}")


def validate_non_empty_list(lst, field_name):
    """验证列表必须非空"""
    if not isinstance(lst, list) or len(lst) == 0:
        raise ValueError(f"{field_name} 必须是非空列表，实际值: {lst!r}")


def validate_positive_int(value, field_name, max_value=None):
    """验证正整数，可选最大值限制"""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} 必须是正整数，实际值: {value!r}")
    if max_value is not None:
        return min(value, max_value)
    return value
