#!/usr/bin/env python3
"""Local static self-check for the Nexent skill packages under this directory.

Five required checks (per skill directory that contains a SKILL.md):

  1. the directory name equals the frontmatter ``name``
  2. the frontmatter carries ONLY the five whitelisted keys
  3. every text file is UTF-8 without BOM
  4. scripts live only under ``scripts/`` and end in ``.py`` or ``.sh``
  5. the SKILL.md body is at most 500 lines

Extra checks that guard the same contract:

  6. the frontmatter matches Nexent's ``FRONTMATTER_PATTERN`` and parses as YAML
  7. ``description`` is present and fits ``VARCHAR(1000)``
  8. every ``allowed-tools`` entry is a real std_*/evo_*/asset_* MCP tool
  9. every ``script_outputs`` entry exists, is ``kind: file`` and declares MIME types
 10. every ``<reference>`` / ``<use_script>`` path named in the body exists

With ``--execute`` it also runs each declared script's ``--selftest`` and checks
the eight artifact-contract rules from
``doc/docs/zh/user-guide/resource-repository/custom-file-generation-skill.md``.

Usage:
    python skills/_validate.py            # static checks only
    python skills/_validate.py --execute  # static checks + artifact contract

Exit code 0 = all skills pass.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# This is a local developer tool: force UTF-8 output so Chinese report lines are
# readable regardless of the host console code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SKILL_FILE = "SKILL.md"
REQUIRED_KEYS = frozenset(["name", "description", "allowed-tools", "tags", "script_outputs"])
MAX_BODY_LINES = 500
MAX_DESCRIPTION_CHARS = 1000
SCRIPT_SUFFIXES = (".py", ".sh")
TEXT_SUFFIXES = (".md", ".py", ".sh", ".yaml", ".yml", ".txt", ".json", ".csv")

# Nexent: sdk/nexent/skills/skill_loader.py:26
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
BOM = b"\xef\xbb\xbf"

# The MCP tools this project exposes (docs/32 §3). Nothing else may be named.
MCP_TOOLS = frozenset([
    "std_clause_list", "std_verify", "std_compliance", "std_hard_anchors",
    "std_propose", "std_audit", "std_ledger",
    "evo_init", "evo_step", "evo_status", "evo_finalize", "evo_archive",
    "asset_ingest", "asset_search", "asset_lineage", "asset_blindspots",
])

# skill-creator/SKILL.md:102-112 -- a skill should contain only essential files.
DISCOURAGED_ROOT_FILES = frozenset([
    "README.md", "CHANGELOG.md", "INSTALLATION_GUIDE.md", "LICENSE", "NOTES.md",
])

RESOURCE_PATTERN = re.compile(r"<(reference|use_script)\s+path=\"([^\"]+)\"\s*/?>")

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


class Check:
    def __init__(self, label: str) -> None:
        self.label = label
        self.status = PASS
        self.detail = ""

    def ok(self, detail: str = "") -> "Check":
        self.detail = detail
        return self

    def fail(self, detail: str) -> "Check":
        self.status = FAIL
        self.detail = detail
        return self

    def warn(self, detail: str) -> "Check":
        if self.status != FAIL:
            self.status = WARN
        self.detail = detail
        return self


# Build artefacts are not part of the skill package and must not fail the checks.
IGNORED_DIRS = frozenset(["__pycache__", ".git", ".pytest_cache", ".mypy_cache",
                          ".ruff_cache", ".venv", "venv", "node_modules"])


def iter_files(skill_dir: Path):
    """Every real file of the skill package, skipping build artefacts."""
    for path in sorted(skill_dir.rglob("*")):
        parts = path.relative_to(skill_dir).parts
        if any(part in IGNORED_DIRS for part in parts):
            continue
        if path.is_file():
            yield path


def iter_text_files(skill_dir: Path):
    for path in iter_files(skill_dir):
        if path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def read_utf8_no_bom(path: Path) -> tuple[str | None, str]:
    """Return (text, error). text is None when the file cannot be read safely."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"无法读取：{exc}"
    if data.startswith(BOM):
        return None, "UTF-8 BOM 存在（会让平台的 frontmatter 正则直接失败）"
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError as exc:
        return None, f"不是合法 UTF-8：{exc}"


