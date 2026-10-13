"""启动恢复测试：进程中断后遗留的运行记录不会永久锁住对话。"""

from __future__ import annotations

from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent.main import create_app

RUN_ID = "77777777-7777-7777-7777-777777777777"


async def test_interrupted_run_is_recovered_on_startup(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}"

    first_app = create_app(build_test_settings(database_url=database_url))
    repositories = first_app.state.repositories
    conversation_id = repositories.create_conversation(title="中断的对话").id
    repositories.create_run(
        run_id=RUN_ID,
        conversation_id=conversation_id,
        model_name="test-model",
        prompt_version="v1",
    )
    assert repositories.get_active_run(conversation_id) is not None

    # 模拟进程中断后重启：不调用 finish_run，直接构建新的应用实例
    second_app = create_app(build_test_settings(database_url=database_url))
    recovered_repositories = second_app.state.repositories
    assert recovered_repositories.get_active_run(conversation_id) is None
    run = recovered_repositories.get_run(RUN_ID)
    assert run.status == "failed"
    assert run.error_code == "run_interrupted"
    assert run.error_category == "internal"

    # 锁已释放：原本会被 409 拒绝的删除操作现在可以执行
    async with running_client(second_app) as client:
        response = await client.delete(f"/api/v1/conversations/{conversation_id}")
    assert response.status_code == 204
