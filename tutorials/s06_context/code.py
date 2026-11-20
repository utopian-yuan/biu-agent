"""把历史指代与本轮证据送到不同的检查点。"""

from argparse import ArgumentParser
from tempfile import TemporaryDirectory

from biu_agent.agents.context import messages_for
from biu_agent.identity import Principal, Problem
from biu_agent.session import TurnContext
from examples.tickets.accounts import Accounts
from examples.tickets.corpus import excerpt
from examples.tickets.workspace import Workspace


def main(workspace, reuse_citation):
    ctx = TurnContext(
        principal=Principal("展具组", "小舟", "1", ""),
        session_id="练习会话",
        turn_id="第二问",
        input_text="那导向牌呢？",
        previous_display=None,
        visible_set_id=None,
        history=[("样品灯验收了吗？", "样品灯验收通过。[acceptance#A1]")],
    )
    ctx.profile["sources"] = {"receiving"}
    print("本轮模型输入", messages_for(ctx, questions_only=True)[0].get_text_content())
    row = excerpt("receiving", "R2", ctx.profile["sources"])
    ctx.evidence[row["source"]] = row
    ref = "acceptance#A1" if reuse_citation else row["source"]
    candidate = f"{row['text']} [{ref}]"
    try:
        print("发布", workspace.verify(ctx, candidate))
    except Problem as error:
        print("拒绝发布", error.code)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--reuse-citation", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory() as directory:
        main(Workspace(Accounts(directory)), args.reuse_citation)
