"""Persistent user questions shared by desktop API and the session MCP."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .store import now


class InteractionStore:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS interaction_questions (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                question TEXT NOT NULL, options TEXT NOT NULL, allow_other INTEGER NOT NULL,
                status TEXT NOT NULL, answer TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    @staticmethod
    def decode(row):
        value = dict(row)
        value["options"] = json.loads(value["options"])
        value["allow_other"] = bool(value["allow_other"])
        return value

    def questions(self, session_id):
        self.store.get_session(session_id)
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM interaction_questions WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
        return [self.decode(row) for row in rows]

    def present(self, session_id: str, question: str, options: list, allow_other: bool = True):
        self.store.get_session(session_id)
        if not question.strip() or len(question) > 5000 or not 1 <= len(options) <= 20:
            raise ValueError("问题不能为空，选项应为 1 至 20 项")
        choices = []
        for option in options:
            item = {"label": option, "value": option} if isinstance(option, str) else option
            if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                raise ValueError("选项必须是文本或带 label/value 的对象")
            choices.append({"label": item["label"][:1000], "value": str(item.get("value", item["label"]))[:1000]})
        if len({item["value"] for item in choices}) != len(choices):
            raise ValueError("选项 value 不能重复")
        serialized = json.dumps(choices, ensure_ascii=False)
        with self.store.connection() as db:
            existing = db.execute("SELECT * FROM interaction_questions WHERE session_id=? AND question=? AND options=? AND status='pending'", (session_id, question, serialized)).fetchone()
            if existing:
                return self.decode(existing)
            identifier, timestamp = uuid.uuid4().hex, now()
            db.execute("INSERT INTO interaction_questions VALUES (?,?,?,?,?,?,?,?,?)", (identifier, session_id, question.strip(), serialized, int(allow_other), "pending", "", timestamp, timestamp))
            row = db.execute("SELECT * FROM interaction_questions WHERE id=?", (identifier,)).fetchone()
        return self.decode(row)

    def answer(self, session_id: str, identifier: str, answer: str):
        self.store.get_session(session_id)
        if not answer.strip() or len(answer) > 10000:
            raise ValueError("回答不能为空或超过 10000 字符")
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM interaction_questions WHERE id=? AND session_id=?", (identifier, session_id)).fetchone()
            if not row:
                raise KeyError("问题不存在")
            question = self.decode(row)
            if question["status"] != "pending":
                raise ValueError("该问题已经回答")
            if not question["allow_other"] and answer not in {item["value"] for item in question["options"]}:
                raise ValueError("请选择提供的选项")
            db.execute("UPDATE interaction_questions SET status='answered',answer=?,updated_at=? WHERE id=?", (answer.strip(), now(), identifier))
        result = {**question, "status": "answered", "answer": answer.strip()}
        # Answers are also part of conversational history; the question row remains authoritative.
        self.store.add_message(session_id, "user", f"回答「{question['question']}」：{answer.strip()}", question_id=identifier)
        return result


class QuestionAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)


def register_interactions(app):
    service = InteractionStore(app.state.store)
    app.state.interactions = service
    router = APIRouter(prefix="/api/sessions")

    @router.get("/{session_id}/questions")
    def questions(session_id: str):
        try:
            return {"items": service.questions(session_id)}
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.post("/{session_id}/questions/{identifier}/answer")
    def answer(session_id: str, identifier: str, body: QuestionAnswer):
        try:
            question = service.answer(session_id, identifier, body.answer)
            return {**question, "next_message": f"已回答「{question['question']}」：{question['answer']}。请读取已回答的问题并继续任务。"}
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    app.include_router(router)
