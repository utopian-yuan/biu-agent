"""权限实验：筛选模型可见字段，绑定一次批准，并在写入前重查权限。"""

from argparse import ArgumentParser
from dataclasses import dataclass, replace
from uuid import uuid4

from pydantic import ConfigDict, Field

from biu_agent.identity import Principal, Problem, hidden
from biu_agent.tools import Inputs
from examples.tickets.corpus import catalog, excerpt


PROJECTS = {
    ("展具组", "P-101"): {"name": "设备台账整理", "progress": "待补验收资料", "budget": 1600},
    ("展具组", "P-104"): {"name": "活动看板", "progress": "准备中", "budget": 500},
}
OWNER = Principal("展具组", "小舟", "1", "")
NOTE_TEXT = "继续跟进设备 A17、A18 的验收资料。"


def inspect_scope(deployment, selected):
    allowed = deployment.intersection(selected)
    print("目录", [doc["id"] for doc in catalog(allowed)])
    try:
        row = excerpt("acceptance", "A2", allowed)
        print("直接读取", row["source"], row["text"])
    except Problem as error:
        print("直接读取", error.status, error.code)


def decision(actor, grants, project, action):
    if (actor.tenant_id, project) not in PROJECTS or project not in grants:
        return "DENY"
    return {"read": "ALLOW", "append_note": "ASK"}.get(action, "DENY")


def model_view(actor, grants, project):
    if decision(actor, grants, project, "read") != "ALLOW":
        raise hidden()
    row = PROJECTS[actor.tenant_id, project]
    return {"id": project, "name": row["name"], "progress": row["progress"]}


class Note(Inputs):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)
    project: str = Field(pattern=r"^P-[0-9]{3}$")
    text: str = Field(min_length=1, max_length=240)


@dataclass(frozen=True)
class Pending:
    operation_id: str
    call_id: str
    owner: Principal
    note: Note
    policy_version: int
    expires_at: int


def request_note(inbox, actor, grants, call_id, raw, *, now, policy_version):
    note = Note.model_validate(raw)
    if decision(actor, grants, note.project, "append_note") != "ASK":
        raise hidden()
    item = Pending(uuid4().hex, call_id, replace(actor, credential=""), note, policy_version, now + 300)
    inbox[item.operation_id] = item
    return item


def consume_approval(inbox, operation_id, call_id, actor, grants, actual, *, now, policy_version, approved):
    item = inbox.get(operation_id)
    if not item or item.owner.scope[:2] != actor.scope[:2] or item.call_id != call_id:
        raise hidden()
    # 一次性待办由同一宿主消费。其他身份或调用无法替原申请人消耗它。
    del inbox[operation_id]
    item.owner.check(actor)
    if decision(actor, grants, actual.project, "append_note") != "ASK":
        raise hidden()
    if now >= item.expires_at:
        raise Problem(409, "expired", "这份待办已到期。")
    if actual != item.note or policy_version != item.policy_version:
        raise Problem(409, "review_changed", "内容或规则已改变，请重新申请。")
    if not approved:
        raise Problem(409, "not_approved", "宿主没有取得本次批准。")
    return item.note


def review_demo(case):
    inbox, ledger = {}, []
    grants = {"P-101"}
    print("发给模型的字段", model_view(OWNER, grants, "P-101"))
    print("读取 P-104", decision(OWNER, grants, "P-104", "read"))
    item = request_note(
        inbox,
        OWNER,
        grants,
        "call-1",
        {"project": "P-101", "text": "  " + NOTE_TEXT},
        now=0,
        policy_version=1,
    )
    print("ASK", item.note.project, item.note.text, "创建后 300 秒内有效")
    print("等待时台账", len(ledger), "条")
    actor = replace(OWNER, access_epoch="3") if case == "restored" else OWNER
    actual = Note(project="P-101", text=NOTE_TEXT.replace("A18", "A19")) if case == "edited" else item.note
    for attempt in (1, 2):
        try:
            note = consume_approval(
                inbox,
                item.operation_id,
                item.call_id,
                actor,
                set() if case == "revoked" else grants,
                actual,
                now=300 if case == "expired" else 120,
                policy_version=2 if case == "policy" else 1,
                approved=case != "refused",
            )
            ledger.append((note.project, note.text))
            print("恢复", attempt, "已写入")
        except Problem as error:
            print("恢复", attempt, error.code)
    print("台账回读", len(ledger), "条", ledger)


if __name__ == "__main__":
    parser = ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--revoke", action="store_true", help="收回验收说明并推进权限版本")
    modes.add_argument("--profile-only", action="store_true", help="仅改变实验用智能体选材")
    modes.add_argument(
        "--review", choices=["approve", "refused", "revoked", "restored", "edited", "expired", "policy"]
    )
    args = parser.parse_args()
    if args.review:
        review_demo(args.review)
    else:
        deployment = {"receiving"} if args.revoke else {"receiving", "acceptance"}
        profile = {"receiving"} if args.profile_only else {"receiving", "acceptance"}
        current = replace(OWNER, access_epoch="2") if args.revoke else OWNER
        inspect_scope(deployment, profile)
        try:
            OWNER.check(current)
            print("原会话可继续")
        except Problem as error:
            print("原会话", error.status, error.code)