def check_skill(skill_dir: Path) -> tuple[list[Check], dict]:
    checks: list[Check] = []
    skill_md = skill_dir / SKILL_FILE
    info: dict = {"name": skill_dir.name, "body_lines": 0, "files": 0, "scripts": []}

    # --- 3. UTF-8 without BOM, for every text file -------------------------
    bom_check = Check("③ UTF-8 无 BOM")
    texts: dict[Path, str] = {}
    bad: list[str] = []
    for path in iter_text_files(skill_dir):
        text, error = read_utf8_no_bom(path)
        rel = path.relative_to(skill_dir).as_posix()
        if error:
            bad.append(f"{rel}：{error}")
        else:
            texts[path] = text or ""
    info["files"] = len(list(iter_text_files(skill_dir)))
    if bad:
        bom_check.fail("；".join(bad))
    else:
        bom_check.ok(f"{info['files']} 个文本文件全部为 UTF-8 无 BOM")
    checks.append(bom_check)

    # --- 4. scripts only under scripts/, legal suffix ----------------------
    script_check = Check("④ 脚本仅在 scripts/ 下且后缀合法")
    script_files = [p for p in iter_files(skill_dir) if p.suffix.lower() in SCRIPT_SUFFIXES]
    misplaced = [p.relative_to(skill_dir).as_posix() for p in script_files
                 if p.parent != skill_dir / "scripts"]
    illegal = [p.relative_to(skill_dir).as_posix()
               for p in ((skill_dir / "scripts").rglob("*")
                         if (skill_dir / "scripts").is_dir() else [])
               if p.is_file() and p.suffix.lower() not in SCRIPT_SUFFIXES
               and "__pycache__" not in p.parts]
    info["scripts"] = sorted(p.name for p in script_files)
    if misplaced:
        script_check.fail("脚本出现在 scripts/ 之外：" + "、".join(misplaced))
    elif illegal:
        script_check.fail("scripts/ 下存在非 .py/.sh 文件：" + "、".join(illegal))
    else:
        script_check.ok("、".join(sorted(p.relative_to(skill_dir).as_posix()
                                         for p in script_files)) or "无脚本")
    checks.append(script_check)

    if skill_md not in texts:
        checks.append(Check("① 目录名 == frontmatter name").fail("SKILL.md 缺失或无法解码"))
        for label in ("② frontmatter 白名单", "⑤ 正文行数 ≤ 500"):
            checks.append(Check(label).fail("SKILL.md 不可用"))
        return checks, info

    raw = texts[skill_md]

    # --- 6. frontmatter regex + YAML --------------------------------------
    regex_check = Check("⑥ frontmatter 正则匹配并可 YAML 解析")
    match = FRONTMATTER_PATTERN.match(raw)
    meta: dict = {}
    if not match:
        regex_check.fail("不匹配 FRONTMATTER_PATTERN：文件必须以 --- 开头，"
                         "frontmatter 与正文之间有空行分隔的 ---")
        meta = {}
    else:
        try:
            import yaml  # PyYAML ships with the Nexent runtime
            loaded = yaml.safe_load(match.group(1))
        except Exception as exc:                       # noqa: BLE001 - report anything
            loaded = None
            regex_check.fail(f"YAML 解析失败：{exc}")
        if regex_check.status == PASS:
            if not isinstance(loaded, dict):
                regex_check.fail(f"frontmatter 顶层必须是映射，实际 {type(loaded).__name__}")
            else:
                meta = loaded
                regex_check.ok(f"解析出 {len(meta)} 个键")
    checks.append(regex_check)

    # --- 1. directory name == name ----------------------------------------
    name_check = Check("① 目录名 == frontmatter name")
    declared = meta.get("name")
    if not isinstance(declared, str) or not declared:
        name_check.fail("frontmatter 缺少 name（SkillLoader 会抛 ValueError）")
    elif declared != skill_dir.name:
        name_check.fail(f"name='{declared}' 与目录名 '{skill_dir.name}' 不一致"
                        f"（read_skill_md 会找不到目录）")
    else:
        name_check.ok(skill_dir.name)
    checks.append(name_check)

    # --- 2. whitelist ------------------------------------------------------
    keys_check = Check("② frontmatter 仅含 5 个白名单键")
    if not meta:
        keys_check.fail("frontmatter 为空")
    else:
        extra = sorted(set(meta) - REQUIRED_KEYS)
        missing = sorted({"name", "description"} - set(meta))
        if extra:
            keys_check.fail("非白名单键会被静默丢弃：" + "、".join(extra))
        elif missing:
            keys_check.fail("缺少必填键：" + "、".join(missing))
        else:
            keys_check.ok("、".join(sorted(meta)))
    checks.append(keys_check)

    # --- 5. body line count -----------------------------------------------
    body_check = Check("⑤ 正文行数 ≤ 500")
    body = match.group(2) if match else ""
    body_lines = len(body.rstrip("\n").split("\n")) if body.strip() else 0
    info["body_lines"] = body_lines
    if body_lines > MAX_BODY_LINES:
        body_check.fail(f"正文 {body_lines} 行，超过 {MAX_BODY_LINES} 行；"
                        f"长内容应拆进 references/")
    else:
        body_check.ok(f"{body_lines} 行（全文 {len(raw.splitlines())} 行）")
    checks.append(body_check)

    # --- 7. description ----------------------------------------------------
    desc_check = Check("⑦ description 非空且 ≤ 1000 字符")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        desc_check.fail("description 缺失（它是唯一的触发依据）")
    elif len(description) > MAX_DESCRIPTION_CHARS:
        desc_check.fail(f"{len(description)} 字符，超过 skill_description 列宽 1000")
    else:
        has_when = any(token in description for token in
                       ("当用户", "时使用", "不适用于", "适用"))
        detail = f"{len(description)} 字符"
        if has_when:
            desc_check.ok(detail + "，含何时用/何时不用")
        else:
            desc_check.warn(detail + "，未见「何时用/何时不用」表述")
    checks.append(desc_check)

    # --- 8. allowed-tools --------------------------------------------------
    tools_check = Check("⑧ allowed-tools 均为已知 MCP 工具")
    tools = meta.get("allowed-tools")
    if tools is None:
        tools_check.ok("未声明")
    elif not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        tools_check.fail("必须是字符串列表")
    else:
        unknown = sorted(set(tools) - MCP_TOOLS)
        if unknown:
            tools_check.fail("未在 MCP 工具清单中：" + "、".join(unknown))
        else:
            tools_check.ok(f"{len(tools)} 个：" + "、".join(tools))
    checks.append(tools_check)

    # --- 9. script_outputs -------------------------------------------------
    outputs_check = Check("⑨ script_outputs 契约")
    outputs = meta.get("script_outputs")
    if outputs is None:
        outputs_check.ok("未声明")
    elif not isinstance(outputs, dict):
        outputs_check.fail("必须是映射（键 = 相对技能根的脚本路径）")
    else:
        problems: list[str] = []
        mimes: list[str] = []
        for rel, spec in outputs.items():
            rel_posix = str(rel).replace("\\", "/").lstrip("./")
            if not isinstance(spec, dict):
                problems.append(f"{rel}：声明必须是映射")
                continue
            if spec.get("kind") != "file":
                problems.append(f"{rel}：kind 必须是 file")
            declared_mimes = spec.get("mime_types")
            if not isinstance(declared_mimes, list) or not declared_mimes:
                problems.append(f"{rel}：mime_types 必须是非空列表")
            else:
                mimes.extend(str(m) for m in declared_mimes)
            if not (skill_dir / rel_posix).is_file():
                problems.append(f"{rel}：声明了不存在的脚本")
            if not rel_posix.startswith("scripts/"):
                problems.append(f"{rel}：路径必须以 scripts/ 开头")
        if problems:
            outputs_check.fail("；".join(problems))
        else:
            outputs_check.ok(f"{len(outputs)} 个脚本，MIME：" + "、".join(sorted(set(mimes))))
    checks.append(outputs_check)

    # --- 10. referenced resources exist ------------------------------------
    ref_check = Check("⑩ 正文引用的资源均存在")
    missing_refs: list[str] = []
    found: list[str] = []
    for kind, rel in RESOURCE_PATTERN.findall(body):
        rel_posix = rel.replace("\\", "/").lstrip("./")
        target = skill_dir / rel_posix
        found.append(rel_posix)
        if not target.is_file():
            missing_refs.append(f"{rel_posix}（<{kind}>）")
        elif kind == "use_script" and isinstance(outputs, dict):
            if rel_posix not in {str(k).replace("\\", "/").lstrip("./") for k in outputs}:
                missing_refs.append(f"{rel_posix}（<use_script> 但未在 script_outputs 声明）")
    if missing_refs:
        ref_check.fail("；".join(missing_refs))
    else:
        ref_check.ok("、".join(found) or "无显式引用")
    checks.append(ref_check)

    # --- extra: discouraged files ------------------------------------------
    discouraged = sorted(p.name for p in skill_dir.iterdir()
                         if p.is_file() and p.name in DISCOURAGED_ROOT_FILES)
    if discouraged:
        warn = Check("⑪ 技能目录内无多余文档")
        warn.warn("doc 03 §1.5 建议技能目录只含必要文件：" + "、".join(discouraged))
        checks.append(warn)

    return checks, info


