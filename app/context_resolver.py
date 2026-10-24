"""Explicit, user-isolated context resolution with persisted provenance."""
from __future__ import annotations

import hashlib
import datetime

from .db import jdumps, jloads, query, query_one, execute
from .ids import new_id
from .repositories import evidence


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def resolve_context(user_id: str, task_type: str, *,
                    source_content_id: str | None = None,
                    source_chain: list[str] | None = None) -> dict:
    refs: list[dict] = []
    content: dict = {"profile": None, "packages": [], "rules": [], "source": None,
                     "ideas": []}

    profile = query_one(
        "SELECT id,version_no,content_json FROM profile_versions"
        " WHERE user_id=? AND active=1", (user_id,))
    if profile:
        content["profile"] = jloads(profile["content_json"], {})
        refs.append({"type": "profile", "id": profile["id"],
                     "version": profile["version_no"]})

    packages = query(
        "SELECT id,type,name,version,summary FROM data_packages"
        " WHERE owner_id=? AND status='active' ORDER BY type,name", (user_id,))
    content["packages"] = packages
    refs.extend({"type": "package", "id": p["id"], "version": p["version"]}
                for p in packages)

    rules = query(
        "SELECT id,text,scope,confidence,version FROM rules"
        " WHERE user_id=? AND status='verified' ORDER BY confidence DESC LIMIT 20",
        (user_id,))
    content["rules"] = rules
    refs.extend({"type": "rule", "id": r["id"], "version": r["version"]}
                for r in rules)

    if source_content_id:
        source = query_one(
            "SELECT id,title,platform,author,url,assets_json FROM source_contents"
            " WHERE id=? AND user_id=?", (source_content_id, user_id))
        if source:
            assets = jloads(source.pop("assets_json"), {})
            transcript = ""
            for key in ("clean", "caption", "asr"):
                if (assets.get(key) or {}).get("exists"):
                    transcript = str(assets[key].get("text") or "")
                    break
            source["transcript"] = transcript
            source["assets"] = assets
            content["source"] = source
            refs.append({"type": "source_content", "id": source["id"]})

    requested = [str(item) for item in (source_chain or []) if str(item).strip()]
    if requested:
        placeholders = ",".join("?" for _ in requested)
        ideas = query(
            f"SELECT id,item_type,title,body,personal_judgement,source_kind,source_url,"
            f"media_json,tags_json,created_at,updated_at FROM content_ideas "
            f"WHERE user_id=? AND deleted_at IS NULL AND id IN ({placeholders}) "
            f"ORDER BY updated_at,id",
            (user_id, *requested),
        )
        for idea in ideas:
            idea["media"] = [
                {"id": item.get("id"), "name": item.get("name"),
                 "content_type": item.get("content_type"), "size": item.get("size")}
                for item in jloads(idea.pop("media_json"), []) if isinstance(item, dict)
            ]
            idea["tags"] = jloads(idea.pop("tags_json"), [])
            content["ideas"].append(idea)
            refs.append({"type": "content_idea", "id": idea["id"],
                         "version": idea.get("updated_at")})

    content["requested_source_chain"] = requested
    serialized = jdumps(content)
    checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    run_id = new_id("CTX")
    token_estimate = max(1, len(serialized) // 4)
    execute(
        "INSERT INTO context_runs VALUES(?,?,?,?,?,?,?,?)",
        (run_id, user_id, task_type, jdumps(refs), serialized,
         token_estimate, checksum, _now()),
    )
    evidence(user_id, "context_run", run_id, "resolved", refs=refs,
             payload={"task_type": task_type, "checksum": checksum,
                      "token_estimate": token_estimate})
    return {"run_id": run_id, "refs": refs, "content": content,
            "token_estimate": token_estimate, "checksum": checksum,
            "isolation": "仅加载当前创作者拥有的对象"}
