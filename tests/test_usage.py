from fastapi import FastAPI
from fastapi.testclient import TestClient

from architecture_workbench.store import Store
from architecture_workbench.usage import UsageService,credits_from,register_usage,number


def stamp(store,message,at):
    with store.connection() as db:db.execute("UPDATE messages SET created_at=? WHERE id=?",(at,message["id"]))


def test_unknown_values_remain_none_and_questions_are_not_tasks(tmp_path):
    store=Store(tmp_path);session=store.create_session("Unknown")
    store.add_message(session["id"],"user","manual context")
    store.add_message(session["id"],"user","question answer",question_id="q1")
    store.add_message(session["id"],"user","run",run_id="unknown",provider="codex")
    store.add_message(session["id"],"system","failed",run_id="unknown",usage={"credits":[],"token_usage":{}})
    value=UsageService(store).get()
    assert value["totals"]["tasks"]==1
    assert value["totals"]["input_tokens"] is None and value["totals"]["native_media_credits"] is None
    assert value["totals"]["currency_cost"] is None and value["totals"]["balance"] is None
    assert value["items"][0]["model"] is None


def test_run_snapshots_and_receipt_ids_are_not_double_counted(tmp_path):
    store=Store(tmp_path);session=store.create_session("Receipts",provider="codebuddy")
    payload={"receipts":[{"id":"a","credit":5.71},{"id":"a","credit":5.71},{"id":"b","credit":5.71}],
             "token_usage":{"input_tokens":100,"output_tokens":20,"cached_input_tokens":40}}
    store.add_message(session["id"],"user","run",run_id="same",provider="codebuddy",model="actual-model")
    store.add_message(session["id"],"assistant","first copy",run_id="same",usage=payload)
    store.add_message(session["id"],"assistant","duplicate terminal snapshot",run_id="same",usage=payload)
    value=UsageService(store).get()
    assert value["totals"]["tasks"]==1 and value["totals"]["input_tokens"]==100
    assert round(value["totals"]["native_media_credits"],2)==11.42
    assert value["totals"]["total_tokens"] is None  # no arithmetic guess about supplier totals
    assert value["items"][0]["model"]=="actual-model"
    assert credits_from({"credits":[5.71,5.71]})==[5.71,5.71]


def test_real_zero_is_known_but_invalid_metrics_are_ignored(tmp_path):
    store=Store(tmp_path);session=store.create_session("Zeros")
    store.add_message(session["id"],"assistant","receipt",run_id="zero",provider="codex",
        usage={"credit":0,"token_usage":{"input_tokens":0,"output_tokens":False,"total_tokens":-1}})
    value=UsageService(store).get()
    assert value["totals"]["input_tokens"]==0 and value["totals"]["native_media_credits"]==0
    assert value["totals"]["output_tokens"] is None and value["totals"]["total_tokens"] is None
    assert number(float('inf')) is None


def test_provider_and_phoenix_date_filters_use_receipt_time(tmp_path):
    store=Store(tmp_path);session=store.create_session("Dates")
    previous=store.add_message(session["id"],"assistant","a",run_id="a",provider="codex",usage={"token_usage":{"input_tokens":10}})
    current=store.add_message(session["id"],"assistant","b",run_id="b",provider="codebuddy",usage={"credits":[5.71]})
    stamp(store,previous,"2026-10-04T04:00:00+00:00")
    stamp(store,current,"2026-10-04T12:00:00+00:00")
    all_values=UsageService(store).get(timezone_name="America/Phoenix")
    assert {item["date"] for item in all_values["items"]}=={"2026-10-03","2026-10-04"}
    today=UsageService(store).get("codebuddy","2026-10-04","2026-10-05","America/Phoenix")
    assert today["totals"]["tasks"]==1 and today["items"][0]["run_id"]=="b"
    assert today["totals"]["input_tokens"] is None and today["totals"]["native_media_credits"]==5.71


def test_current_model_setting_never_invents_historical_model(tmp_path):
    store=Store(tmp_path);session=store.create_session("Model")
    store.save_settings({"models":{"codex":"current-setting","codebuddy":""}})
    store.add_message(session["id"],"assistant","old",provider="codex",run_id="old",usage={"token_usage":{"input_tokens":1}})
    value=UsageService(store).get()
    assert value["current_models"]["codex"]=="current-setting"
    assert value["items"][0]["model"] is None


def test_usage_api_is_read_only_and_does_not_deduct_membership(tmp_path):
    app=FastAPI();app.state.store=Store(tmp_path);register_usage(app)
    app.state.store.save_settings({"member_points":42})
    session=app.state.store.create_session("API")
    app.state.store.add_message(session["id"],"assistant","receipt",run_id="api",provider="codebuddy",usage={"credits":[5.71]})
    with TestClient(app) as client:
        response=client.get("/api/usage")
        assert response.status_code==200 and response.json()["totals"]["native_media_credits"]==5.71
        assert client.get("/api/usage",params={"provider":"remote"}).status_code==422
        assert client.get("/api/usage",params={"since":"2030-01-02","until":"2030-01-01"}).status_code==422
    assert app.state.store.get_settings()["member_points"]==42
