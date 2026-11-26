"""User-owned persistent sessions, run traces and evaluation records."""

import base64
import copy
import json
import math
import os
import secrets
import sqlite3
import time
import zlib
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException


def clean(value):
    if isinstance(value, dict):
        return {
            k: clean(v)
            for k, v in value.items()
            if k.lower() not in {"route_token", "password", "authorization", "cookie", "callback_token"}
        }
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def pack(value):
    data = zlib.compress(json.dumps(clean(value), ensure_ascii=False, allow_nan=False).encode())
    if len(data) > 300_000:
        raise ValueError("Record exceeds the storage limit")
    return data


def unpack(value):
    return json.loads(zlib.decompress(bytes(value)))


class Records:
    """Identical key/query semantics for local SQLite and dedicated AWS DynamoDB."""

    def __init__(self, *, table=None, path=None):
        self.table = table or os.getenv("GATEWAY_DATA_TABLE")
        if self.table:
            import boto3
            from botocore.config import Config

            self.db = boto3.client(
                "dynamodb",
                region_name=os.getenv("AWS_REGION", "us-east-1"),
                config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
            )
        else:
            self.path = Path(path or os.getenv("GATEWAY_DB_PATH", ".data/gateway.sqlite3"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS records "
                    "(pk TEXT, sk TEXT, payload BLOB NOT NULL, expires INTEGER NOT NULL, "
                    "PRIMARY KEY(pk, sk))"
                )
            self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def put_many(self, rows, *, create=False):
        now = int(time.time())
        encoded = [
            (pk, sk, pack(data), data.get("expires_at", now + 180 * 86400)) for pk, sk, data in rows
        ]
        if self.table:
            self.db.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": {
                                "pk": {"S": pk},
                                "sk": {"S": sk},
                                "payload": {"B": payload},
                                "expires_at": {"N": str(expires)},
                            },
                            **(
                                {"ConditionExpression": "attribute_not_exists(pk)"}
                                if create
                                else {}
                            ),
                        }
                    }
                    for pk, sk, payload, expires in encoded
                ]
            )
        else:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for row in encoded:
                    db.execute(
                        ("INSERT" if create else "INSERT OR REPLACE")
                        + " INTO records VALUES (?, ?, ?, ?)",
                        row,
                    )

    def put(self, pk, sk, data, *, create=False):
        self.put_many([(pk, sk, data)], create=create)

    def get(self, pk, sk):
        if self.table:
            row = self.db.get_item(
                TableName=self.table,
                Key={"pk": {"S": pk}, "sk": {"S": sk}},
                ConsistentRead=True,
            ).get("Item")
            return (
                unpack(row["payload"]["B"])
                if row and int(row["expires_at"]["N"]) > time.time()
                else None
            )
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM records WHERE pk=? AND sk=? AND expires>?",
                (pk, sk, int(time.time())),
            ).fetchone()
        return unpack(row[0]) if row else None

    def acquire_lease(self, pk, sk, owner, seconds=600):
        """One active writer per session, including across ECS workers."""
        now = int(time.time())
        item = {"owner": owner, "expires_at": now + seconds}
        if self.table:
            from botocore.exceptions import ClientError

            try:
                self.db.put_item(
                    TableName=self.table,
                    Item={"pk": {"S": pk}, "sk": {"S": sk}, "owner": {"S": owner},
                          "payload": {"B": pack(item)}, "expires_at": {"N": str(now + seconds)}},
                    ConditionExpression="attribute_not_exists(pk) OR expires_at <= :now",
                    ExpressionAttributeValues={":now": {"N": str(now)}},
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    raise HTTPException(409, "当前会话仍有请求处理中，请完成后继续或新建会话。") from exc
                raise
        else:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT expires FROM records WHERE pk=? AND sk=?", (pk, sk)).fetchone()
                if row and row[0] > now:
                    raise HTTPException(409, "当前会话仍有请求处理中，请完成后继续或新建会话。")
                db.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?)",
                           (pk, sk, pack(item), now + seconds))

    def release_lease(self, pk, sk, owner):
        if self.table:
            from botocore.exceptions import ClientError

            try:
                self.db.delete_item(
                    TableName=self.table, Key={"pk": {"S": pk}, "sk": {"S": sk}},
                    ConditionExpression="#owner = :owner",
                    ExpressionAttributeNames={"#owner": "owner"},
                    ExpressionAttributeValues={":owner": {"S": owner}},
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        else:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT payload FROM records WHERE pk=? AND sk=?", (pk, sk)).fetchone()
                if row and unpack(row[0])["owner"] == owner:
                    db.execute("DELETE FROM records WHERE pk=? AND sk=?", (pk, sk))

    def query(self, pk, prefix, *, limit=50, cursor=None, lower=None, upper=None, reverse=True):
        if not 1 <= limit <= 5000:
            raise ValueError("Invalid page size")
        after = None
        if cursor:
            try:
                after = base64.urlsafe_b64decode(cursor.encode()).decode()
                if not after.startswith(prefix) or len(after) > 180:
                    raise ValueError()
            except (ValueError, UnicodeError) as exc:
                raise HTTPException(422, "历史分页标识无效") from exc
        lower, upper = lower or prefix, upper or prefix + "\uffff"
        if self.table:
            options = {}
            if after:
                options["ExclusiveStartKey"] = {"pk": {"S": pk}, "sk": {"S": after}}
            response = self.db.query(
                TableName=self.table,
                KeyConditionExpression="pk=:pk AND sk BETWEEN :lo AND :hi",
                ExpressionAttributeValues={
                    ":pk": {"S": pk},
                    ":lo": {"S": lower},
                    ":hi": {"S": upper},
                },
                ScanIndexForward=not reverse,
                Limit=limit,
                ConsistentRead=True,
                **options,
            )
            rows = [
                (r["sk"]["S"], unpack(r["payload"]["B"]))
                for r in response["Items"]
                if int(r["expires_at"]["N"]) > time.time()
            ]
            last = response.get("LastEvaluatedKey", {}).get("sk", {}).get("S")
        else:
            params = [pk, lower, upper, int(time.time())]
            condition = ""
            if after:
                condition = " AND sk" + ("<" if reverse else ">") + "?"
                params.append(after)
            with self.connect() as db:
                result = db.execute(
                    "SELECT sk,payload FROM records WHERE pk=? AND sk BETWEEN ? AND ? "
                    "AND expires>?"
                    + condition
                    + " ORDER BY sk "
                    + ("DESC" if reverse else "ASC")
                    + " LIMIT ?",
                    (*params, limit + 1),
                ).fetchall()
            rows = [(sk, unpack(payload)) for sk, payload in result[:limit]]
            last = rows[-1][0] if len(result) > limit else None
        return {
            "items": [v for _, v in rows],
            "next_cursor": base64.urlsafe_b64encode(last.encode()).decode() if last else None,
        }


def percentile(values, fraction):
    values = sorted(v for v in values if isinstance(v, (float, int)) and math.isfinite(v))
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


class History:
    def __init__(self, records=None):
        self.records = records or Records()

    @staticmethod
    def user_key(user):
        return "USER#" + user

    @staticmethod
    def run_key(user, run_id):
        return f"RUN#{user}#{run_id}"

    def create_session(self, user, title):
        now = int(time.time() * 1000)
        item = {"id": secrets.token_hex(12), "title": title, "created_at": now}
        self.records.put(self.user_key(user), "SESSION#" + item["id"], item, create=True)
        return item

    def session(self, user, session_id):
        item = self.records.get(self.user_key(user), "SESSION#" + session_id)
        if not item:
            raise HTTPException(404, "会话不存在")
        return item

    def sessions(self, user):
        result = self.records.query(self.user_key(user), "SESSION#", limit=500)
        result["items"].sort(key=lambda x: x["created_at"], reverse=True)
        return result

    def begin(self, user, body, run_id):
        session_id = body.session_id
        if session_id:
            session = self.session(user, session_id)
            session["expires_at"] = int(time.time()) + 180 * 86400
            self.records.put(self.user_key(user), "SESSION#" + session_id, session)
        else:
            session_id = self.create_session(user, "未命名会话")["id"]
        now = int(time.time() * 1000)
        item = {
            "id": run_id,
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "status": "running",
            "mode": body.mode,
            "provider": body.provider,
            "execute_target": body.execute_target,
            "template_id": body.template_id,
            "task_preview": body.task.text[:160],
            "event_count": 0,
            "catalog_version": body.catalog_version,
            "ranking_version": body.ranking_version,
            "policy_version": body.policy_version,
            "request": body.model_dump(mode="json"),
            "review": None,
            "expires_at": int(time.time()) + 180 * 86400,
        }
        rows = self.summary_rows(user, item)
        if body.client_request_id:
            rows.append(
                (
                    self.user_key(user),
                    "IDEMP#" + body.client_request_id,
                    {"run_id": run_id, "expires_at": int(time.time()) + 86400},
                )
            )
        try:
            self.records.put_many(rows, create=True)
        except Exception as exc:
            duplicate = (
                self.records.get(self.user_key(user), "IDEMP#" + body.client_request_id)
                if body.client_request_id
                else None
            )
            if duplicate:
                raise HTTPException(
                    409, "请求已提交，请在历史记录中查看：" + duplicate["run_id"]
                ) from exc
            raise
        return item

    def summary_rows(self, user, item):
        summary = {k: v for k, v in item.items() if k not in {"request", "conversation", "session_routing"}}
        key = f"RUN#{item['created_at']:013d}#{item['id']}"
        return [
            (self.run_key(user, item["id"]), "META", item),
            (self.user_key(user), key, summary),
            (self.user_key(user) + "#SESSION#" + item["session_id"], key, summary),
        ]

    def event(self, user, item, event):
        original = item
        item = copy.deepcopy(item)
        item["updated_at"] = int(time.time() * 1000)
        item["event_count"] = event["sequence"]
        if event.get("stage"):
            item["last_stage"] = event["stage"]
        data = event.get("data", {})
        if event["type"] == "context":
            item["context"] = data
        if event["type"] == "attempt":
            item.setdefault("attempts", []).append(data)
            item["fallback_count"] = max(item.get("fallback_count", 0), data["number"] - 1)
            if data["status"] == "started":
                item.update(model_id=data["model_id"], model=data.get("model_name", data["model_id"]),
                            configuration_id=data["configuration_id"])
        if event["type"] == "reroute":
            item["model_decision_ms"] = (item.get("model_decision_ms") or 0) + (data.get("model_decision_ms") or 0)
            item["model_decision_calls"] = (item.get("model_decision_calls") or 0) + len(data.get("decision_calls", []))
        if event.get("stage") == "classification" and event.get("status") == "completed":
            item.update(
                profile=data.get("profile"), gpu_ms=(data.get("timings") or {}).get("upstream_ms")
            )
        if event.get("stage") == "selection" and event.get("status") == "completed":
            model = data.get("selected_model") or {}
            selection = data.get("selection", {})
            calls = selection.get("decision_calls") or []
            passes = [c.get("timings", {}).get("forward_passes") for c in calls]
            item.update(
                model_id=model.get("id"),
                model=model.get("name"),
                configuration_id=data.get("choice"),
                stickiness=data.get("selection", {}).get("stickiness"),
                model_decision_ms=data.get("selection", {}).get("decision_ms"),
                model_decision_calls=len(data.get("selection", {}).get("decision_calls", [])),
                decision_mode=data.get("selection", {}).get("decision_mode"),
                decision_forward_passes=sum(passes) if passes and all(type(p) is int for p in passes) else None,
                assessment_ms=selection.get("assessment_ms"),
                selection_ms=selection.get("selection_ms"),
                model_selection_source=data.get("source"),
            )
            if selection.get("decision_mode") == "two_stage":
                upstream = [c.get("timings", {}).get("upstream_ms") for c in calls]
                item["gpu_ms"] = sum(upstream) if upstream and all(v is not None for v in upstream) else None
        if event["type"] == "route":
            item["route_ms"] = data.get("route_total_ms")
        if event.get("stage") == "execution" and event.get("status") == "completed":
            item.update(
                target_ms=data.get("backend_total_ms"),
                usage=data.get("usage"),
                reference_cost=data.get("observed_reference_cost_usd"),
                output_complete=data.get("complete"),
                stop_reason=data.get("stop_reason"),
                model_id=data.get("model_id", item.get("model_id")),
                model=data.get("model_name", item.get("model")),
                configuration_id=data.get("configuration_id", item.get("configuration_id")),
                cache=data.get("cache"),
                ttft_ms=(data.get("timings") or {}).get("ttft_ms"),
                fallback_count=data.get("fallback_count", 0),
            )
        if event["type"] == "completed":
            item.update(status=data["status"], total_ms=data.get("backend_total_ms"))
        if event["type"] == "error":
            item.update(
                status="error",
                error=data,
                failed_stage=event.get("stage"),
                total_ms=event["elapsed_ms"] if item["mode"] == "live" else None,
            )
        rows = self.summary_rows(user, item) + [
            (self.run_key(user, item["id"]), f"EVENT#{event['sequence']:06d}", event)
        ]
        if event["type"] == "completed" and item["status"] == "executed" and item.get("session_routing"):
            rows.append((self.user_key(user), "ROUTING#" + item["session_id"], item["session_routing"]))
        self.records.put_many(rows)
        original.update(item)

    def interrupted(self, user, item, stage):
        if item["status"] != "running":
            return
        item.update(
            status="interrupted",
            interrupted_stage=stage,
            execution_outcome_unknown=stage in ("execution", "unknown"),
            updated_at=int(time.time() * 1000),
        )
        self.records.put_many(self.summary_rows(user, item))

    def detail(self, user, run_id):
        item = self.records.get(self.run_key(user, run_id), "META")
        if not item:
            raise HTTPException(404, "运行记录不存在")
        if item["status"] == "running" and item["updated_at"] < time.time() * 1000 - 600_000:
            self.interrupted(user, item, item.get("last_stage", "unknown"))
        events, cursor = [], None
        while True:
            page = self.records.query(
                self.run_key(user, run_id), "EVENT#", limit=50, cursor=cursor, reverse=False
            )
            events.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        return {**item, "events": events}

    def runs(self, user, *, session_id=None, limit=50, cursor=None, days=30):
        pk = self.user_key(user)
        if session_id:
            self.session(user, session_id)
            pk += "#SESSION#" + session_id
        lower = f"RUN#{int((time.time() - days * 86400) * 1000):013d}"
        page = self.records.query(pk, "RUN#", limit=limit, cursor=cursor, lower=lower)
        for row in page["items"]:
            if row["status"] == "running" and row["updated_at"] < time.time() * 1000 - 600_000:
                item = self.records.get(self.run_key(user, row["id"]), "META")
                if item and item["status"] == "running":
                    self.interrupted(user, item, item.get("last_stage", "unknown"))
                    row.update({k: v for k, v in item.items() if k != "request"})
        return page

    def statistics(self, user, *, session_id=None, days=30):
        rows, cursor = [], None
        while len(rows) < 5000:
            page = self.runs(
                user,
                session_id=session_id,
                limit=min(200, 5000 - len(rows)),
                cursor=cursor,
                days=days,
            )
            rows.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        live = [r for r in rows if r["mode"] == "live"]
        decision_stats = {}
        for prefix, mode in (("joint_decision", "joint"), ("two_stage_decision", "two_stage"),
                             ("legacy_selection", "legacy")):
            samples = [r["model_decision_ms"] for r in live if
                       (r.get("decision_mode") == mode if mode != "legacy" else
                        r.get("decision_mode") not in {"joint", "two_stage"})
                       and r.get("model_decision_ms") is not None]
            decision_stats.update({prefix + "_p50_ms": percentile(samples, 0.5),
                                   prefix + "_p95_ms": percentile(samples, 0.95),
                                   prefix + "_samples": len(samples)})
        costs = [Decimal(r["reference_cost"]) for r in live if r.get("reference_cost") is not None]
        reviewed = [r for r in live if r.get("review")]
        groups = {}
        for r in live:
            key = r.get("model_id") or "未选出目标"
            group = groups.setdefault(
                key, {"model_id": key, "requests": 0, "completed": 0, "times": []}
            )
            group["requests"] += 1
            group["completed"] += r["status"] == "executed"
            if r.get("target_ms") is not None:
                group["times"].append(r["target_ms"])
        return {
            **decision_stats,
            "days": days,
            "scope": "selected_session" if session_id else "all_user_sessions",
            "total_records": len(rows),
            "live_requests": len(live),
            "replays": len(rows) - len(live),
            "completed": sum(r["status"] == "executed" for r in live),
            "failed": sum(
                r["status"] in ("error", "incomplete", "no_route", "interrupted") for r in live
            ),
            "running": sum(r["status"] == "running" for r in live),
            "gpu_p50_ms": percentile([r.get("gpu_ms") for r in live], 0.5),
            "model_decision_p50_ms": percentile([r.get("model_decision_ms") for r in live], 0.5),
            "model_decision_p95_ms": percentile([r.get("model_decision_ms") for r in live], 0.95),
            "model_decision_samples": sum(r.get("model_decision_ms") is not None for r in live),
            "route_p50_ms": percentile([r.get("route_ms") for r in live], 0.5),
            "route_p95_ms": percentile([r.get("route_ms") for r in live], 0.95),
            "ttft_p50_ms": percentile([r.get("ttft_ms") for r in live], 0.5),
            "context_requests": sum(bool((r.get("context") or {}).get("history_turns")) for r in live),
            "sticky_requests": sum((r.get("stickiness") or {}).get("retained", False) for r in live),
            "fallback_requests": sum(bool(r.get("fallback_count")) for r in live),
            "cache_observed_requests": sum((r.get("cache") or {}).get("observed", False) for r in live),
            "cache_hit_requests": sum((r.get("cache") or {}).get("read_tokens", 0) > 0 for r in live),
            "cache_read_tokens": sum((r.get("cache") or {}).get("read_tokens", 0) for r in live),
            "cache_write_tokens": sum((r.get("cache") or {}).get("write_tokens", 0) for r in live),
            "total_p95_ms": percentile([r.get("total_ms") for r in live], 0.95),
            "observed_reference_cost_usd": format(sum(costs), ".9f") if costs else None,
            "cost_known_samples": len(costs),
            "cost_unknown_samples": len(live) - len(costs),
            "business_reviews": len(reviewed),
            "business_passes": sum(r["review"]["passed"] for r in reviewed),
            "models": [
                {k: v for k, v in g.items() if k != "times"}
                | {
                    "target_p50_ms": percentile(g["times"], 0.5),
                    "target_p95_ms": percentile(g["times"], 0.95),
                    "latency_samples": len(g["times"]),
                }
                for g in groups.values()
            ],
            "truncated": bool(cursor),
            "note": "回放不计入性能；完整输出不代表业务通过。参考费用不含 GPU，未知费用不按零计。",
        }
