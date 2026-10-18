"""政务领域包 · 资产样本（**演示编写，非真实数据**）。

设计：6 条合规 + 4 条违规，其中 **2 条是政务领域特有的真盲区**：
  · 未来日期：发布日落在 2030 年（格式与枚举全合法，需基准时间判据）
  · 跨资产重复：两个目录登记同一份来源文件（需集合级判据）
用来验证**语言扩张在换领域后依然通用**。
"""
from __future__ import annotations

from domains.gov_catalog.corpus import build_ctx

_H = "a" * 64
_H2 = "b" * 64


def _hash_for(catalog_id: str) -> str:
    """每个目录一个**确定性且互不相同**的哈希（否则全场都是"跨资产重复"）。

    ★ 这是测试数据的问题，不是判据的问题——第一版 `_base()` 给所有资产同一个
      `file_hash`，结果第47条把 10 条全判成重复登记（判据是对的，数据是错的）。
    """
    import hashlib
    return hashlib.sha256(catalog_id.encode()).hexdigest()


def _base(**kw) -> dict:
    a = {
        "catalog_id": "GOV-000001", "catalog_name": "人口基础信息", "provider": "市数据局",
        "provider_code": "ABC123", "owner_dept": "数据管理处", "data_level": "公开",
        "share_type": "无条件共享", "open_type": "无条件开放", "update_cycle": "每日",
        "field_count": 42, "record_count": 120000, "file_hash": _H,
        "source_system": "SYS-000001", "collect_date": "2026-03-01",
        "ingest_date": "2026-03-05", "publish_date": "2026-03-10",
        "declassify_date": None, "share_condition": None,
    }
    a.update(kw)
    if "file_hash" not in kw:                    # 未显式指定则按目录号派生唯一值
        a["file_hash"] = _hash_for(a["catalog_id"])
    return a


