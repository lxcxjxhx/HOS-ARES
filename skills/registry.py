# -*- coding: utf-8 -*-
"""
skills/registry.py — Skill/Tool Registry（技能/工具注册中心）

职责：
    - 扫描 skills 根目录，发现 .skill 声明式插件
    - 解析每个插件的 manifest（name / tools / trigger / workflow）
    - 提供按名称获取、按任务类型（trigger）匹配、列出全部技能的能力
    - 供上层（gateway 调度链 / Agent）按需查找并触发技能

设计说明：
    HOS-ARES 通过 Skill/Tool Registry 以插件方式接入安全 Agent，
    不在代码中写死工具。每个技能是一个 .skill 目录，内含 manifest.yaml
    声明式元数据，例如 security-audit.skill/manifest.yaml。

    manifest 格式（YAML 子集，见 _parse_yaml_subset）：
        name:   技能唯一名称
        tools:  该技能调用的底层工具列表（Security Tool Layer 中的工具）
        trigger: 可触发的任务类型列表（与 gateway 的任务类型对应）
        workflow: 可选，该技能对应的编排流程名

依赖：
    零第三方依赖（自带最小 YAML 子集解析，保持脚手架可运行）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# manifest 文件名约定（按优先级排列，存在任一即采用）
MANIFEST_FILENAMES = ("manifest.yaml", "manifest.yml", "SKILL.yaml", "SKILL.yml")

# 技能目录后缀约定（目录名以 .skill 结尾视为技能插件）
SKILL_SUFFIX = ".skill"


@dataclass
class Skill:
    """单个技能插件的元数据表示。"""

    name: str                          # 技能唯一名称（来自 manifest.name）
    tools: List[str] = field(default_factory=list)     # 依赖的底层工具列表
    trigger: List[str] = field(default_factory=list)   # 可触发的任务类型列表
    workflow: Optional[str] = None     # 可选：对应编排流程名
    path: Optional[str] = None         # 技能目录绝对路径
    manifest_path: Optional[str] = None  # manifest 文件绝对路径

    def matches(self, task_type: str) -> bool:
        """判断该技能是否可由指定任务类型触发（trigger 不区分大小写）。"""
        if not task_type:
            return False
        return task_type.lower() in [t.lower() for t in self.trigger]

    def __str__(self) -> str:  # 便于调试打印
        return (
            f"Skill(name={self.name!r}, tools={self.tools}, "
            f"trigger={self.trigger}, workflow={self.workflow!r})"
        )


class SkillRegistry:
    """Skill/Tool 注册中心：负责发现、解析、索引声明式技能插件。"""

    def __init__(self, skills_root: Optional[str] = None) -> None:
        self.skills_root: Optional[str] = skills_root
        self._skills: Dict[str, Skill] = {}
        if skills_root:
            self.discover(skills_root)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def register(self, skill_dir: str) -> Optional[Skill]:
        """
        注册单个技能目录。

        参数：
            skill_dir: 技能目录绝对路径（需含 manifest 文件）。

        返回：
            成功解析并注册返回 Skill，否则返回 None。
        """
        manifest_path = self._find_manifest(skill_dir)
        if not manifest_path:
            return None
        data = self._load_manifest(manifest_path)
        if not data or not data.get("name"):
            return None
        skill = Skill(
            name=data["name"],
            tools=list(data.get("tools", []) or []),
            trigger=list(data.get("trigger", []) or []),
            workflow=data.get("workflow"),
            path=os.path.abspath(skill_dir),
            manifest_path=manifest_path,
        )
        self._skills[skill.name] = skill
        return skill

    def discover(self, skills_root: str) -> List[Skill]:
        """
        扫描技能根目录，注册所有 .skill 插件。

        参数：
            skills_root: skills 根目录路径。

        返回：
            本次新注册成功的 Skill 列表。
        """
        self.skills_root = skills_root
        found: List[Skill] = []
        if not os.path.isdir(skills_root):
            return found
        for entry in sorted(os.listdir(skills_root)):
            entry_path = os.path.join(skills_root, entry)
            if not os.path.isdir(entry_path):
                continue
            # 仅处理 .skill 目录（或含 manifest 的目录）
            if not (entry.endswith(SKILL_SUFFIX) or self._find_manifest(entry_path)):
                continue
            skill = self.register(entry_path)
            if skill:
                found.append(skill)
        return found

    def get(self, name: str) -> Optional[Skill]:
        """按名称获取已注册技能，未找到返回 None。"""
        return self._skills.get(name)

    def find_by_trigger(self, task_type: str) -> Optional[Skill]:
        """
        按任务类型（trigger）匹配技能。

        参数：
            task_type: 任务类型字符串（如 code_review / vulnerability）。

        返回：
            首个匹配该任务类型的 Skill，未命中返回 None。
        """
        for skill in self._skills.values():
            if skill.matches(task_type):
                return skill
        return None

    def list(self) -> List[Skill]:
        """列出所有已注册技能。"""
        return list(self._skills.values())

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------
    def _find_manifest(self, skill_dir: str) -> Optional[str]:
        """在技能目录中查找 manifest 文件。"""
        for fname in MANIFEST_FILENAMES:
            p = os.path.join(skill_dir, fname)
            if os.path.isfile(p):
                return p
        return None

    def _load_manifest(self, manifest_path: str) -> Dict:
        """
        解析声明式插件 manifest（最小 YAML 子集）。

        支持：
            - 顶层 `key: value` 键值对
            - 键后跟缩进列表 `- item`
            - 行内注释 `# ...`
            - 字符串引号剥离（" " 与 ' '）

        真实环境建议替换为 PyYAML（yaml.safe_load），此处为保持
        零依赖脚手架而自带精简解析。
        """
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return {}
        return self._parse_yaml_subset(text)

    def _parse_yaml_subset(self, text: str) -> Dict:
        """精简 YAML 解析（仅覆盖本脚手架 manifest 语法）。"""
        result: Dict = {}
        current_key: Optional[str] = None
        for raw in text.splitlines():
            # 去掉行内注释并去除首尾空白
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            # 列表项：- item
            if line.startswith("- "):
                item = line[2:].strip().strip('"').strip("'")
                if current_key is not None:
                    result.setdefault(current_key, []).append(item)
                continue
            # 键值对：key: value
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                current_key = key
                if value:
                    result[key] = value
                else:
                    # 空值键，后续行可能是列表项
                    result.setdefault(key, [])
            # 其他行（如嵌套对象）在本脚手架中忽略
        return result


if __name__ == "__main__":
    # 验证示例：以本文件所在目录（skills/）为根进行发现与查询
    root = os.path.dirname(os.path.abspath(__file__))
    reg = SkillRegistry(root)

    print("=" * 50)
    print("已注册技能:")
    for s in reg.list():
        print("  -", s)
    print("=" * 50)

    for name in ("security_audit", "not_exist"):
        s = reg.get(name)
        print(f"get('{name}') -> {s.name if s else None}")

    for task in ("code_review", "vulnerability", "general"):
        s = reg.find_by_trigger(task)
        print(f"find_by_trigger('{task}') -> {s.name if s else None}")
