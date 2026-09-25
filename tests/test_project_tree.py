import project_tree
from app import app


def test_project_tree_returns_bounded_structured_nodes():
    client = app.test_client()
    response = client.get("/api/project-tree")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data["nodes"], list)
    if data["nodes"]:
        node = data["nodes"][0]
        assert {"name", "path", "kind", "icon"} <= set(node)
