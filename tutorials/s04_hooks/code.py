"""在工具执行的前后插入预算检查与事件记录。"""

from argparse import ArgumentParser

from biu_agent.identity import Problem
from tutorials.s01_agent_loop.code import decide_receipt, read_receipt, run_loop


class BudgetExceeded(Exception):
    pass


def with_hooks(execute, before, after):
    def dispatch(call):
        before(call)
        try:
            result = execute(call)
        except Exception:
            after(call, "error")
            raise
        after(call, "end")
        return result

    return dispatch


def main(budget, section):
    def before(call):
        nonlocal budget
        if budget <= 0:
            raise BudgetExceeded("预算耗尽，工具执行次数为 0")
        budget -= 1
        print(call.id, "start", call.name)

    def after(call, phase):
        print(call.id, phase, call.name)

    def decide(messages):
        action = decide_receipt(messages)
        if not isinstance(action, str):
            action.args["section"] = section
        return action

    dispatch = with_hooks(read_receipt, before, after)
    return run_loop("核对签收", decide, dispatch)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--budget", type=int, default=1)
    parser.add_argument("--section", default="R2")
    args = parser.parse_args()
    try:
        main(args.budget, args.section)
    except (BudgetExceeded, Problem) as error:
        print(error)
