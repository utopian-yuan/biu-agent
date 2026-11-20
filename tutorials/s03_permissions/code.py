"""目录、直接读取和旧会话各有自己的检查点。"""

from argparse import ArgumentParser
from dataclasses import replace

from biu_agent.identity import Principal, Problem
from examples.tickets.corpus import catalog, excerpt


def inspect_scope(deployment, selected):
    allowed = deployment.intersection(selected)
    print("目录", [doc["id"] for doc in catalog(allowed)])
    try:
        row = excerpt("acceptance", "A2", allowed)
        print("直接读取", row["source"], row["text"])
    except Problem as error:
        print("直接读取", error.status, error.code)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    deployment = {"receiving", "acceptance"}
    profile = {"receiving", "acceptance"}
    original = Principal("展具组", "小舟", "1", "")
    current = original
    if args.revoke:
        deployment.remove("acceptance")
        current = replace(original, access_epoch="2")
    inspect_scope(deployment, profile)
    try:
        original.check(current)
        print("原会话可继续")
    except Problem as error:
        print("原会话", error.status, error.code)
