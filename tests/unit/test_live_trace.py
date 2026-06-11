from gflights.live_trace import new_task_trace


def test_task_trace_ownership_map_is_scoped_to_task_subtree() -> None:
    root = new_task_trace(command="gflights.test", name="root", run_id="run")
    branch_a = root.child("branch-a")
    branch_b = root.child("branch-b")
    branch_a_child = branch_a.child("managed-tab")
    branch_b_child = branch_b.child("managed-tab")

    branch_a_child.record_target("TARGET_A")
    branch_b_child.record_target("TARGET_B")

    assert root.ownership_map() == {
        "TARGET_A": branch_a_child.task_id,
        "TARGET_B": branch_b_child.task_id,
    }
    assert branch_a.ownership_map() == {"TARGET_A": branch_a_child.task_id}
    assert branch_b.ownership_map() == {"TARGET_B": branch_b_child.task_id}
    assert branch_a_child.owns_target("TARGET_A")
    assert not branch_a_child.owns_target("TARGET_B")
