"""父循环派发一个取证任务，子循环交回出处和版本，父循环按自己的权限重读。"""

import json
from argparse import ArgumentParser
from dataclasses import dataclass, replace
from functools import partial

from biu_agent.identity import Problem
from examples.tickets.corpus import document, excerpt
from tutorials.s01_agent_loop.code import Call, Result, StepLimit, run_loop

TASK_SOURCES = {"acceptance"}


@dataclass(frozen=True)
class Finding:
    source: str
    version: str


def decide_child(messages, keyword="范围"):
    if isinstance(messages[-1], Result):
        for source, text in json.loads(messages[-1].value).items():
            if keyword in text:
                return source
        return ""
    return Call("child-read", "read_scope", {"id": "acceptance"})


def review_scope(question, parent_grants, steps, keyword):
    allowed = parent_grants.intersection(TASK_SOURCES)
    version = None

    def read(call):
        nonlocal version
        if call.name != "read_scope":
            raise ValueError("子任务只能读取验收范围")
        doc = document(call.args["id"], allowed)
        version = doc["sha256"]
        passages = {f"{doc['id']}#{key}": text for key, text in doc["sections"].items()}
        return json.dumps(passages, ensure_ascii=False)

    print("子任务收到", question, "授权", sorted(allowed))
    decide = partial(decide_child, keyword=keyword)
    source = run_loop(question, decide, read, steps=steps, observe=lambda event: print(" 子", event))
    return Finding(source, version) if source and version else None


def verify_finding(finding, current_grants):
    doc, separator, section = finding.source.partition("#")
    if doc not in TASK_SOURCES or not separator or not section:
        raise Problem(409, "child_source_outside", "返回的出处不在委派范围内。")
    row = excerpt(doc, section, current_grants)
    if row["sha256"] != finding.version:
        raise Problem(409, "child_version_changed", "父子两次读取的版本不一致。")
    return f"{row['text']} [{row['source']}]"


def decide_parent(messages):
    if isinstance(messages[-1], Result):
        return messages[-1].value
    return Call("parent-task", "review_scope", {"question": "验收说明覆盖哪些物品？"})


def main(parent_grants, steps, keyword, handoff):
    def delegate(call):
        if call.name != "review_scope":
            raise ValueError("未登记的委派工具")
        try:
            finding = review_scope(call.args["question"], parent_grants, steps, keyword)
        except StepLimit:
            return "子任务达到步数上限，本轮未取得验收依据。"
        except Problem as error:
            if error.code != "not_found":
                raise
            return "当前资料范围内缺少验收依据。"
        if not finding:
            return "子任务未找到说明验收范围的段落。"
        print("交接", finding.source, "文件版本", finding.version)
        current = parent_grants
        if handoff == "revoked":
            current = parent_grants - TASK_SOURCES
        elif handoff == "outside":
            finding = replace(finding, source="receiving#R2")
        elif handoff == "mismatch":
            finding = replace(finding, version="此前版本的模拟回执")
        try:
            return verify_finding(finding, current)
        except Problem as error:
            return "父任务复核未通过 " + error.code

    run_loop(
        "请帮我核对导向牌的验收依据。", decide_parent, delegate, observe=lambda event: print("父", event)
    )


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--receiving-only", action="store_true")
    parser.add_argument("--child-steps", type=int, default=2)
    parser.add_argument("--keyword", choices=["范围", "通电", "归档"], default="范围")
    parser.add_argument("--handoff", choices=["normal", "revoked", "outside", "mismatch"], default="normal")
    args = parser.parse_args()
    grants = {"receiving"} if args.receiving_only else {"receiving", "acceptance"}
    main(grants, args.child_steps, args.keyword, args.handoff)