def execute_scripts(skill_dir: Path, script_outputs: dict) -> list[tuple[str, str, str]]:
    """Run each declared script with --selftest and verify the 8 artifact rules."""
    results: list[tuple[str, str, str]] = []
    for rel, spec in sorted(script_outputs.items()):
        rel_posix = str(rel).replace("\\", "/").lstrip("./")
        script = skill_dir / rel_posix
        if not script.is_file():
            results.append((rel_posix, FAIL, "脚本不存在"))
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--selftest"],
                capture_output=True, text=True, timeout=120, cwd=str(skill_dir),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            results.append((rel_posix, FAIL, f"执行失败：{exc}"))
            continue
        if proc.returncode != 0:
            results.append((rel_posix, FAIL,
                            f"退出码 {proc.returncode}：{(proc.stderr or '').strip()[:300]}"))
            continue

        problems = check_artifact_contract(proc.stdout, spec)
        if problems:
            results.append((rel_posix, FAIL, "；".join(problems)))
            continue
        payload = json.loads(proc.stdout.strip())
        names = ", ".join(f"{a['file_name']} ({a['file_size_bytes']} B)"
                          for a in payload["artifacts"])
        results.append((rel_posix, PASS, names))
    return results


def check_artifact_contract(stdout: str, spec: dict) -> list[str]:
    """The eight rules from custom-file-generation-skill.md, applied to stdout."""
    problems: list[str] = []
    text = stdout.strip()
    if not text or len(text.splitlines()) != 1:
        problems.append(f"stdout 必须恰好一行 JSON（实际 {len(text.splitlines())} 行）")
        return problems
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        problems.append(f"stdout 不是合法 JSON：{exc}")
        return problems
    if not isinstance(payload, dict):
        problems.append("stdout JSON 顶层必须是对象")
        return problems
    if payload.get("status") != "success":
        problems.append(f"status 必须是 success，实际 {payload.get('status')!r}")
        return problems
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        problems.append("artifacts 必须是数组")
        return problems
    if not artifacts:
        problems.append("artifacts 为空：没有可发布的附件")
        return problems

    declared = {str(m) for m in (spec.get("mime_types") or [])}
    for index, artifact in enumerate(artifacts):
        tag = f"artifacts[{index}]"
        if not isinstance(artifact, dict):
            problems.append(f"{tag} 必须是对象")
            continue
        if artifact.get("kind") != "file":
            problems.append(f"{tag}.kind 必须是 file")
        for key in ("absolute_path", "file_name", "mime_type"):
            value = artifact.get(key)
            if not isinstance(value, str) or not value:
                problems.append(f"{tag}.{key} 必须是非空字符串")
        size = artifact.get("file_size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            problems.append(f"{tag}.file_size_bytes 必须是非负整数（且不能是 bool）")
            continue
        path_text = artifact.get("absolute_path")
        if not isinstance(path_text, str) or not path_text:
            continue
        path = Path(path_text)
        if not path.is_file():
            problems.append(f"{tag}.absolute_path 指向的文件不存在：{path_text}")
            continue
        actual = path.stat().st_size
        if actual != size:
            problems.append(f"{tag}.file_size_bytes={size} 与磁盘实际 {actual} 不一致"
                            f"（必须先落盘再 stat）")
        mime = artifact.get("mime_type")
        if declared and isinstance(mime, str) and mime not in declared:
            problems.append(f"{tag}.mime_type={mime} 不在声明的 mime_types 内")
        if isinstance(artifact.get("file_name"), str) and \
                artifact["file_name"] != path.name:
            problems.append(f"{tag}.file_name 与 absolute_path 的文件名不一致")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nexent skill package static self-check."
    )
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent),
                        help="Directory holding the skill packages.")
    parser.add_argument("--execute", action="store_true",
                        help="Also run each declared script's --selftest and verify "
                             "the eight artifact-contract rules.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    skill_dirs = sorted(p for p in root.iterdir()
                        if p.is_dir() and (p / SKILL_FILE).is_file())

    print("=" * 78)
    print("Nexent Skill 包本地静态自检（_validate.py）")
    print("=" * 78)
    print(f"根目录 : {root}")
    print(f"技能数 : {len(skill_dirs)}")
    print(f"白名单 : {'、'.join(sorted(REQUIRED_KEYS))}")
    print(f"正文上限: {MAX_BODY_LINES} 行")
    print()

    if not skill_dirs:
        print("未找到任何含 SKILL.md 的技能目录。")
        return 1

    print(f"{'技能名':<24}{'正文':>6}{'文件':>6}{'脚本':>6}   结果")
    print("-" * 78)
    all_checks: list[tuple[Path, list[Check]]] = []
    any_failed = False
    for skill_dir in skill_dirs:
        checks, info = check_skill(skill_dir)
        all_checks.append((skill_dir, checks))
        failed = [c for c in checks if c.status == FAIL]
        warned = [c for c in checks if c.status == WARN]
        verdict = "FAIL" if failed else ("WARN" if warned else "PASS")
        any_failed = any_failed or bool(failed)
        print(f"{skill_dir.name:<24}{info['body_lines']:>6}{info['files']:>6}"
              f"{len(info['scripts']):>6}   {verdict}")

    print("-" * 78)
    passed = sum(1 for _, checks in all_checks
                 if not any(c.status == FAIL for c in checks))
    print(f"结果: {passed}/{len(skill_dirs)} 通过")
    print()

    for skill_dir, checks in all_checks:
        print(f"[{skill_dir.name}]")
        for check in checks:
            mark = {PASS: "✓ PASS", FAIL: "✗ FAIL", WARN: "! WARN"}[check.status]
            print(f"  {check.label:<34} {mark}  {check.detail}")
        print()

    if args.execute:
        print("=" * 78)
        print("产物契约运行时校验（--execute）：脚本 --selftest + 8 项 artifact 校验")
        print("=" * 78)
        for skill_dir, _ in all_checks:
            _, info = check_skill(skill_dir)
            text, _error = read_utf8_no_bom(skill_dir / SKILL_FILE)
            outputs: dict = {}
            if text:
                match = FRONTMATTER_PATTERN.match(text)
                if match:
                    try:
                        import yaml
                        loaded = yaml.safe_load(match.group(1))
                        if isinstance(loaded, dict) and isinstance(
                                loaded.get("script_outputs"), dict):
                            outputs = loaded["script_outputs"]
                    except Exception:                      # noqa: BLE001
                        outputs = {}
            if not outputs:
                print(f"[{skill_dir.name}] 无 script_outputs 声明，跳过")
                continue
            for rel, status, detail in execute_scripts(skill_dir, outputs):
                mark = "✓ PASS" if status == PASS else "✗ FAIL"
                print(f"[{skill_dir.name}] {rel}\n    {mark}  {detail}")
                if status == FAIL:
                    any_failed = True
        print()

    print("=" * 78)
    print("总判定:", "FAIL —— 存在未通过项" if any_failed else "全部通过")
    print("=" * 78)
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
