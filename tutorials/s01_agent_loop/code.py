"""单次模型决策只产生一个动作，循环负责执行并回填。"""

from argparse import ArgumentParser
from dataclasses import dataclass

from examples.tickets.corpus import excerpt


@dataclass
class Call:
    id: str
    name: str
    args: dict


@dataclass
class Result:
    call_id: str
    value: str


class StepLimit(Exception):
    pass


def run_loop(question, decide, dispatch, *, steps=3, observe=print):
    messages = [question]
    for _ in range(steps):
        action = decide(messages)
        messages.append(action)
        observe(action)
        if isinstance(action, str):
            return action
        result = Result(action.id, dispatch(action))
        messages.append(result)
        observe(result)
    raise StepLimit(f"{steps} 次模型决策已用完")


def decide_receipt(messages):
    last = messages[-1]
    if isinstance(last, Result):
        return last.value
    return Call("read-1", "read_material", {"id": "receiving", "section": "R2"})


def read_receipt(call):
    if call.name != "read_material":
        raise ValueError("未登记的工具")
    row = excerpt(call.args["id"], call.args["section"], {"receiving"})
    return f"{row['text']} [{row['source']}]"


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--steps", type=int, default=3)
    args = parser.parse_args()
    try:
        run_loop("导向牌签收了吗？", decide_receipt, read_receipt, steps=args.steps)
    except StepLimit as error:
        print(error)
