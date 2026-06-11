"""Task trace helpers for ownership-scoped live browser work."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from gflights.browser import CdpResult

_WORKFLOW_CREATED_PAGE_RE = re.compile(r"\bworkflow-created page ([A-Fa-f0-9]{8,})\b")


@dataclass
class TaskTrace:
    """UUID task identity with a shared Chrome target ownership map."""

    task_id: str
    root_task_id: str
    parent_task_id: str | None
    command: str
    name: str
    run_id: str
    _target_task_ids: dict[str, str] = field(default_factory=dict, repr=False)
    _task_parent_ids: dict[str, str | None] = field(default_factory=dict, repr=False)

    @classmethod
    def root(cls, *, command: str, name: str, run_id: str) -> "TaskTrace":
        task_id = _new_task_id()
        return cls(
            task_id=task_id,
            root_task_id=task_id,
            parent_task_id=None,
            command=command,
            name=name,
            run_id=run_id,
            _task_parent_ids={task_id: None},
        )

    def child(self, name: str, *, run_id: str | None = None) -> "TaskTrace":
        self._task_parent_ids.setdefault(self.task_id, self.parent_task_id)
        task_id = _new_task_id()
        self._task_parent_ids[task_id] = self.task_id
        return TaskTrace(
            task_id=task_id,
            root_task_id=self.root_task_id,
            parent_task_id=self.task_id,
            command=self.command,
            name=name,
            run_id=run_id or self.run_id,
            _target_task_ids=self._target_task_ids,
            _task_parent_ids=self._task_parent_ids,
        )

    def record_target(self, target_id: str) -> None:
        if target_id:
            self._target_task_ids[target_id] = self.task_id

    def owns_target(self, target_id: str) -> bool:
        return bool(target_id) and self._target_task_ids.get(target_id) == self.task_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "root_task_id": self.root_task_id,
            "parent_task_id": self.parent_task_id,
            "command": self.command,
            "name": self.name,
            "run_id": self.run_id,
        }

    def ownership_map(self) -> dict[str, str]:
        """Return target ownership for this trace's task subtree."""
        if self.task_id == self.root_task_id:
            return dict(self._target_task_ids)
        return {
            target_id: task_id
            for target_id, task_id in self._target_task_ids.items()
            if self._is_task_descendant(task_id, self.task_id)
        }

    def _is_task_descendant(self, task_id: str, ancestor_task_id: str) -> bool:
        while task_id:
            if task_id == ancestor_task_id:
                return True
            parent_task_id = self._task_parent_ids.get(task_id)
            if parent_task_id is None:
                return False
            task_id = parent_task_id
        return False


def new_task_trace(*, command: str, name: str, run_id: str) -> TaskTrace:
    return TaskTrace.root(command=command, name=name, run_id=run_id)


def open_result_page_id(result: CdpResult) -> str:
    return page_id_from_payload(result.json_payload) or workflow_created_page_id(
        cdp_result_message(result)
    )


def page_id_from_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    page = payload.get("page")
    if isinstance(page, dict):
        page_id = page.get("id") or page.get("targetId")
        if isinstance(page_id, str):
            return page_id
    target = payload.get("target")
    if isinstance(target, dict):
        target_id = target.get("id") or target.get("targetId")
        if isinstance(target_id, str):
            return target_id
    for key in ("id", "page_id", "targetId"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def recoverable_open_page_warning(result: CdpResult, page_id: str) -> str:
    if result.status != "tool_error" or not page_id:
        return ""
    if not workflow_created_page_id(cdp_result_message(result)):
        return ""
    return (
        "cdp open reported a workflow-created-page recording error after creating "
        f"target {page_id}; continuing with recovered target evidence"
    )


def cdp_result_message(result: CdpResult) -> str:
    payload = result.json_payload or {}
    return " ".join(
        str(value)
        for value in [
            result.error,
            result.stderr,
            payload.get("message"),
            payload.get("error"),
        ]
        if value
    )


def workflow_created_page_id(text: str) -> str:
    match = _WORKFLOW_CREATED_PAGE_RE.search(text)
    return match.group(1) if match else ""


def _new_task_id() -> str:
    return str(uuid4())