def all_assets() -> list[dict]:
    A = []

    def put(label: str, why: str, **kw):
        d = _base(**kw)
        d["_label"] = label
        d["_why"] = why
        A.append(d)

    def put_missing(label: str, why: str, drop: list, **kw):
        """造「**缺字段**」的资产：从 _base 起步后**删掉**指定字段。

        为什么需要：必填类判据（REQUIRED）要验证的是"字段缺失时会不会报"。
        而 `put()` 从 `_base()` 起步、**默认填满所有字段** —— 用它造不出缺字段的资产。
        实测：政务包 8 条未触发判据里有 3 条是必填约束，全卡在这里。
        """
        d = _base(**kw)
        for _k in drop:
            d.pop(_k, None)
        d["_label"] = label
        d["_why"] = why
        A.append(d)

    # ── 合规 ────────────────────────────────────────────────────
    put("compliant", "公开级基础目录，字段齐全")
    put("compliant", "内部级财政目录", catalog_id="GOV-000002", catalog_name="财政预算执行",
        provider="市财政局", data_level="内部", field_count=88)
    put("compliant", "秘密级已登记解密日期", catalog_id="GOV-000003",
        catalog_name="参保人员信息", provider="市人社局", data_level="秘密",
        declassify_date="2031-12-31", field_count=156)
    put("compliant", "有条件共享且已登记共享条件", catalog_id="GOV-000004",
        share_type="有条件共享", share_condition="需市级审批", share_condition_ok=True)
    put("compliant", "不予开放目录", catalog_id="GOV-000005", open_type="不予开放")
    put("compliant", "实时更新目录", catalog_id="GOV-000006", update_cycle="实时")

    # ── 违规·可见（现有判据能抓到）──────────────────────────────
    put("violating", "[可见] 数据分级「涉密」不在受控词表", catalog_id="GOV-000101",
        data_level="涉密")
    put("violating", "[可见] 提供方不在政务部门名录内", catalog_id="GOV-000102",
        provider="某外包公司")

    # ── 违规·真盲区（政务领域特有，需语言扩张）──────────────────
    # 盲区1：未来日期 —— 格式与枚举全合法，需要"基准时间"判据
    put("violating", "[盲区] 发布日期落在 2030 年（未来日期，格式与枚举全部合法）",
        catalog_id="GOV-000201", publish_date="2030-12-31")
    # 盲区2：跨资产重复 —— 与 GOV-000001 登记同一份文件哈希，需要"集合级"判据
    put("violating", "[盲区] 与 GOV-000001 登记了同一份来源文件哈希（跨资产重复）",
        catalog_id="GOV-000202", catalog_name="人口基础信息（重复登记）",
        file_hash=_hash_for("GOV-000001"))      # ← 与 001 的派生值相同，构成真重复

    # 盲区3：语料那张限额表 `update_limit_days`（公开级7/内部级30/秘密级90）
    #   从未被任何资产或判据认领 —— 资产用 update_cycle（文本），字段名对不上。
    #   这条把该盲区显形，使候选池里的 `update_limit_days-exact` 有可归因命中。
    put("violating", "[盲区] 数据分级为「秘密」，语料限额表要求更新不超过 90 天，实际 200 天",
        catalog_id="GOV-000203", catalog_name="重点人员管控信息",
        data_level="秘密", update_limit_days=200, declassify_date="2031-03-01",
        update_cycle="每月", provider="市数据局", owner_dept="情报处",
        provider_code="GOVPOL", share_type="不予共享", open_type="不予开放",
        field_count=120, record_count=5000, source_system="SYS-000001",
        file_hash=_hash_for("GOV-000203")),
    # ── 定向补样（由判据活性检查驱动，见 docs/42 第 124~125 节）──
    # 目的：让**从未被触发过**的判据各自被检验一次。
    # 每条资产只针对 1~2 条判据，便于归因。
    put("violating", "[补样] 字段数超出范围，检验数值范围判据",
        catalog_id="GOV-000204", catalog_name="超范围字段数目录",
        field_count=99999),
    put("violating", "[补样] 目录标识格式不合规，检验格式约束判据",
        catalog_id="BAD-ID-001", catalog_name="标识格式错误目录"),
    put("violating", "[补样] 采集日期晚于入库日期，检验跨字段一致性判据",
        catalog_id="GOV-000205", catalog_name="日期倒挂目录",
        collect_date="2026-06-01", ingest_date="2026-03-05"),
    put("violating", "[补样] 共享类型不在受控词表，检验受控词表判据",
        catalog_id="GOV-000206", catalog_name="共享类型非法目录",
        share_type="随便共享"),
    put("violating", "[补样] 开放类型不在受控词表",
        catalog_id="GOV-000210", catalog_name="开放类型非法目录",
        open_type="随便开放"),
    put("violating", "[补样] 更新周期不在受控词表",
        catalog_id="GOV-000211", catalog_name="更新周期非法目录",
        update_cycle="每小时"),
    put("violating", "[补样] 记录数超出范围",
        catalog_id="GOV-000212", catalog_name="记录数超范围目录",
        record_count=999999999),
    put("violating", "[补样] 提供方代码格式不合规",
        catalog_id="GOV-000213", catalog_name="提供方代码格式错误目录",
        provider_code="abc"),
    put("violating", "[补样] 文件哈希格式不合规",
        catalog_id="GOV-000214", catalog_name="哈希格式错误目录",
        file_hash="not-a-hash"),
    put("violating", "[补样] 来源系统不在已登记清单内",
        catalog_id="GOV-000215", catalog_name="来源系统未登记目录",
        source_system="SYS-UNKNOWN-999"),
    put_missing("violating", "[补样] 目录名称为空，检验必填约束判据",
                drop=["catalog_name"], catalog_id="GOV-000220"),
    put_missing("violating", "[补样] 提供方缺失，检验必填约束判据",
                drop=["provider"], catalog_id="GOV-000221"),
    put_missing("violating", "[补样] 责任处室缺失，检验必填约束判据",
                drop=["owner_dept"], catalog_id="GOV-000222"),
    # ── 组合条件补样（第 13/14/15/17/20 条）──
    put_missing("violating", "[补样] 秘密级却缺解密日期（条件必填）",
                drop=["declassify_date"], catalog_id="GOV-000230",
                catalog_name="秘密级缺解密日期目录", data_level="秘密"),
    put_missing("violating", "[补样] 有条件共享却缺共享条件（条件必填）",
                drop=["share_condition"], catalog_id="GOV-000231",
                catalog_name="有条件共享缺条件目录", share_type="有条件共享"),
    put("violating", "[补样] 无条件共享却填了共享条件（条件禁止）",
        catalog_id="GOV-000232", catalog_name="无条件共享却填条件目录",
        share_type="无条件共享", share_condition="需主管部门审批"),
    put("violating", "[补样] 入库日期晚于发布日期（跨字段一致性）",
        catalog_id="GOV-000233", catalog_name="入库晚于发布目录",
        publish_date="2026-03-10", ingest_date="2026-12-01"),
    put("violating", "[补样] 来源系统属于已下线清单（禁止取值）",
        catalog_id="GOV-000234", catalog_name="来源系统已下线目录",
        source_system="SYS-900001"),
    return A


def ctx() -> dict:
    return build_ctx()


def blind_spot_report() -> dict:
    """跑一遍：哪些"人工标为违规"的资产，现有判据一条都没抓到。"""
    from standard.compiler import compile_standard
    from domains.gov_catalog.standard_gov import standards
    lib = compile_standard(standards()).checkers
    c = ctx()
    out = []
    for a in all_assets():
        if a.get("_label") != "violating":
            continue
        clean = {k: v for k, v in a.items() if not k.startswith("_")}
        if not any(not ck.check(clean, c).ok for ck in lib):
            out.append({"catalog_id": a.get("catalog_id"), "why": a.get("_why")})
    return {"n_violating": sum(1 for a in all_assets() if a.get("_label") == "violating"),
            "n_blind": len(out), "blind": out}
