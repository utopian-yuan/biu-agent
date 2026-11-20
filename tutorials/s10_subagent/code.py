"""父 Agent 委派一项取证，子 Agent 用独立消息列表和受限工具运行。"""

import json
from argparse import ArgumentParser

from biu_agent.identity import Problem
from examples.tickets.corpus import document, excerpt
from tutorials.s01_agent_loop.code import Call, Result, StepLimit, run_loop


def decide_child(messages):
    if isinstance(messages[-1], Result):
        for source, text in json.loads(messages[-1].value).items():
            if "范围" in text:
                return source
        return ""
    return Call("child-read", "read_scope", {"id": "acceptance"})


def review_scope(question, parent_grants, steps):
    allowed = parent_grants.intersection({"acceptance"})

    def read(call):
        if call.name != "read_scope":
            raise ValueError("子任务只能读取验收范围")
        doc = document(call.args["id"], allowed)
        passages = {f"{doc['id']}#{key}": text for key, text in doc["sections"].items()}
        return json.dumps(passages, ensure_ascii=False)

    print("子任务收到", question, "授权", sorted(allowed))
    return run_loop(question, decide_child, read, steps=steps, observe=lambda event: print(" 子", event))


def decide_parent(messages):
    if isinstance(messages[-1], Result):
        return messages[-1].value
    return Call("parent-task", "review_scope", {"question": "验收说明覆盖哪些物品？"})


def main(parent_grants, steps):
    def delegate(call):
        if call.name != "review_scope":
            raise ValueError("未登记的委派工具")
        try:
            source = review_scope(call.args["question"], parent_grants, steps)
        except StepLimit:
            return "子任务达到步数上限，本轮未取得验收依据。"
        except Problem as error:
            if error.code != "not_found":
                raise
            return "当前资料范围内缺少验收依据。"
        if not source:
            return "子任务未找到说明验收范围的段落。"
        doc, section = source.split("#")
        row = excerpt(doc, section, parent_grants)
        return f"{row['text']} [{row['source']}]"

    run_loop(
        "请帮我核对导向牌的验收依据。", decide_parent, delegate, observe=lambda event: print("父", event)
    )


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--receiving-only", action="store_true")
    parser.add_argument("--child-steps", type=int, default=2)
    args = parser.parse_args()
    grants = {"receiving"} if args.receiving_only else {"receiving", "acceptance"}
    main(grants, args.child_steps)
