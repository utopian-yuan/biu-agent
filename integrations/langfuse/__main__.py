"""Export two evaluated tutorial turns as one repeatable Langfuse experiment."""

import argparse
import json
import sqlite3
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from dotenv import load_dotenv

from . import LangfuseExport, send_scores


def material_case(db, turn_id, agent_id):
    turn = db.execute(
        """SELECT t.*,s.agent_id FROM turns t JOIN sessions s ON s.id=t.session_id
        WHERE t.id=? AND s.deleted_at IS NULL""",
        (str(turn_id),),
    ).fetchone()
    if not turn or turn["agent_id"] != agent_id:
        raise ValueError("请选择相应智能体下的有效轮次。")
    spans = [
        dict(row)
        for row in db.execute("SELECT * FROM spans WHERE turn_id=? ORDER BY started_at", (str(turn_id),))
    ]
    if not spans:
        raise ValueError("该轮缺少调用链，请选择已完成的教程对话。")
    received = {r["source"] for r in json.loads(turn["evidence_json"])}
    required = {"receiving#R1", "receiving#R2"}
    if agent_id == "materials":
        required.add("acceptance#A1")
    errors = []
    if turn["status"] != "succeeded":
        errors.append("turn_failed")
    if not required <= received or not all(ref in (turn["answer_text"] or "") for ref in required):
        errors.append("missing_reference")
    if agent_id == "receiving" and any(r.startswith("acceptance#") for r in received):
        errors.append("scope_mismatch")
    report = {"case_id": agent_id, "passed": not errors, "errors": errors}
    return report, spans


def main():
    parser = argparse.ArgumentParser(description="比较材料助手与到货助手对同一教程问题的引文与权限")
    parser.add_argument("database", type=Path, help="本次实验的 core.sqlite3 路径")
    parser.add_argument("--materials-turn", required=True, type=UUID)
    parser.add_argument("--receiving-turn", required=True, type=UUID)
    parser.add_argument("--name", required=True, help="实验名称")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        cases = [
            material_case(db, args.materials_turn, "materials"),
            material_case(db, args.receiving_turn, "receiving"),
        ]
    run = str(uuid5(NAMESPACE_URL, f"biu/materials/{args.name}/{args.materials_turn}/{args.receiving_turn}"))
    exporter = LangfuseExport.from_env(timeout=10)
    try:
        scores = [exporter.item("biu-materials-v1", run, args.name, report, spans) for report, spans in cases]
    finally:
        drained = exporter.close(timeout=45)
    if not drained:
        raise SystemExit("部分调用链未送达，可用相同的轮次和名称重试。")
    send_scores(scores)
    print("已提交两个教程样本的调用链和引文检查评分。")


if __name__ == "__main__":
    main()
