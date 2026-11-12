"""The app's one stage table: AI-DLC Lean 0.3.1's 26 stages in Lean's order, the two steps brain records by other means
(审查: Lean's release review, review.md; 回写 Brain: brain reflect's branch), and the phases they sit in. Every view
that names, orders or counts a stage reads it: Lean's progress (lean_view), 需求详情, 交付总览 and the 需求看板.

It is a copy of Lean's own table (aidlc_lean.py STAGES), which the app never imports (lean_view.py says why), so
tests/test_stages_contract.py parses brain-kit's vendored aidlc_lean.py and fails when an id, phase, per-unit flag or
document differs. The names are brain-kit's (mrdesc.py STAGE_NAMES): the 变更说明 names a stage the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Optional, Tuple

Phase = Literal["init", "ideation", "inception", "construction", "release", "operations"]
GateKind = Literal["IDEATION", "REQUIREMENTS", "DESIGN", "INCEPTION", "PLAN", "RELEASE"]
# how the stage graph cuts a stage's rounds: doc (a Lean document stage), verify (each VERIFY), release (each
# `lean release`), review (each commit of review.md), reflect (each brain/<KEY>-reflect-<n> branch)
RoundKind = Literal["doc", "verify", "release", "review", "reflect"]


@dataclass(frozen=True)
class PhaseDef:
    key: Phase
    n: str        # the band's number: 0 1 2 3 ✓ 4
    title: str    # the band's title
    name: str     # how a sentence names it: 构思、运维两段整段不需要
    sub: str      # the band's subtitle, its main stages


@dataclass(frozen=True)
class StageDef:
    id: str
    n: str                          # the AI-DLC number shown with the name: 2.3 需求分析
    label: str
    phase: Phase
    per_unit: bool
    doc: Optional[str]              # its document under aidlc-docs/<intent>/, {unit} for a per-unit stage
    gates: Tuple[GateKind, ...] = ()   # the approvals whose files cover that document
    rounds: RoundKind = "doc"
    icon: str = ""
    lean: bool = True               # False: not a Lean stage, recorded by other means
    # Lean never asks for it (Ideation's stages, ci-pipeline, Operations': aidlc_lean.py open_stages): it is on a run's
    # way only once recorded, Ideation's also while Ideation is in use
    optional: bool = False


PHASES: Tuple[PhaseDef, ...] = (
    PhaseDef("init", "0", "初始化", "初始化", "认领 · 开工 · 带上 Brain 的知识"),
    PhaseDef("ideation", "1", "构思", "构思", "1.1 意图捕获 · 1.4 范围定义 · 1.7 审批交接"),
    PhaseDef("inception", "2", "启动", "启动", "2.3 需求分析 · 2.6 应用设计 · 2.7 拆分单元"),
    PhaseDef("construction", "3", "构建", "构建", "3.5 代码生成 · 3.6 构建测试 · 审查"),
    PhaseDef("release", "✓", "发布", "发布", "发布审批 · 产品验收"),
    PhaseDef("operations", "4", "运维 · 回写", "运维", "4.1 部署流水线 · 回写 Brain"),
)

STAGES: Tuple[StageDef, ...] = (
    StageDef("intent-capture", "1.1", "意图捕获", "ideation", False, "ideation/intent-capture/intent-statement.md",
             icon="idea", optional=True),
    StageDef("market-research", "1.2", "市场调研", "ideation", False, "ideation/market-research/market-research.md",
             icon="search", optional=True),
    StageDef("feasibility", "1.3", "可行性评估", "ideation", False, "ideation/feasibility/feasibility-assessment.md",
             icon="scale", optional=True),
    StageDef("scope-definition", "1.4", "范围定义", "ideation", False, "ideation/scope-definition/scope-document.md",
             icon="scope", optional=True),
    StageDef("team-formation", "1.5", "团队组建", "ideation", False, "ideation/team-formation/team-assessment.md",
             icon="team", optional=True),
    StageDef("rough-mockups", "1.6", "草图原型", "ideation", False, "ideation/rough-mockups/wireframes.md",
             icon="sketch", optional=True),
    StageDef("approval-handoff", "1.7", "审批交接", "ideation", False, "ideation/approval-handoff/initiative-brief.md",
             ("IDEATION",), icon="handoff", optional=True),
    StageDef("reverse-engineering", "2.1", "逆向工程", "inception", False, "inception/reverse-engineering/architecture.md",
             icon="search"),
    StageDef("requirements", "2.3", "需求分析", "inception", False, "inception/requirements/requirements.md",
             ("REQUIREMENTS", "INCEPTION"), icon="req"),
    StageDef("user-stories", "2.4", "用户故事", "inception", False, "inception/user-stories/stories.md", icon="stories"),
    StageDef("application-design", "2.6", "应用设计", "inception", False,
             "inception/application-design/application-design.md", ("DESIGN", "INCEPTION"), icon="design"),
    StageDef("units", "2.7", "拆分单元", "inception", False, "inception/application-design/unit-of-work.md", icon="units"),
    StageDef("functional-design", "3.1", "功能设计", "construction", True,
             "construction/{unit}/functional-design/functional-design.md", icon="design"),
    StageDef("nfr-design", "3.2", "非功能设计", "construction", True, "construction/{unit}/nfr-design/nfr-design.md",
             icon="shield"),
    StageDef("infrastructure-design", "3.4", "基础设施设计", "construction", True,
             "construction/{unit}/infrastructure-design/infrastructure-design.md", icon="server"),
    StageDef("code-generation", "3.5", "代码生成", "construction", True, "construction/plans/{unit}-code-generation-plan.md",
             ("PLAN",), icon="code"),
    StageDef("build-and-test", "3.6", "构建测试", "construction", False,
             "construction/build-and-test/build-and-test-summary.md", rounds="verify", icon="ci"),
    StageDef("review", "3.6", "审查", "construction", False, "construction/build-and-test/review.md", rounds="review",
             icon="review", lean=False),
    StageDef("ci-pipeline", "3.7", "CI 流水线", "construction", False, "construction/ci-pipeline/ci-config.md",
             icon="pipeline", optional=True),
    StageDef("release", "✓", "发布", "release", False, "operations/release.md", ("RELEASE",), rounds="release",
             icon="release"),
    StageDef("deployment-pipeline", "4.1", "部署流水线", "operations", False,
             "operations/deployment-pipeline/deployment-strategy.md", icon="pipeline", optional=True),
    StageDef("environment-provisioning", "4.2", "环境准备", "operations", False,
             "operations/environment-provisioning/environment-inventory.md", icon="server", optional=True),
    StageDef("deployment-execution", "4.3", "部署执行", "operations", False,
             "operations/deployment-execution/deployment-log.md", icon="rocket", optional=True),
    StageDef("observability-setup", "4.4", "可观测性", "operations", False,
             "operations/observability-setup/observability.md", icon="eye", optional=True),
    StageDef("incident-response", "4.5", "故障响应", "operations", False, "operations/incident-response/runbooks.md",
             icon="alarm", optional=True),
    StageDef("performance-validation", "4.6", "性能验证", "operations", False,
             "operations/performance-validation/load-test-plan.md", icon="gauge", optional=True),
    StageDef("feedback-optimization", "4.7", "反馈优化", "operations", False,
             "operations/feedback-optimization/feedback-loop.md", icon="loop", optional=True),
    StageDef("reflect", "4", "回写 Brain", "operations", False, None, rounds="reflect", icon="brain", lean=False),
)
BY_ID: Mapping[str, StageDef] = {s.id: s for s in STAGES}
LEAN: Tuple[StageDef, ...] = tuple(s for s in STAGES if s.lean)


def label(sid: object) -> str:
    """The name of a stage id read from a record; 一个阶段 for one this table does not know (a newer Lean's)."""
    s = BY_ID.get(str(sid))
    return s.label if s else "一个阶段"
